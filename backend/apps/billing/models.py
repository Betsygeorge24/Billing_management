from django.conf import settings
from django.core.validators import MinValueValidator
from django.db import models

from apps.core.models import TimeStampedModel


class Bill(TimeStampedModel):
    class DiscountType(models.TextChoices):
        AMOUNT = "AMOUNT", "Amount"
        PERCENT = "PERCENT", "Percent"

    class Status(models.TextChoices):
        COMPLETED = "COMPLETED", "Completed"
        HELD = "HELD", "Held"
        VOID = "VOID", "Void"
        PARTIALLY_RETURNED = "PARTIALLY_RETURNED", "Partially returned"
        RETURNED = "RETURNED", "Returned"

    bill_no = models.CharField(max_length=40, unique=True, db_index=True)
    customer = models.ForeignKey("customers.Customer", on_delete=models.SET_NULL, null=True, blank=True, related_name="bills")
    staff = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="bills")
    subtotal = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    discount_type = models.CharField(max_length=10, choices=DiscountType.choices, default=DiscountType.AMOUNT)
    discount_value = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    discount_amount = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    tax_amount = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    tax_inclusive = models.BooleanField(default=False)
    round_off = models.DecimalField(max_digits=8, decimal_places=2, default=0)
    grand_total = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    paid_amount = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    balance = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.COMPLETED, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["staff", "created_at"]), models.Index(fields=["customer", "created_at"])]

    def __str__(self) -> str:
        return self.bill_no


class BillItem(models.Model):
    bill = models.ForeignKey(Bill, on_delete=models.PROTECT, related_name="items")
    product = models.ForeignKey("catalog.Product", on_delete=models.PROTECT, related_name="bill_items")
    variant = models.ForeignKey("catalog.ProductVariant", on_delete=models.PROTECT, related_name="bill_items")
    name_snapshot = models.CharField(max_length=220)
    sku_snapshot = models.CharField(max_length=64, blank=True)
    qty = models.DecimalField(max_digits=12, decimal_places=3, validators=[MinValueValidator(0)])
    unit_price = models.DecimalField(max_digits=12, decimal_places=2, validators=[MinValueValidator(0)])
    discount = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    tax_percent = models.DecimalField(max_digits=5, decimal_places=2, default=0)
    tax_amount = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    line_total = models.DecimalField(max_digits=12, decimal_places=2, default=0)


class Payment(models.Model):
    class Mode(models.TextChoices):
        CASH = "CASH", "Cash"
        CARD = "CARD", "Card"
        UPI = "UPI", "UPI"
        CREDIT = "CREDIT", "Credit"

    bill = models.ForeignKey(Bill, on_delete=models.PROTECT, related_name="payments")
    mode = models.CharField(max_length=10, choices=Mode.choices)
    amount = models.DecimalField(max_digits=12, decimal_places=2, validators=[MinValueValidator(0)])
    reference = models.CharField(max_length=100, blank=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)


class HeldBill(TimeStampedModel):
    staff = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="held_bills")
    customer = models.ForeignKey("customers.Customer", on_delete=models.SET_NULL, null=True, blank=True)
    label = models.CharField(max_length=120, blank=True)
    cart_data = models.JSONField(default=dict)

    class Meta:
        ordering = ["-updated_at"]


class NumberCounter(models.Model):
    name = models.CharField(max_length=40, unique=True)
    value = models.PositiveBigIntegerField(default=0)

    def __str__(self) -> str:
        return f"{self.name}: {self.value}"