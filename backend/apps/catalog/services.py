from decimal import Decimal, InvalidOperation

from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import F

from apps.audit.models import AuditLog
from apps.catalog.models import ProductVariant
from apps.inventory.models import StockMovement
from apps.shop_settings.models import ShopSettings


@transaction.atomic
def adjust_stock(*, variant_id: int, quantity_change: Decimal, reason: str, user) -> ProductVariant:
    try:
        delta = Decimal(str(quantity_change))
    except (InvalidOperation, TypeError, ValueError):
        raise ValidationError("Enter a valid stock quantity change.") from None
    if not delta or not delta.is_finite() or not reason.strip():
        raise ValidationError("A non-zero adjustment and a reason are required.")
    variant = ProductVariant.objects.select_related("product").get(pk=variant_id)
    if not ShopSettings.get_solo().allow_negative_stock and variant.stock_qty + delta < 0:
        raise ValidationError("This adjustment would make stock negative.")
    ProductVariant.objects.filter(pk=variant.pk).update(stock_qty=F("stock_qty") + delta)
    variant.refresh_from_db(fields=["stock_qty"])
    StockMovement.objects.create(
        variant=variant, type=StockMovement.MovementType.ADJUSTMENT, qty=delta,
        ref_type="ADJUSTMENT", ref_id=str(variant.pk), note=reason.strip(), user=user,
    )
    AuditLog.objects.create(
        user=user, action="stock_adjustment", object_type="ProductVariant", object_id=str(variant.pk),
        before_data={"stock_qty": str(variant.stock_qty - delta)},
        after_data={"stock_qty": str(variant.stock_qty), "reason": reason.strip()},
    )
    return variant