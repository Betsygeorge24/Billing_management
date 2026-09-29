from django.core.validators import MinValueValidator
from django.db import models

from apps.core.models import TimeStampedModel


class Category(TimeStampedModel):
    name = models.CharField(max_length=100, unique=True)
    description = models.TextField(blank=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["name"]

    def __str__(self) -> str:
        return self.name


class Product(TimeStampedModel):
    class Unit(models.TextChoices):
        PCS = "PCS", "Pieces"
        METER = "METER", "Meter"
        KG = "KG", "Kilogram"

    name = models.CharField(max_length=180, db_index=True)
    sku = models.CharField(max_length=64, unique=True, db_index=True)
    barcode = models.CharField(max_length=80, blank=True, db_index=True)
    category = models.ForeignKey(Category, on_delete=models.PROTECT, related_name="products")
    hsn_code = models.CharField(max_length=20, blank=True)
    unit = models.CharField(max_length=10, choices=Unit.choices, default=Unit.PCS)
    cost_price = models.DecimalField(max_digits=12, decimal_places=2, validators=[MinValueValidator(0)])
    selling_price = models.DecimalField(max_digits=12, decimal_places=2, validators=[MinValueValidator(0)])
    mrp = models.DecimalField(max_digits=12, decimal_places=2, validators=[MinValueValidator(0)])
    tax_percent = models.DecimalField(max_digits=5, decimal_places=2, default=0, validators=[MinValueValidator(0)])
    min_stock_alert = models.DecimalField(max_digits=12, decimal_places=3, default=0, validators=[MinValueValidator(0)])
    is_active = models.BooleanField(default=True, db_index=True)
    image = models.ImageField(upload_to="products/", blank=True)

    class Meta:
        ordering = ["name"]
        indexes = [models.Index(fields=["category", "is_active"])]

    def __str__(self) -> str:
        return f"{self.name} ({self.sku})"


class ProductVariant(TimeStampedModel):
    product = models.ForeignKey(Product, on_delete=models.CASCADE, related_name="variants")
    size = models.CharField(max_length=40, blank=True)
    color = models.CharField(max_length=50, blank=True)
    sku = models.CharField(max_length=64, unique=True, db_index=True)
    barcode = models.CharField(max_length=80, blank=True, db_index=True)
    stock_qty = models.DecimalField(max_digits=12, decimal_places=3, default=0)
    price_override = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)

    class Meta:
        ordering = ["product__name", "size", "color"]
        constraints = [models.UniqueConstraint(fields=["product", "size", "color"], name="unique_product_variant")]
        indexes = [models.Index(fields=["product", "stock_qty"])]

    def __str__(self) -> str:
        details = " / ".join(value for value in (self.size, self.color) if value)
        return f"{self.product.name} - {details}" if details else self.product.name