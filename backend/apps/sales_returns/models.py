from django.conf import settings
from django.db import models

from apps.core.models import TimeStampedModel


class SaleReturn(TimeStampedModel):
    return_no = models.CharField(max_length=40, unique=True, db_index=True)
    bill = models.ForeignKey("billing.Bill", on_delete=models.PROTECT, related_name="returns")
    staff = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="processed_returns")
    reason = models.TextField()
    refund_mode = models.CharField(max_length=12, choices=[("CASH", "Cash"), ("UPI", "UPI"), ("CREDIT", "Customer credit")])
    refund_amount = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ["-created_at"]


class SaleReturnItem(models.Model):
    sale_return = models.ForeignKey(SaleReturn, on_delete=models.CASCADE, related_name="items")
    bill_item = models.ForeignKey("billing.BillItem", on_delete=models.PROTECT, related_name="return_items")
    qty = models.DecimalField(max_digits=12, decimal_places=3)
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    restock = models.BooleanField(default=True)