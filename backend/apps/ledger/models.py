from django.db import models

from apps.core.models import TimeStampedModel


class LedgerEntry(TimeStampedModel):
    class AccountType(models.TextChoices):
        CASH = "CASH", "Cash"
        BANK = "BANK", "Bank"
        SALES = "SALES", "Sales"
        PURCHASE = "PURCHASE", "Purchase"
        CUSTOMER = "CUSTOMER", "Customer"
        SUPPLIER = "SUPPLIER", "Supplier"
        EXPENSE = "EXPENSE", "Expense"
        RETURN = "RETURN", "Return"

    date = models.DateField(db_index=True)
    account_type = models.CharField(max_length=12, choices=AccountType.choices, db_index=True)
    ref_type = models.CharField(max_length=40, blank=True)
    ref_id = models.CharField(max_length=64, blank=True)
    debit = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    credit = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    description = models.CharField(max_length=240, blank=True)

    class Meta:
        ordering = ["date", "id"]
        indexes = [models.Index(fields=["account_type", "date"]), models.Index(fields=["ref_type", "ref_id"])]


class Expense(TimeStampedModel):
    category = models.CharField(max_length=100, db_index=True)
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    date = models.DateField(db_index=True)
    note = models.TextField(blank=True)
    mode = models.CharField(max_length=10, choices=[("CASH", "Cash"), ("CARD", "Card"), ("UPI", "UPI")], default="CASH")

    class Meta:
        ordering = ["-date", "-id"]