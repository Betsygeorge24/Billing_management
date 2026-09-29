from __future__ import annotations

from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Any

from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import F
from django.utils import timezone

from apps.audit.models import AuditLog
from apps.billing.models import Bill, BillItem, HeldBill, NumberCounter, Payment
from apps.catalog.models import ProductVariant
from apps.inventory.models import StockMovement
from apps.ledger.models import LedgerEntry
from apps.shop_settings.models import ShopSettings


CENT = Decimal("0.01")
ZERO = Decimal("0")
ONE = Decimal("1")
HUNDRED = Decimal("100")


def money(value: Decimal) -> Decimal:
    return value.quantize(CENT, rounding=ROUND_HALF_UP)


def decimal_value(value: Any, label: str, *, default: Decimal | None = None) -> Decimal:
    if value is None and default is not None:
        return default
    try:
        result = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        raise ValidationError(f"{label} must be a valid number.") from None
    if not result.is_finite():
        raise ValidationError(f"{label} must be a finite number.")
    return result


def calculate_totals(
    items: list[dict[str, Any]],
    *,
    discount_type: str = Bill.DiscountType.AMOUNT,
    discount_value: Decimal = ZERO,
    tax_inclusive: bool = False,
    round_total: bool = False,
    max_discount_percent: Decimal | None = None,
    can_discount: bool = True,
) -> dict[str, Any]:
    """Calculate totals from already-authoritative product prices and item inputs."""
    if not items:
        raise ValidationError("Add at least one item before checkout.")

    prepared: list[dict[str, Any]] = []
    subtotal = ZERO
    item_discount_total = ZERO
    for item in items:
        qty = decimal_value(item.get("qty"), "Quantity")
        unit_price = decimal_value(item.get("unit_price"), "Price")
        tax_percent = decimal_value(item.get("tax_percent"), "Tax rate", default=ZERO)
        item_discount = decimal_value(item.get("discount"), "Item discount", default=ZERO)
        if qty <= ZERO or qty > Decimal("1000000"):
            raise ValidationError("Quantity must be greater than zero and within the supported limit.")
        if unit_price < ZERO or tax_percent < ZERO or tax_percent > Decimal("100"):
            raise ValidationError("Price or tax rate is outside the supported range.")
        raw_amount = qty * unit_price
        if item_discount < ZERO or item_discount > raw_amount:
            raise ValidationError("An item discount cannot exceed that item's value.")

        rate = tax_percent / HUNDRED
        divisor = ONE + rate if tax_inclusive else ONE
        raw_base = raw_amount / divisor
        discount_base = item_discount / divisor
        base_after_item_discount = raw_base - discount_base
        subtotal += raw_base
        item_discount_total += discount_base
        prepared.append({
            **item,
            "qty": qty,
            "unit_price": unit_price,
            "tax_percent": tax_percent,
            "item_discount_base": discount_base,
            "base_after_item_discount": base_after_item_discount,
        })

    after_item_discounts = sum((row["base_after_item_discount"] for row in prepared), ZERO)
    discount_value = decimal_value(discount_value, "Bill discount", default=ZERO)
    if discount_value < ZERO:
        raise ValidationError("Bill discount cannot be negative.")
    if discount_type == Bill.DiscountType.PERCENT:
        if discount_value > HUNDRED:
            raise ValidationError("Percentage discount cannot exceed 100%.")
        bill_discount = after_item_discounts * discount_value / HUNDRED
    elif discount_type == Bill.DiscountType.AMOUNT:
        bill_discount = discount_value
    else:
        raise ValidationError("Choose a valid bill discount type.")
    if bill_discount > after_item_discounts:
        raise ValidationError("Bill discount cannot exceed the remaining item value.")

    total_discount = item_discount_total + bill_discount
    if total_discount and not can_discount:
        raise ValidationError("Your account is not permitted to give discounts.")
    if max_discount_percent is not None and subtotal:
        allowed_discount = subtotal * max_discount_percent / HUNDRED
        if total_discount > allowed_discount:
            raise ValidationError(
                f"Discount exceeds your maximum of {max_discount_percent}% "
                f"(₹{money(allowed_discount):.2f} on this bill)."
            )

    line_results: list[dict[str, Any]] = []
    tax_total = ZERO
    allocated_bill_discount = ZERO
    for index, row in enumerate(prepared):
        remaining = row["base_after_item_discount"]
        if index == len(prepared) - 1:
            line_bill_discount = bill_discount - allocated_bill_discount
        elif after_item_discounts:
            line_bill_discount = bill_discount * remaining / after_item_discounts
            allocated_bill_discount += line_bill_discount
        else:
            line_bill_discount = ZERO
        taxable_base = remaining - line_bill_discount
        line_tax = money(taxable_base * row["tax_percent"] / HUNDRED)
        rounded_base = money(taxable_base)
        line_total = money(rounded_base + line_tax)
        tax_total += line_tax
        line_results.append({
            **row,
            "discount": money(row["item_discount_base"] + line_bill_discount),
            "tax_amount": line_tax,
            "taxable_base": rounded_base,
            "line_total": line_total,
        })

    subtotal_amount = money(subtotal)
    discount_amount = money(total_discount)
    pre_round_total = money(subtotal_amount - discount_amount + tax_total)
    round_off = (pre_round_total.quantize(Decimal("1"), rounding=ROUND_HALF_UP) - pre_round_total) if round_total else ZERO
    grand_total = money(pre_round_total + round_off)
    return {
        "lines": line_results,
        "subtotal": subtotal_amount,
        "discount_amount": discount_amount,
        "tax_amount": money(tax_total),
        "round_off": money(round_off),
        "grand_total": grand_total,
        "tax_inclusive": tax_inclusive,
    }


def _bill_number() -> str:
    year = timezone.localdate().year
    counter_name = f"bill-{year}"
    counter, _ = NumberCounter.objects.get_or_create(name=counter_name)
    NumberCounter.objects.filter(pk=counter.pk).update(value=F("value") + 1)
    counter.refresh_from_db(fields=["value"])
    prefix = ShopSettings.get_solo().invoice_prefix.strip() or "INV"
    return f"{prefix}-{year}-{counter.value:06d}"


def _payment_plan(payments: list[dict[str, Any]], grand_total: Decimal, customer, user) -> tuple[list[dict[str, Any]], Decimal, Decimal, Decimal]:
    if not isinstance(payments, list) or not payments:
        raise ValidationError("Add at least one payment method.")
    normalized: list[dict[str, Any]] = []
    cash_received = ZERO
    non_cash_total = ZERO
    credit_total = ZERO
    valid_modes = {mode for mode, _ in Payment.Mode.choices}
    for payment in payments:
        mode = str(payment.get("mode", "")).upper()
        amount = decimal_value(payment.get("amount"), "Payment amount")
        if mode not in valid_modes or amount <= ZERO:
            raise ValidationError("Each payment needs a valid method and an amount greater than zero.")
        if mode == Payment.Mode.CASH:
            cash_received += amount
        elif mode == Payment.Mode.CREDIT:
            credit_total += amount
            if customer is None:
                raise ValidationError("Customer credit requires a registered customer.")
        else:
            non_cash_total += amount
        normalized.append({"mode": mode, "amount": amount, "reference": str(payment.get("reference", ""))[:100]})

    if credit_total:
        if user.is_admin is False and not user.permissions.get("can_use_customer_credit", True):
            raise ValidationError("Your account is not permitted to use customer credit.")
        if credit_total != grand_total - non_cash_total - min(cash_received, grand_total - non_cash_total):
            raise ValidationError("Customer credit must exactly cover the remaining balance.")
    required_cash = grand_total - non_cash_total - credit_total
    if required_cash < ZERO:
        raise ValidationError("Payments cannot exceed the bill total, except for cash change.")
    if cash_received < required_cash:
        raise ValidationError("Payments do not cover the bill total.")
    retained_cash = required_cash
    change_due = cash_received - retained_cash

    effective: list[dict[str, Any]] = []
    cash_remaining = retained_cash
    for row in normalized:
        if row["mode"] == Payment.Mode.CASH:
            applied = min(row["amount"], cash_remaining)
            cash_remaining -= applied
            if applied:
                effective.append({**row, "amount": applied})
        else:
            effective.append(row)
    return effective, money(grand_total - credit_total), money(credit_total), money(change_due)


@transaction.atomic
def checkout(*, user, cart: list[dict[str, Any]], payments: list[dict[str, Any]], customer=None,
             discount_type: str = Bill.DiscountType.AMOUNT, discount_value: Any = ZERO,
             tax_inclusive: bool = False, round_total: bool = False, held_bill_id: int | None = None) -> dict[str, Any]:
    if not isinstance(cart, list) or len(cart) > 100:
        raise ValidationError("Cart must contain between one and 100 items.")
    try:
        variant_ids = {int(item["variant_id"]) for item in cart}
    except (KeyError, TypeError, ValueError):
        raise ValidationError("Each cart item needs a valid product variant.") from None
    variants = ProductVariant.objects.select_related("product").filter(
        id__in=variant_ids, product__is_active=True, product__category__is_active=True
    )
    variant_map = {variant.pk: variant for variant in variants}
    if len(variant_map) != len(variant_ids):
        raise ValidationError("One or more products are no longer available.")

    calc_items: list[dict[str, Any]] = []
    variant_by_line: list[ProductVariant] = []
    for item in cart:
        variant = variant_map[int(item["variant_id"])]
        unit_price = variant.price_override if variant.price_override is not None else variant.product.selling_price
        calc_items.append({
            "qty": item.get("qty"),
            "unit_price": unit_price,
            "tax_percent": variant.product.tax_percent,
            "discount": item.get("discount", ZERO),
        })
        variant_by_line.append(variant)

    is_admin = user.is_admin
    permissions = user.permissions if isinstance(user.permissions, dict) else {}
    can_discount = is_admin or bool(permissions.get("can_give_discount", False))
    max_discount_percent = None if is_admin else decimal_value(
        permissions.get("max_discount_percent", ZERO), "Maximum discount", default=ZERO
    )
    totals = calculate_totals(
        calc_items,
        discount_type=discount_type,
        discount_value=decimal_value(discount_value, "Bill discount", default=ZERO),
        tax_inclusive=tax_inclusive,
        round_total=round_total,
        max_discount_percent=max_discount_percent,
        can_discount=can_discount,
    )
    payment_rows, paid_amount, balance, change_due = _payment_plan(payments, totals["grand_total"], customer, user)

    shop = ShopSettings.get_solo()
    for line, variant in zip(totals["lines"], variant_by_line, strict=True):
        if not shop.allow_negative_stock:
            updated = ProductVariant.objects.filter(pk=variant.pk, stock_qty__gte=line["qty"]).update(
                stock_qty=F("stock_qty") - line["qty"]
            )
            if not updated:
                raise ValidationError(f"Not enough stock for {variant}.")
        else:
            ProductVariant.objects.filter(pk=variant.pk).update(stock_qty=F("stock_qty") - line["qty"])

    bill = Bill.objects.create(
        bill_no=_bill_number(), customer=customer, staff=user, subtotal=totals["subtotal"],
        discount_type=discount_type, discount_value=decimal_value(discount_value, "Bill discount", default=ZERO),
        discount_amount=totals["discount_amount"], tax_amount=totals["tax_amount"], round_off=totals["round_off"],
        tax_inclusive=totals["tax_inclusive"], grand_total=totals["grand_total"], paid_amount=paid_amount, balance=balance,
    )
    item_objects: list[BillItem] = []
    for line, variant in zip(totals["lines"], variant_by_line, strict=True):
        item_objects.append(BillItem(
            bill=bill,
            product=variant.product,
            variant=variant,
            name_snapshot=variant.product.name + (f" ({variant.size})" if variant.size else "") + (f" / {variant.color}" if variant.color else ""),
            sku_snapshot=variant.sku,
            qty=line["qty"],
            unit_price=line["unit_price"],
            discount=line["discount"],
            tax_percent=line["tax_percent"],
            tax_amount=line["tax_amount"],
            line_total=line["line_total"],
        ))
    BillItem.objects.bulk_create(item_objects)

    for line, variant in zip(totals["lines"], variant_by_line, strict=True):
        StockMovement.objects.create(
            variant=variant, type=StockMovement.MovementType.SALE, qty=-line["qty"],
            ref_type="BILL", ref_id=str(bill.pk), note=f"Sale {bill.bill_no}", user=user,
        )

    LedgerEntry.objects.create(
        date=timezone.localdate(), account_type=LedgerEntry.AccountType.SALES,
        ref_type="BILL", ref_id=str(bill.pk), credit=bill.grand_total,
        description=f"Sale {bill.bill_no}",
    )
    for row in payment_rows:
        Payment.objects.create(bill=bill, **row)
        account = {
            Payment.Mode.CASH: LedgerEntry.AccountType.CASH,
            Payment.Mode.CARD: LedgerEntry.AccountType.BANK,
            Payment.Mode.UPI: LedgerEntry.AccountType.BANK,
            Payment.Mode.CREDIT: LedgerEntry.AccountType.CUSTOMER,
        }[row["mode"]]
        LedgerEntry.objects.create(
            date=timezone.localdate(), account_type=account, ref_type="BILL", ref_id=str(bill.pk),
            debit=row["amount"], description=f"{row['mode'].title()} payment for {bill.bill_no}",
        )
    if balance and customer is not None:
        type(customer).objects.filter(pk=customer.pk).update(credit_balance=F("credit_balance") + balance)
    if customer is not None:
        customer.refresh_from_db(fields=["credit_balance"])
    if held_bill_id is not None:
        HeldBill.objects.filter(pk=held_bill_id, staff=user).delete()

    AuditLog.objects.create(
        user=user, action="bill_created", object_type="Bill", object_id=str(bill.pk),
        after_data={"bill_no": bill.bill_no, "grand_total": str(bill.grand_total), "discount": str(bill.discount_amount)},
    )
    return {"bill": bill, "change_due": change_due}


@transaction.atomic
def hold_bill(*, user, cart: list[dict[str, Any]], customer=None, label: str = "") -> HeldBill:
    if not isinstance(cart, list) or not cart:
        raise ValidationError("Add at least one item before holding a bill.")
    if len(cart) > 100:
        raise ValidationError("A held bill cannot contain more than 100 items.")
    for item in cart:
        try:
            item["variant_id"] = int(item["variant_id"])
            quantity = decimal_value(item["qty"], "Quantity")
        except (KeyError, TypeError, ValueError):
            raise ValidationError("Held cart items require a variant and quantity.") from None
        if quantity <= ZERO:
            raise ValidationError("Held cart quantities must be greater than zero.")
    return HeldBill.objects.create(staff=user, customer=customer, label=label[:120], cart_data={"items": cart})


@transaction.atomic
def void_bill(*, bill: Bill, reason: str, user) -> Bill:
    bill = Bill.objects.select_related("customer").get(pk=bill.pk)
    if bill.status in {Bill.Status.VOID, Bill.Status.HELD}:
        raise ValidationError("This bill cannot be voided in its current status.")
    if bill.returns.exists():
        raise ValidationError("A bill with returns cannot be voided; process a return for the remaining items.")
    if not reason.strip():
        raise ValidationError("A reason is required to void a bill.")
    original_entries = list(LedgerEntry.objects.filter(ref_type="BILL", ref_id=str(bill.pk)))
    for item in bill.items.select_related("variant"):
        ProductVariant.objects.filter(pk=item.variant_id).update(stock_qty=F("stock_qty") + item.qty)
        StockMovement.objects.create(
            variant=item.variant, type=StockMovement.MovementType.VOID, qty=item.qty,
            ref_type="BILL_VOID", ref_id=str(bill.pk), note=reason.strip(), user=user,
        )
    for entry in original_entries:
        LedgerEntry.objects.create(
            date=timezone.localdate(), account_type=entry.account_type,
            ref_type="BILL_VOID", ref_id=str(bill.pk), debit=entry.credit, credit=entry.debit,
            description=f"Void reversal for {bill.bill_no}: {reason.strip()}",
        )
    if bill.customer_id and bill.balance:
        type(bill.customer).objects.filter(pk=bill.customer_id).update(credit_balance=F("credit_balance") - bill.balance)
    bill.status = Bill.Status.VOID
    bill.balance = Decimal("0")
    bill.save(update_fields=["status", "balance", "updated_at"])
    AuditLog.objects.create(
        user=user, action="bill_void", object_type="Bill", object_id=str(bill.pk),
        before_data={"status": Bill.Status.COMPLETED}, after_data={"status": Bill.Status.VOID, "reason": reason.strip()},
    )
    return bill


@transaction.atomic
def collect_bill_balance(*, bill: Bill, amount, mode: str, reference: str = "", user) -> Payment:
    bill = Bill.objects.select_related("customer").get(pk=bill.pk)
    if bill.status in {Bill.Status.VOID, Bill.Status.HELD} or bill.balance <= 0:
        raise ValidationError("This bill has no collectible balance.")
    value = decimal_value(amount, "Collection amount")
    if value <= 0 or value > bill.balance:
        raise ValidationError("Collection must be greater than zero and no more than the bill balance.")
    if mode not in {Payment.Mode.CASH, Payment.Mode.CARD, Payment.Mode.UPI}:
        raise ValidationError("Choose cash, card, or UPI for a balance collection.")
    payment = Payment.objects.create(bill=bill, mode=mode, amount=value, reference=reference[:100])
    bill.paid_amount += value
    bill.balance -= value
    bill.save(update_fields=["paid_amount", "balance", "updated_at"])
    if bill.customer_id:
        type(bill.customer).objects.filter(pk=bill.customer_id).update(credit_balance=F("credit_balance") - value)
    account = LedgerEntry.AccountType.CASH if mode == Payment.Mode.CASH else LedgerEntry.AccountType.BANK
    LedgerEntry.objects.create(
        date=timezone.localdate(), account_type=account, ref_type="BILL_BALANCE", ref_id=str(bill.pk),
        debit=value, description=f"{mode.title()} balance collection for {bill.bill_no}",
    )
    if bill.customer_id:
        LedgerEntry.objects.create(
            date=timezone.localdate(), account_type=LedgerEntry.AccountType.CUSTOMER,
            ref_type="BILL_BALANCE", ref_id=str(bill.pk), credit=value,
            description=f"Customer balance collected for {bill.bill_no}",
        )
    AuditLog.objects.create(user=user, action="bill_balance_collected", object_type="Bill", object_id=str(bill.pk),
                            after_data={"amount": str(value), "mode": mode})
    return payment