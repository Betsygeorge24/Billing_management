import json
from decimal import Decimal

from django.test import TestCase

from apps.accounts.models import User
from apps.billing.models import Bill, BillItem
from apps.catalog.models import Category, Product, ProductVariant


class BillingEndpointTests(TestCase):
    def setUp(self):
        self.category = Category.objects.create(name="Shirts")
        self.product = Product.objects.create(
            name="Linen shirt", sku="SH-01", barcode="1234567890", category=self.category,
            unit=Product.Unit.PCS, cost_price=Decimal("40"), selling_price=Decimal("100"),
            mrp=Decimal("120"), tax_percent=Decimal("5"),
        )
        self.variant = ProductVariant.objects.create(
            product=self.product, sku="SH-01-M", barcode="9876543210", size="M", stock_qty=Decimal("10")
        )
        self.staff = User.objects.create_user(
            email="cashier@example.com", password="Strong-password-123", name="Cashier", role=User.Role.STAFF,
            permissions={"can_give_discount": True, "max_discount_percent": 10},
        )
        self.other_staff = User.objects.create_user(
            email="other@example.com", password="Strong-password-123", name="Other Cashier", role=User.Role.STAFF,
        )

    def post_json(self, path, payload):
        return self.client.post(path, data=json.dumps(payload), content_type="application/json")

    def test_search_returns_variant_price_and_stock(self):
        self.client.force_login(self.staff)

        response = self.client.get("/billing/api/products/?q=9876543210")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["results"][0]["variant_id"], self.variant.pk)
        self.assertEqual(response.json()["results"][0]["price"], "100.00")
        self.assertEqual(response.json()["results"][0]["stock"], "10.000")

    def test_checkout_ignores_client_submitted_price(self):
        self.client.force_login(self.staff)

        response = self.post_json("/billing/api/checkout/", {
            "items": [{"variant_id": self.variant.pk, "qty": "1", "unit_price": "0.01", "discount": "0"}],
            "payments": [{"mode": "CASH", "amount": "105"}],
        })

        self.assertEqual(response.status_code, 201)
        bill = Bill.objects.get(pk=response.json()["bill_id"])
        item = BillItem.objects.get(bill=bill)
        self.assertEqual(item.unit_price, Decimal("100.00"))
        self.assertEqual(bill.grand_total, Decimal("105.00"))

    def test_over_cap_checkout_returns_exact_limit_without_writes(self):
        self.client.force_login(self.staff)

        response = self.post_json("/billing/api/checkout/", {
            "items": [{"variant_id": self.variant.pk, "qty": "1", "discount": "0"}],
            "discount_type": "AMOUNT",
            "discount_value": "10.01",
            "payments": [{"mode": "CASH", "amount": "100"}],
        })

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["errors"], ["Discount exceeds your maximum of 10% (₹10.00 on this bill)."])
        self.assertEqual(Bill.objects.count(), 0)
        self.variant.refresh_from_db()
        self.assertEqual(self.variant.stock_qty, Decimal("10.000"))

    def test_staff_cannot_view_another_cashiers_bill(self):
        bill = Bill.objects.create(bill_no="INV-2026-000099", staff=self.other_staff)
        self.client.force_login(self.staff)

        response = self.client.get(f"/billing/bills/{bill.pk}/")

        self.assertEqual(response.status_code, 404)

    def test_staff_bill_history_only_contains_own_bills(self):
        own = Bill.objects.create(bill_no="INV-2026-000101", staff=self.staff)
        Bill.objects.create(bill_no="INV-2026-000102", staff=self.other_staff)
        self.client.force_login(self.staff)

        response = self.client.get("/billing/bills/")

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, own.bill_no)
        self.assertNotContains(response, "INV-2026-000102")