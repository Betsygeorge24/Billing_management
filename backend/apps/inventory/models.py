from django.conf import settings
from django.db import models

from apps.core.models import TimeStampedModel


class StockMovement(TimeStampedModel):
    class MovementType(models.TextChoices):
        PURCHASE = "PURCHASE", "Purchase"
        SALE = "SALE", "Sale"
        RETURN = "RETURN", "Return"
        ADJUSTMENT = "ADJUSTMENT", "Adjustment"
        DAMAGE = "DAMAGE", "Damage"
        VOID = "VOID", "Void reversal"

    variant = models.ForeignKey("catalog.ProductVariant", on_delete=models.PROTECT, related_name="movements")
    type = models.CharField(max_length=16, choices=MovementType.choices, db_index=True)
    qty = models.DecimalField(max_digits=12, decimal_places=3)
    ref_type = models.CharField(max_length=40, blank=True)
    ref_id = models.CharField(max_length=64, blank=True)
    note = models.TextField(blank=True)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["ref_type", "ref_id"]), models.Index(fields=["type", "created_at"])]

    def __str__(self) -> str:
        return f"{self.type} {self.qty} {self.variant}"