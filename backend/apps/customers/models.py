from django.core.validators import MinValueValidator
from django.db import models

from apps.core.models import TimeStampedModel


class Customer(TimeStampedModel):
    name = models.CharField(max_length=180, db_index=True)
    phone = models.CharField(max_length=24, blank=True, db_index=True)
    address = models.TextField(blank=True)
    gstin = models.CharField(max_length=20, blank=True)
    credit_balance = models.DecimalField(max_digits=12, decimal_places=2, default=0, validators=[MinValueValidator(0)])
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["name"]

    def __str__(self) -> str:
        return f"{self.name} ({self.phone})" if self.phone else self.name


class CustomerPayment(TimeStampedModel):
    class Mode(models.TextChoices):
        CASH = "CASH", "Cash"
        CARD = "CARD", "Card"
        UPI = "UPI", "UPI"

    customer = models.ForeignKey(Customer, on_delete=models.PROTECT, related_name="payments")
    amount = models.DecimalField(max_digits=12, decimal_places=2, validators=[MinValueValidator(0)])
    mode = models.CharField(max_length=8, choices=Mode.choices, default=Mode.CASH)
    date = models.DateField(db_index=True)
    note = models.CharField(max_length=240, blank=True)