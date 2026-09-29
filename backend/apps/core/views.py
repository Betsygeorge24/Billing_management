from datetime import datetime, time, timedelta
from decimal import Decimal

from django.db.models import F, Sum
from django.shortcuts import redirect, render
from django.utils import timezone
from django.core.paginator import Paginator

from apps.accounts.models import User
from apps.billing.models import Bill
from apps.catalog.models import ProductVariant
from apps.core.access import admin_required


def _dashboard_context():
    today = timezone.localdate()
    current_month = today.replace(day=1)
    today_start = timezone.make_aware(datetime.combine(today, time.min))
    tomorrow_start = timezone.make_aware(datetime.combine(today + timedelta(days=1), time.min))
    month_start = timezone.make_aware(datetime.combine(current_month, time.min))

    reportable_bills = Bill.objects.exclude(status__in=[Bill.Status.HELD, Bill.Status.VOID])
    today_bills = reportable_bills.filter(created_at__gte=today_start, created_at__lt=tomorrow_start)
    month_bills = reportable_bills.filter(created_at__gte=month_start, created_at__lt=tomorrow_start)
    low_stock_products = ProductVariant.objects.filter(
        product__is_active=True,
        product__min_stock_alert__gt=0,
        stock_qty__lte=F("product__min_stock_alert"),
    ).values("product_id").distinct().count()
    recent_bills = reportable_bills.select_related("customer", "staff").order_by("-created_at")[:5]

    context = {
        "today": today,
        "today_sales": today_bills.aggregate(total=Sum("grand_total"))["total"] or Decimal("0.00"),
        "today_transaction_count": today_bills.count(),
        "month_sales": month_bills.aggregate(total=Sum("grand_total"))["total"] or Decimal("0.00"),
        "low_stock_count": low_stock_products,
        "active_staff_count": User.objects.filter(role=User.Role.STAFF, is_active=True).count(),
        "recent_bills": recent_bills,
    }
    return context


@admin_required
def admin_dashboard(request):
    return render(request, "admin_portal/dashboard.html", _dashboard_context())


@admin_required
def mobile_admin_home(request):
    return render(request, "mobile/dashboard.html", _dashboard_context())


@admin_required
def mobile_bills(request):
    bills = Bill.objects.exclude(status__in=[Bill.Status.HELD, Bill.Status.VOID])
    bills = bills.select_related("customer", "staff").prefetch_related("payments").order_by("-created_at")
    page = Paginator(bills, 20).get_page(request.GET.get("page"))
    return render(request, "mobile/bills.html", {"page_obj": page, "bills": page.object_list})


@admin_required
def mobile_more(_request):
    return render(_request, "mobile/more.html")


@admin_required
def mobile_root(request):
    return redirect("mobile_admin_home")


