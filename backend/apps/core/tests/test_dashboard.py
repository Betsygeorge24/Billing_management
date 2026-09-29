from decimal import Decimal

from django.test import TestCase

from apps.accounts.models import User
from apps.billing.models import Bill
from apps.catalog.models import Category, Product, ProductVariant


class DashboardMetricsTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_user(
            email="owner@example.com", password="Strong-password-123", name="Owner", role=User.Role.ADMIN,
        )
        self.staff = User.objects.create_user(
            email="cashier@example.com", password="Strong-password-123", name="Cashier", role=User.Role.STAFF,
        )
        User.objects.create_user(
            email="inactive@example.com", password="Strong-password-123", name="Inactive Cashier",
            role=User.Role.STAFF, is_active=False,
        )
        category = Category.objects.create(name="Dashboard fabric")
        product = Product.objects.create(
            name="Low-stock fabric", sku="DASH-FAB-1", category=category, cost_price=Decimal("10"),
            selling_price=Decimal("20"), mrp=Decimal("25"), min_stock_alert=Decimal("2"),
        )
        ProductVariant.objects.create(product=product, sku="DASH-FAB-1-LOW", stock_qty=Decimal("1"))
        ProductVariant.objects.create(product=product, sku="DASH-FAB-1-OK", size="Wide", stock_qty=Decimal("3"))

    def test_dashboard_aggregates_real_sales_stock_and_staff(self):
        Bill.objects.create(bill_no="DASH-001", staff=self.staff, grand_total=Decimal("80.00"))
        Bill.objects.create(
            bill_no="DASH-VOID", staff=self.staff, grand_total=Decimal("500.00"), status=Bill.Status.VOID,
        )
        Bill.objects.create(
            bill_no="DASH-HELD", staff=self.staff, grand_total=Decimal("400.00"), status=Bill.Status.HELD,
        )
        self.client.force_login(self.admin)

        response = self.client.get("/admin-portal/")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["today_sales"], Decimal("80.00"))
        self.assertEqual(response.context["month_sales"], Decimal("80.00"))
        self.assertEqual(response.context["today_transaction_count"], 1)
        self.assertEqual(response.context["low_stock_count"], 1)
        self.assertEqual(response.context["active_staff_count"], 1)
        self.assertEqual(len(response.context["recent_bills"]), 1)

    def test_dashboard_metrics_are_zero_without_bills(self):
        admin = User.objects.create_user(
            email="empty-owner@example.com", password="Strong-password-123", name="Empty Owner", role=User.Role.ADMIN,
        )
        self.client.force_login(admin)

        response = self.client.get("/admin-portal/")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["today_sales"], Decimal("0.00"))
        self.assertEqual(response.context["today_transaction_count"], 0)
        self.assertContains(response, 'aria-label="Main navigation"')
        self.assertContains(response, 'aria-current="page"')
        self.assertContains(response, ">Transactions</span>")

    def test_staff_cannot_open_dashboard(self):
        self.client.force_login(self.staff)

        self.assertEqual(self.client.get("/admin-portal/").status_code, 403)

    def test_mobile_dashboard_renders_live_metrics_in_phone_frame(self):
        Bill.objects.create(bill_no="MOBILE-001", staff=self.staff, grand_total=Decimal("45.00"))
        self.client.force_login(self.admin)

        response = self.client.get("/m/admin/")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["today_sales"], Decimal("45.00"))
        self.assertContains(response, "₹45.00")
        self.assertContains(response, 'class="phone-frame"')

    def test_admin_dashboard_has_desktop_and_mobile_presentations(self):
        self.client.force_login(self.admin)

        response = self.client.get("/admin-portal/")

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "dashboard-desktop-bills")
        self.assertContains(response, "dashboard-mobile-bills")

    def test_mobile_bills_are_paginated_and_show_receipt_links(self):
        Bill.objects.create(bill_no="MOBILE-002", staff=self.staff, grand_total=Decimal("75.00"))
        self.client.force_login(self.admin)

        response = self.client.get("/m/admin/bills/")

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "MOBILE-002")
        self.assertContains(response, "/billing/bills/")
        self.assertEqual(response.context["page_obj"].paginator.per_page, 20)

    def test_mobile_more_has_only_working_quick_links(self):
        self.client.force_login(self.admin)

        response = self.client.get("/m/admin/more/")

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "/admin-portal/")
        self.assertContains(response, "/billing/")
        self.assertContains(response, "/m/admin/bills/")

    def test_staff_is_blocked_from_mobile_admin_subpages(self):
        self.client.force_login(self.staff)

        self.assertEqual(self.client.get("/m/admin/bills/").status_code, 403)
        self.assertEqual(self.client.get("/m/admin/more/").status_code, 403)