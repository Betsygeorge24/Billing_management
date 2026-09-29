from django.db import transaction

from apps.audit.models import AuditLog
from apps.ledger.models import Expense, LedgerEntry


@transaction.atomic
def record_expense(*, category: str, amount, date, note: str, mode: str, user) -> Expense:
    expense = Expense.objects.create(category=category.strip(), amount=amount, date=date, note=note.strip(), mode=mode)
    account = LedgerEntry.AccountType.CASH if mode == "CASH" else LedgerEntry.AccountType.BANK
    LedgerEntry.objects.create(
        date=date, account_type=LedgerEntry.AccountType.EXPENSE, ref_type="EXPENSE",
        ref_id=str(expense.pk), debit=expense.amount, description=f"{expense.category}: {expense.note}"[:240],
    )
    LedgerEntry.objects.create(
        date=date, account_type=account, ref_type="EXPENSE", ref_id=str(expense.pk),
        credit=expense.amount, description=f"{mode.title()} expense: {expense.category}"[:240],
    )
    AuditLog.objects.create(user=user, action="expense_created", object_type="Expense", object_id=str(expense.pk))
    return expense