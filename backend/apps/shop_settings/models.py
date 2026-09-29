from django.core.validators import MinValueValidator
from django.db import models


class ShopSettings(models.Model):
    shop_name = models.CharField(max_length=180, default="POS Billing")
    address = models.TextField(blank=True)
    gstin = models.CharField(max_length=20, blank=True)
    phone = models.CharField(max_length=24, blank=True)
    logo = models.ImageField(upload_to="shop/", blank=True)
    invoice_prefix = models.CharField(max_length=20, default="INV")
    footer_note = models.CharField(max_length=240, blank=True)
    default_tax = models.DecimalField(max_digits=5, decimal_places=2, default=0, validators=[MinValueValidator(0)])
    currency = models.CharField(max_length=8, default="INR")
    paper_size = models.CharField(max_length=8, choices=[("80MM", "80mm thermal"), ("A4", "A4")], default="80MM")
    allow_negative_stock = models.BooleanField(default=False)

    def save(self, *args, **kwargs):
        self.pk = 1
        super().save(*args, **kwargs)

    @classmethod
    def get_solo(cls):
        instance, _ = cls.objects.get_or_create(pk=1)
        return instance

    def __str__(self) -> str:
        return self.shop_name