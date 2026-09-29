from decimal import Decimal, InvalidOperation

from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import F
from django.utils import timezone

from apps.audit.models import AuditLog
from apps.customers.models import Customer, CustomerPayment
from apps.ledger.models import LedgerEntry


@transaction.atomic
def collect_customer_payment(*, customer: Customer, amount, mode: str, date=None, note: str = "", user) -> CustomerPayment:
    try:
        value = Decimal(str(amount))
    except (InvalidOperation, TypeError, ValueError):
        raise ValidationError("Enter a valid collection amount.") from None
    if not value.is_finite() or value <= 0:
        raise ValidationError("Collection amount must be greater than zero.")
    customer.refresh_from_db(fields=["credit_balance"])
    if value > customer.credit_balance:
        raise ValidationError("Collection cannot exceed the customer's outstanding balance.")
    paid_date = date or timezone.localdate()
    payment = CustomerPayment.objects.create(
        customer=customer, amount=value, mode=mode, date=paid_date, note=note.strip(),
    )
    Customer.objects.filter(pk=customer.pk).update(credit_balance=F("credit_balance") - value)
    cash_account = LedgerEntry.AccountType.CASH if mode == CustomerPayment.Mode.CASH else LedgerEntry.AccountType.BANK
    LedgerEntry.objects.create(date=paid_date, account_type=cash_account, ref_type="CUSTOMER_PAYMENT",
                               ref_id=str(payment.pk), debit=value, description=f"Collection from {customer.name}")
    LedgerEntry.objects.create(date=paid_date, account_type=LedgerEntry.AccountType.CUSTOMER,
                               ref_type="CUSTOMER_PAYMENT", ref_id=str(payment.pk), credit=value,
                               description=f"Customer balance collected: {customer.name}")
    AuditLog.objects.create(user=user, action="customer_payment", object_type="CustomerPayment", object_id=str(payment.pk))
    return payment