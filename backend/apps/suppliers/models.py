from django.core.validators import MinValueValidator
from django.db import models

from apps.core.models import TimeStampedModel


class Supplier(TimeStampedModel):
    name = models.CharField(max_length=180, db_index=True)
    phone = models.CharField(max_length=24, blank=True)
    email = models.EmailField(blank=True)
    address = models.TextField(blank=True)
    gstin = models.CharField(max_length=20, blank=True)
    opening_balance = models.DecimalField(max_digits=12, decimal_places=2, default=0, validators=[MinValueValidator(0)])
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["name"]

    def __str__(self) -> str:
        return self.name


class Purchase(TimeStampedModel):
    class Status(models.TextChoices):
        RECEIVED = "RECEIVED", "Received"
        CANCELLED = "CANCELLED", "Cancelled"

    supplier = models.ForeignKey(Supplier, on_delete=models.PROTECT, related_name="purchases")
    invoice_no = models.CharField(max_length=80, db_index=True)
    date = models.DateField(db_index=True)
    subtotal = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    tax = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    total = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    paid_amount = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    status = models.CharField(max_length=12, choices=Status.choices, default=Status.RECEIVED, db_index=True)

    class Meta:
        ordering = ["-date", "-id"]
        constraints = [models.UniqueConstraint(fields=["supplier", "invoice_no"], name="unique_supplier_invoice")]

    def __str__(self) -> str:
        return self.invoice_no


class PurchaseItem(models.Model):
    purchase = models.ForeignKey(Purchase, on_delete=models.CASCADE, related_name="items")
    product = models.ForeignKey("catalog.Product", on_delete=models.PROTECT)
    variant = models.ForeignKey("catalog.ProductVariant", on_delete=models.PROTECT, related_name="purchase_items")
    qty = models.DecimalField(max_digits=12, decimal_places=3, validators=[MinValueValidator(0)])
    unit_cost = models.DecimalField(max_digits=12, decimal_places=2, validators=[MinValueValidator(0)])
    tax_percent = models.DecimalField(max_digits=5, decimal_places=2, default=0)
    line_total = models.DecimalField(max_digits=12, decimal_places=2, default=0)


class SupplierPayment(TimeStampedModel):
    class Mode(models.TextChoices):
        CASH = "CASH", "Cash"
        BANK = "BANK", "Bank"
        UPI = "UPI", "UPI"

    supplier = models.ForeignKey(Supplier, on_delete=models.PROTECT, related_name="payments")
    amount = models.DecimalField(max_digits=12, decimal_places=2, validators=[MinValueValidator(0)])
    mode = models.CharField(max_length=8, choices=Mode.choices, default=Mode.CASH)
    date = models.DateField(db_index=True)
    note = models.CharField(max_length=240, blank=True)