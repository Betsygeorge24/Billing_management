import json

from django.core.exceptions import ValidationError
from django.db.models import Q
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, render
from django.views.decorators.http import require_GET, require_http_methods, require_POST

from apps.billing.models import Bill, HeldBill
from apps.billing.services import checkout, hold_bill
from apps.catalog.models import Category, ProductVariant
from apps.core.access import staff_or_admin_required
from apps.customers.models import Customer


def _json_body(request):
    try:
        data = json.loads(request.body or b"{}")
    except (json.JSONDecodeError, UnicodeDecodeError):
        raise ValidationError("Request body must be valid JSON.") from None
    if not isinstance(data, dict):
        raise ValidationError("Request body must be a JSON object.")
    return data


def _error_response(error, status=400):
    messages = error.messages if isinstance(error, ValidationError) else [str(error)]
    return JsonResponse({"ok": False, "errors": messages}, status=status)


@staff_or_admin_required
def billing_page(request):
    permissions = request.user.permissions if isinstance(request.user.permissions, dict) else {}
    return render(request, "billing/pos.html", {
        "categories": Category.objects.filter(is_active=True),
        "customers": Customer.objects.filter(is_active=True).order_by("name")[:300],
        "can_discount": request.user.is_admin or permissions.get("can_give_discount", False),
        "max_discount_percent": None if request.user.is_admin else permissions.get("max_discount_percent", 0),
        "is_admin": request.user.is_admin,
    })


@staff_or_admin_required
@require_GET
def product_search(request):
    query = request.GET.get("q", "").strip()[:100]
    category_id = request.GET.get("category", "").strip()
    products = ProductVariant.objects.select_related("product", "product__category").filter(
        product__is_active=True, product__category__is_active=True
    )
    if category_id:
        products = products.filter(product__category_id=category_id)
    if query:
        products = products.filter(
            Q(product__name__icontains=query)
            | Q(product__sku__iexact=query)
            | Q(product__barcode__iexact=query)
            | Q(sku__iexact=query)
            | Q(barcode__iexact=query)
        )
    else:
        products = products.order_by("product__name")
    results = []
    for variant in products[:40]:
        product = variant.product
        label = " / ".join(value for value in (variant.size, variant.color) if value)
        results.append({
            "variant_id": variant.pk,
            "product": product.name,
            "variant": label,
            "sku": variant.sku,
            "barcode": variant.barcode or product.barcode,
            "category": product.category.name,
            "unit": product.unit,
            "price": str(variant.price_override if variant.price_override is not None else product.selling_price),
            "tax_percent": str(product.tax_percent),
            "stock": str(variant.stock_qty),
        })
    return JsonResponse({"results": results})


@staff_or_admin_required
@require_POST
def customer_create(request):
    try:
        data = _json_body(request)
        name = str(data.get("name", "")).strip()
        phone = str(data.get("phone", "")).strip()
        if not name or len(name) > 180 or len(phone) > 24:
            raise ValidationError("Enter a customer name and a phone number of at most 24 characters.")
        customer = Customer.objects.create(name=name, phone=phone)
        return JsonResponse({"ok": True, "customer": {"id": customer.pk, "name": customer.name, "phone": customer.phone}}, status=201)
    except ValidationError as error:
        return _error_response(error)


@staff_or_admin_required
@require_http_methods(["GET", "POST"])
def held_bills(request):
    if request.method == "GET":
        held = HeldBill.objects.filter(staff=request.user).select_related("customer")
        return JsonResponse({"results": [{
            "id": item.pk, "label": item.label or f"Held bill {item.pk}",
            "customer": item.customer.name if item.customer else "Walk-in",
            "item_count": len(item.cart_data.get("items", [])),
            "updated_at": item.updated_at.isoformat(),
        } for item in held]})
    try:
        data = _json_body(request)
        customer = None
        if data.get("customer_id"):
            customer = get_object_or_404(Customer, pk=data["customer_id"], is_active=True)
        held = hold_bill(user=request.user, cart=data.get("items"), customer=customer, label=data.get("label", ""))
        return JsonResponse({"ok": True, "id": held.pk}, status=201)
    except ValidationError as error:
        return _error_response(error)


@staff_or_admin_required
@require_http_methods(["GET", "DELETE"])
def held_bill_detail(request, held_id):
    held = get_object_or_404(HeldBill, pk=held_id, staff=request.user)
    if request.method == "DELETE":
        held.delete()
        return JsonResponse({"ok": True})
    return JsonResponse({
        "id": held.pk,
        "items": held.cart_data.get("items", []),
        "customer_id": held.customer_id,
    })


@staff_or_admin_required
@require_POST
def checkout_view(request):
    try:
        data = _json_body(request)
        customer = None
        if data.get("customer_id"):
            customer = get_object_or_404(Customer, pk=data["customer_id"], is_active=True)
        result = checkout(
            user=request.user,
            cart=data.get("items"),
            payments=data.get("payments"),
            customer=customer,
            discount_type=data.get("discount_type", Bill.DiscountType.AMOUNT),
            discount_value=data.get("discount_value", "0"),
            tax_inclusive=data.get("tax_inclusive") is True,
            round_total=data.get("round_total") is True,
            held_bill_id=int(data["held_bill_id"]) if data.get("held_bill_id") else None,
        )
        bill = result["bill"]
        return JsonResponse({
            "ok": True,
            "bill_id": bill.pk,
            "bill_no": bill.bill_no,
            "grand_total": str(bill.grand_total),
            "change_due": str(result["change_due"]),
            "redirect_url": f"/billing/bills/{bill.pk}/",
        }, status=201)
    except (ValidationError, TypeError, ValueError) as error:
        return _error_response(error if isinstance(error, ValidationError) else ValidationError("Checkout data is invalid."))


@staff_or_admin_required
@require_GET
def my_bills(request):
    bills = Bill.objects.select_related("customer", "staff").prefetch_related("payments").order_by("-created_at")
    if not request.user.is_admin:
        bills = bills.filter(staff=request.user)
    return render(request, "billing/my_bills.html", {"bills": bills[:100]})


@staff_or_admin_required
@require_GET
def bill_receipt(request, bill_id):
    bills = Bill.objects.select_related("customer", "staff").prefetch_related("items", "payments")
    if not request.user.is_admin:
        bills = bills.filter(staff=request.user)
    bill = get_object_or_404(bills, pk=bill_id)
    from apps.shop_settings.models import ShopSettings

    return render(request, "billing/receipt.html", {"bill": bill, "shop": ShopSettings.get_solo()})