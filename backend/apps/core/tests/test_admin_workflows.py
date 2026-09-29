from decimal import Decimal

from django.core.exceptions import ValidationError
from django.test import TestCase

from apps.accounts.models import User
from apps.billing.models import Bill, BillItem, Payment
from apps.billing.services import collect_bill_balance, void_bill
from apps.catalog.models import Category, Product, ProductVariant
from apps.customers.models import Customer
from apps.inventory.models import StockMovement
from apps.ledger.models import LedgerEntry
from apps.sales_returns.services import process_return
from apps.suppliers.models import Supplier
from apps.suppliers.services import collect_supplier_payment, receive_purchase, supplier_balance


class AdminWorkflowTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_user(
            email="admin@example.com", password="Strong-password-123", name="Owner", role=User.Role.ADMIN,
        )
        self.staff = User.objects.create_user(
            email="staff@example.com", password="Strong-password-123", name="Cashier", role=User.Role.STAFF,
        )
        category = Category.objects.create(name="Workflow category")
        self.product = Product.objects.create(
            name="Workflow product", sku="WORK-001", category=category, cost_price=Decimal("10"),
            selling_price=Decimal("100"), mrp=Decimal("120"), tax_percent=Decimal("5"), min_stock_alert=Decimal("2"),
        )
        self.variant = ProductVariant.objects.create(product=self.product, sku="WORK-001-STD", stock_qty=Decimal("5"))
        self.supplier = Supplier.objects.create(name="Workflow supplier", opening_balance=Decimal("20"))
        self.customer = Customer.objects.create(name="Workflow customer", phone="9000000000", credit_balance=Decimal("50"))

    def _bill(self, bill_no, *, qty=Decimal("2"), total=Decimal("210"), paid=Decimal("210"), balance=Decimal("0")):
        bill = Bill.objects.create(
            bill_no=bill_no, staff=self.staff, customer=self.customer, subtotal=Decimal("200"),
            tax_amount=Decimal("10"), grand_total=total, paid_amount=paid, balance=balance,
        )
        BillItem.objects.create(
            bill=bill, product=self.product, variant=self.variant, name_snapshot=self.product.name,
            sku_snapshot=self.variant.sku, qty=qty, unit_price=Decimal("100"), tax_percent=Decimal("5"),
            tax_amount=Decimal("10"), line_total=total,
        )
        return bill

    def test_admin_module_pages_are_protected_and_render(self):
        self.client.force_login(self.admin)
        paths = [
            "/admin-portal/products/", "/admin-portal/categories/", "/admin-portal/staff/",
            "/admin-portal/suppliers/", "/admin-portal/purchases/", "/admin-portal/customers/",
            "/admin-portal/returns/", "/admin-portal/bills/", "/admin-portal/ledger/",
            "/admin-portal/expenses/", "/admin-portal/reports/", "/admin-portal/settings/", "/admin-portal/audit/",
        ]

        self.assertTrue(all(self.client.get(path).status_code == 200 for path in paths))
        self.client.force_login(self.staff)
        self.assertTrue(all(self.client.get(path).status_code == 403 for path in paths))
        mobile_paths = [path.replace("/admin-portal/", "/m/admin/") for path in paths]
        self.assertTrue(all(self.client.get(path).status_code == 403 for path in mobile_paths))

    def test_every_admin_module_renders_inside_mobile_frame(self):
        bill = self._bill("MOBILE-ADMIN-001")
        self.client.force_login(self.admin)
        paths = [
            "/m/admin/", "/m/admin/more/", "/m/admin/products/", "/m/admin/products/new/",
            "/m/admin/categories/", f"/m/admin/products/{self.product.pk}/",
            f"/m/admin/variants/{self.variant.pk}/stock/", "/m/admin/staff/", "/m/admin/staff/new/",
            f"/m/admin/staff/{self.staff.pk}/", "/m/admin/suppliers/", "/m/admin/suppliers/new/",
            f"/m/admin/suppliers/{self.supplier.pk}/", f"/m/admin/suppliers/{self.supplier.pk}/edit/",
            "/m/admin/purchases/", "/m/admin/purchases/new/", "/m/admin/customers/",
            "/m/admin/customers/new/", f"/m/admin/customers/{self.customer.pk}/",
            f"/m/admin/customers/{self.customer.pk}/edit/", "/m/admin/returns/",
            f"/m/admin/returns/new/?bill_no={bill.bill_no}", "/m/admin/bills/",
            f"/m/admin/bills/{bill.pk}/", "/m/admin/ledger/", "/m/admin/expenses/",
            "/m/admin/reports/", "/m/admin/reports/?kind=stock", "/m/admin/settings/", "/m/admin/audit/",
        ]

        for path in paths:
            with self.subTest(path=path):
                response = self.client.get(path)
                self.assertEqual(response.status_code, 200)
                self.assertContains(response, 'class="phone-frame"')
                self.assertContains(response, 'aria-label="Mobile navigation"')
        self.assertContains(self.client.get("/m/admin/products/new/"), "Product variants")

    def test_supplier_and_customer_detail_pages_render(self):
        self.client.force_login(self.admin)

        supplier_response = self.client.get(f"/admin-portal/suppliers/{self.supplier.pk}/")
        customer_response = self.client.get(f"/admin-portal/customers/{self.customer.pk}/")

        self.assertEqual(supplier_response.status_code, 200)
        self.assertEqual(customer_response.status_code, 200)
        self.assertContains(supplier_response, 'max="20.00"')

    def test_supplier_payment_over_balance_shows_error_and_is_not_saved(self):
        self.client.force_login(self.admin)

        response = self.client.post(f"/admin-portal/suppliers/{self.supplier.pk}/", {
            "amount": "25", "mode": "CASH", "date": "2026-09-29", "note": "Too much",
        })

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Payment cannot exceed the supplier&#x27;s outstanding balance.")
        self.assertEqual(self.supplier.payments.count(), 0)

    def test_mobile_supplier_payment_saves_and_returns_to_mobile_supplier(self):
        self.client.force_login(self.admin)

        response = self.client.post(f"/m/admin/suppliers/{self.supplier.pk}/", {
            "amount": "5", "mode": "CASH", "date": "2026-09-29", "note": "Mobile payment",
        })

        self.assertRedirects(response, f"/m/admin/suppliers/{self.supplier.pk}/")
        self.assertEqual(self.supplier.payments.count(), 1)

    def test_supplier_without_balance_is_told_payment_is_not_due(self):
        self.supplier.opening_balance = Decimal("0")
        self.supplier.save(update_fields=["opening_balance"])
        self.client.force_login(self.admin)

        response = self.client.get(f"/admin-portal/suppliers/{self.supplier.pk}/")

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "No outstanding balance is due")
        self.assertNotContains(response, "Record payment</button>")

    def test_product_form_creates_variant_and_stock_adjustment_is_recorded(self):
        self.client.force_login(self.admin)
        response = self.client.post("/m/admin/products/new/", {
            "name": "New cotton", "sku": "COT-NEW", "barcode": "COTNEW01", "category": self.product.category_id,
            "hsn_code": "5208", "unit": "METER", "cost_price": "20", "selling_price": "40", "mrp": "50",
            "tax_percent": "5", "min_stock_alert": "2", "is_active": "on",
            "variants-TOTAL_FORMS": "1", "variants-INITIAL_FORMS": "0",
            "variants-MIN_NUM_FORMS": "0", "variants-MAX_NUM_FORMS": "1000",
            "variants-0-size": "42 inch", "variants-0-color": "Blue",
            "variants-0-sku": "COT-NEW-BLUE", "variants-0-barcode": "COTNEWBLUE",
            "variants-0-price_override": "",
        })

        self.assertRedirects(response, "/m/admin/products/")
        new_variant = ProductVariant.objects.get(sku="COT-NEW-BLUE")
        adjust_response = self.client.post(f"/m/admin/variants/{new_variant.pk}/stock/", {
            "quantity_change": "4.5", "reason": "Opening count",
        })

        self.assertRedirects(adjust_response, f"/m/admin/products/{new_variant.product_id}/")
        new_variant.refresh_from_db()
        self.assertEqual(new_variant.stock_qty, Decimal("4.500"))
        self.assertEqual(StockMovement.objects.filter(variant=new_variant, type=StockMovement.MovementType.ADJUSTMENT).count(), 1)

    def test_staff_form_creates_account_with_discount_permissions(self):
        self.client.force_login(self.admin)

        response = self.client.post("/m/admin/staff/new/", {
            "name": "New Cashier", "email": "new-cashier@example.com", "phone": "9111111111",
            "role": User.Role.STAFF, "is_active": "on", "password": "Cashier-password-321",
            "can_give_discount": "on", "max_discount_percent": "7.5", "can_process_return": "on",
            "can_use_customer_credit": "on",
        })

        self.assertRedirects(response, "/m/admin/staff/")
        created = User.objects.get(email="new-cashier@example.com")
        self.assertTrue(created.check_password("Cashier-password-321"))
        self.assertTrue(created.permissions["can_give_discount"])
        self.assertEqual(created.permissions["max_discount_percent"], "7.5")

    def test_reports_export_as_csv(self):
        self._bill("REPORT-001")
        self.client.force_login(self.admin)

        response = self.client.get("/admin-portal/reports/?kind=sales&export=csv")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "text/csv; charset=utf-8")
        self.assertIn(b"Date,Bills,Sales", response.content)

    def test_purchase_receipt_updates_stock_and_supplier_outstanding(self):
        purchase = receive_purchase(
            supplier=self.supplier, invoice_no="SUP-001", purchase_date="2026-09-29",
            items=[{"variant_id": self.variant.pk, "qty": "2", "unit_cost": "10", "tax_percent": "10"}],
            paid_amount="2", user=self.admin,
        )

        self.variant.refresh_from_db()
        self.assertEqual(purchase.total, Decimal("22.00"))
        self.assertEqual(self.variant.stock_qty, Decimal("7.000"))
        self.assertEqual(supplier_balance(self.supplier), Decimal("40.00"))
        self.assertEqual(StockMovement.objects.get(ref_type="PURCHASE", ref_id=str(purchase.pk)).qty, Decimal("2"))

    def test_supplier_payment_reduces_outstanding_and_posts_ledger(self):
        receive_purchase(
            supplier=self.supplier, invoice_no="SUP-002", purchase_date="2026-09-29",
            items=[{"variant_id": self.variant.pk, "qty": "1", "unit_cost": "10", "tax_percent": "0"}],
            user=self.admin,
        )

        payment = collect_supplier_payment(
            supplier=self.supplier, amount="5", mode="CASH", date="2026-09-29", note="Part payment", user=self.admin,
        )

        self.assertEqual(payment.amount, Decimal("5"))
        self.assertEqual(supplier_balance(self.supplier), Decimal("25.00"))
        self.assertEqual(LedgerEntry.objects.filter(ref_type="SUPPLIER_PAYMENT", ref_id=str(payment.pk)).count(), 2)

    def test_return_restock_is_transactional_and_prevents_over_return(self):
        self.variant.stock_qty = Decimal("3")
        self.variant.save(update_fields=["stock_qty"])
        bill = self._bill("RETURN-001")

        sale_return = process_return(
            bill=bill, items=[{"bill_item_id": bill.items.get().pk, "qty": "1", "restock": True}],
            reason="Size exchange", refund_mode="CASH", user=self.admin,
        )

        self.variant.refresh_from_db()
        bill.refresh_from_db()
        self.assertEqual(sale_return.refund_amount, Decimal("105.00"))
        self.assertEqual(self.variant.stock_qty, Decimal("4.000"))
        self.assertEqual(bill.status, Bill.Status.PARTIALLY_RETURNED)
        with self.assertRaisesMessage(ValidationError, "Return quantity exceeds"):
            process_return(
                bill=bill, items=[{"bill_item_id": bill.items.get().pk, "qty": "2", "restock": True}],
                reason="Excess quantity", refund_mode="CASH", user=self.admin,
            )

    def test_void_restores_stock_and_posts_reversing_ledger(self):
        self.variant.stock_qty = Decimal("3")
        self.variant.save(update_fields=["stock_qty"])
        bill = self._bill("VOID-001")
        LedgerEntry.objects.create(date="2026-09-29", account_type=LedgerEntry.AccountType.SALES,
                                   ref_type="BILL", ref_id=str(bill.pk), credit=bill.grand_total)

        void_bill(bill=bill, reason="Duplicate sale", user=self.admin)

        bill.refresh_from_db()
        self.variant.refresh_from_db()
        reversal = LedgerEntry.objects.get(ref_type="BILL_VOID", ref_id=str(bill.pk))
        self.assertEqual(bill.status, Bill.Status.VOID)
        self.assertEqual(self.variant.stock_qty, Decimal("5.000"))
        self.assertEqual(reversal.debit, bill.grand_total)

    def test_collect_bill_balance_updates_bill_customer_and_ledger(self):
        bill = self._bill("DUE-001", qty=Decimal("1"), total=Decimal("100"), paid=Decimal("50"), balance=Decimal("50"))
        bill.items.update(line_total=Decimal("100"))

        payment = collect_bill_balance(bill=bill, amount="20", mode=Payment.Mode.UPI, reference="UPI-1", user=self.admin)

        bill.refresh_from_db()
        self.customer.refresh_from_db()
        self.assertEqual(payment.amount, Decimal("20"))
        self.assertEqual(bill.balance, Decimal("30"))
        self.assertEqual(bill.paid_amount, Decimal("70"))
        self.assertEqual(self.customer.credit_balance, Decimal("30"))
        self.assertEqual(LedgerEntry.objects.filter(ref_type="BILL_BALANCE", ref_id=str(bill.pk)).count(), 2)