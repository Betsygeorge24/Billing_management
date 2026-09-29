from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import F, Sum

from apps.audit.models import AuditLog
from apps.catalog.models import ProductVariant
from apps.inventory.models import StockMovement
from apps.ledger.models import LedgerEntry
from apps.suppliers.models import Purchase, PurchaseItem, Supplier, SupplierPayment


CENT = Decimal("0.01")


def _amount(value, label):
    try:
        number = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        raise ValidationError(f"{label} must be a valid amount.") from None
    if not number.is_finite() or number < 0:
        raise ValidationError(f"{label} cannot be negative.")
    return number


def supplier_balance(supplier: Supplier) -> Decimal:
    purchases = supplier.purchases.filter(status=Purchase.Status.RECEIVED).aggregate(total=Sum("total"))["total"] or Decimal("0")
    paid = supplier.purchases.filter(status=Purchase.Status.RECEIVED).aggregate(total=Sum("paid_amount"))["total"] or Decimal("0")
    payments = supplier.payments.aggregate(total=Sum("amount"))["total"] or Decimal("0")
    return supplier.opening_balance + purchases - paid - payments


@transaction.atomic
def receive_purchase(*, supplier: Supplier, invoice_no: str, purchase_date, items: list[dict],
                     paid_amount=Decimal("0"), user) -> Purchase:
    if not invoice_no.strip() or not items:
        raise ValidationError("Supplier invoice number and at least one item are required.")
    prepared = []
    subtotal = Decimal("0")
    tax_total = Decimal("0")
    for item in items:
        variant = ProductVariant.objects.select_related("product").get(pk=item.get("variant_id"))
        qty = _amount(item.get("qty"), "Quantity")
        unit_cost = _amount(item.get("unit_cost"), "Unit cost")
        tax_percent = _amount(item.get("tax_percent", variant.product.tax_percent), "Tax rate")
        if qty <= 0 or tax_percent > 100:
            raise ValidationError("Purchase quantities must be positive and tax cannot exceed 100%.")
        line_subtotal = qty * unit_cost
        line_tax = line_subtotal * tax_percent / Decimal("100")
        line_total = (line_subtotal + line_tax).quantize(CENT, rounding=ROUND_HALF_UP)
        subtotal += line_subtotal
        tax_total += line_tax
        prepared.append((variant, qty, unit_cost, tax_percent, line_total))
    subtotal = subtotal.quantize(CENT, rounding=ROUND_HALF_UP)
    tax_total = tax_total.quantize(CENT, rounding=ROUND_HALF_UP)
    total = subtotal + tax_total
    paid = _amount(paid_amount, "Paid amount")
    if paid > total:
        raise ValidationError("Paid amount cannot exceed the purchase total.")
    purchase = Purchase.objects.create(
        supplier=supplier, invoice_no=invoice_no.strip(), date=purchase_date,
        subtotal=subtotal, tax=tax_total, total=total, paid_amount=paid,
    )
    for variant, qty, unit_cost, tax_percent, line_total in prepared:
        PurchaseItem.objects.create(
            purchase=purchase, product=variant.product, variant=variant,
            qty=qty, unit_cost=unit_cost, tax_percent=tax_percent, line_total=line_total,
        )
        ProductVariant.objects.filter(pk=variant.pk).update(stock_qty=F("stock_qty") + qty)
        StockMovement.objects.create(
            variant=variant, type=StockMovement.MovementType.PURCHASE, qty=qty,
            ref_type="PURCHASE", ref_id=str(purchase.pk), note=f"Supplier invoice {purchase.invoice_no}", user=user,
        )
    LedgerEntry.objects.create(
        date=purchase.date, account_type=LedgerEntry.AccountType.PURCHASE,
        ref_type="PURCHASE", ref_id=str(purchase.pk), debit=total,
        description=f"Purchase {purchase.invoice_no}",
    )
    LedgerEntry.objects.create(
        date=purchase.date, account_type=LedgerEntry.AccountType.SUPPLIER,
        ref_type="PURCHASE", ref_id=str(purchase.pk), credit=total,
        description=f"Amount payable: {purchase.invoice_no}",
    )
    if paid:
        LedgerEntry.objects.create(
            date=purchase.date, account_type=LedgerEntry.AccountType.SUPPLIER,
            ref_type="PURCHASE_PAYMENT", ref_id=str(purchase.pk), debit=paid,
            description=f"Paid on purchase {purchase.invoice_no}",
        )
        LedgerEntry.objects.create(
            date=purchase.date, account_type=LedgerEntry.AccountType.CASH,
            ref_type="PURCHASE_PAYMENT", ref_id=str(purchase.pk), credit=paid,
            description=f"Purchase payment {purchase.invoice_no}",
        )
    AuditLog.objects.create(user=user, action="purchase_received", object_type="Purchase", object_id=str(purchase.pk))
    return purchase


@transaction.atomic
def collect_supplier_payment(*, supplier: Supplier, amount, mode: str, date, note: str = "", user) -> SupplierPayment:
    value = _amount(amount, "Payment")
    if value <= 0:
        raise ValidationError("Payment must be greater than zero.")
    if value > supplier_balance(supplier):
        raise ValidationError("Payment cannot exceed the supplier's outstanding balance.")
    payment = SupplierPayment.objects.create(supplier=supplier, amount=value, mode=mode, date=date, note=note.strip())
    cash_account = LedgerEntry.AccountType.CASH if mode == SupplierPayment.Mode.CASH else LedgerEntry.AccountType.BANK
    LedgerEntry.objects.create(date=date, account_type=LedgerEntry.AccountType.SUPPLIER,
                               ref_type="SUPPLIER_PAYMENT", ref_id=str(payment.pk), debit=value,
                               description=f"Payment to {supplier.name}")
    LedgerEntry.objects.create(date=date, account_type=cash_account, ref_type="SUPPLIER_PAYMENT",
                               ref_id=str(payment.pk), credit=value, description=f"Supplier payment: {supplier.name}")
    AuditLog.objects.create(user=user, action="supplier_payment", object_type="SupplierPayment", object_id=str(payment.pk))
    return payment