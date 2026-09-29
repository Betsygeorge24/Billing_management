from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from uuid import uuid4

from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import F, Sum
from django.utils import timezone

from apps.audit.models import AuditLog
from apps.billing.models import Bill
from apps.catalog.models import ProductVariant
from apps.customers.models import Customer
from apps.inventory.models import StockMovement
from apps.ledger.models import LedgerEntry
from apps.sales_returns.models import SaleReturn, SaleReturnItem


@transaction.atomic
def process_return(*, bill: Bill, items: list[dict], reason: str, refund_mode: str, user) -> SaleReturn:
    bill = Bill.objects.select_related("customer").get(pk=bill.pk)
    if bill.status in {Bill.Status.HELD, Bill.Status.VOID}:
        raise ValidationError("Held or void bills cannot be returned.")
    if not reason.strip() or not items:
        raise ValidationError("A return reason and at least one item are required.")
    if refund_mode not in {"CASH", "UPI", "CREDIT"}:
        raise ValidationError("Choose a valid refund method.")
    prepared = []
    refund_total = Decimal("0")
    for selection in items:
        bill_item = bill.items.select_related("variant").get(pk=selection.get("bill_item_id"))
        try:
            qty = Decimal(str(selection.get("qty")))
        except (InvalidOperation, TypeError, ValueError):
            raise ValidationError("Return quantity must be a valid number.") from None
        if not qty.is_finite() or qty <= 0:
            raise ValidationError("Return quantity must be greater than zero.")
        previously_returned = bill_item.return_items.aggregate(total=Sum("qty"))["total"] or Decimal("0")
        if previously_returned + qty > bill_item.qty:
            raise ValidationError(f"Return quantity exceeds the remaining quantity for {bill_item.name_snapshot}.")
        line_amount = (bill_item.line_total * qty / bill_item.qty).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        prepared.append((bill_item, qty, line_amount, bool(selection.get("restock", True))))
        refund_total += line_amount
    refund_total = refund_total.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    prior_refunds = bill.returns.aggregate(total=Sum("refund_amount"))["total"] or Decimal("0")
    received = bill.paid_amount
    if prior_refunds + refund_total > received:
        raise ValidationError("Refund cannot exceed the amount already received for this bill.")
    if refund_mode == "CREDIT" and bill.customer_id is None:
        raise ValidationError("Customer credit refund requires a registered customer.")

    sale_return = SaleReturn.objects.create(
        return_no=f"RET-{timezone.localdate():%Y}-{uuid4().hex[:8].upper()}",
        bill=bill, staff=user, reason=reason.strip(), refund_mode=refund_mode,
        refund_amount=refund_total,
    )
    for bill_item, qty, amount, restock in prepared:
        SaleReturnItem.objects.create(sale_return=sale_return, bill_item=bill_item, qty=qty, amount=amount, restock=restock)
        ProductVariant.objects.filter(pk=bill_item.variant_id).update(
            stock_qty=F("stock_qty") + (qty if restock else Decimal("0"))
        )
        StockMovement.objects.create(
            variant=bill_item.variant,
            type=StockMovement.MovementType.RETURN if restock else StockMovement.MovementType.DAMAGE,
            qty=qty if restock else Decimal("0"),
            ref_type="RETURN", ref_id=str(sale_return.pk),
            note=(f"{sale_return.return_no}: {reason.strip()}" if restock else f"Damaged return qty {qty}; not restocked. {reason.strip()}"), user=user,
        )
    if refund_mode == "CREDIT":
        Customer.objects.filter(pk=bill.customer_id).update(credit_balance=F("credit_balance") + refund_total)
        credit_account = LedgerEntry.AccountType.CUSTOMER
    elif refund_mode == "CASH":
        credit_account = LedgerEntry.AccountType.CASH
    else:
        credit_account = LedgerEntry.AccountType.BANK
    LedgerEntry.objects.create(
        date=timezone.localdate(), account_type=LedgerEntry.AccountType.RETURN,
        ref_type="RETURN", ref_id=str(sale_return.pk), debit=refund_total,
        description=f"Return {sale_return.return_no} for {bill.bill_no}",
    )
    LedgerEntry.objects.create(
        date=timezone.localdate(), account_type=credit_account,
        ref_type="RETURN", ref_id=str(sale_return.pk), credit=refund_total,
        description=f"{refund_mode.title()} refund {sale_return.return_no}",
    )
    returned_qty = sum((item.return_items.aggregate(total=Sum("qty"))["total"] or Decimal("0") for item in bill.items.all()), Decimal("0"))
    sold_qty = sum((item.qty for item in bill.items.all()), Decimal("0"))
    bill.status = Bill.Status.RETURNED if returned_qty >= sold_qty else Bill.Status.PARTIALLY_RETURNED
    bill.save(update_fields=["status", "updated_at"])
    AuditLog.objects.create(
        user=user, action="sale_return", object_type="SaleReturn", object_id=str(sale_return.pk),
        after_data={"return_no": sale_return.return_no, "refund_amount": str(refund_total), "bill_no": bill.bill_no},
    )
    return sale_return