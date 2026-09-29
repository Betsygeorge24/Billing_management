from django.core.management import call_command
from django.test import TestCase
from django.utils import timezone

from apps.accounts.models import User
from apps.billing.models import Bill
from apps.catalog.models import Product, ProductVariant
from apps.ledger.models import Expense, LedgerEntry
from apps.sales_returns.models import SaleReturn
from apps.suppliers.models import Purchase, Supplier


class SeedDataTests(TestCase):
    def test_seed_data_populates_demo_shop_and_is_idempotent(self):
        call_command("seed_data", verbosity=0)

        self.assertGreaterEqual(Product.objects.count(), 40)
        self.assertGreaterEqual(ProductVariant.objects.count(), 60)
        self.assertEqual(Purchase.objects.filter(invoice_no__startswith="SEED-PO-").count(), 3)
        self.assertEqual(Bill.objects.filter(bill_no__startswith="DEMO-").count(), 60)
        self.assertEqual(SaleReturn.objects.filter(bill__bill_no__in=[
            f"DEMO-{timezone.localdate().year}-{number:04d}" for number in (3, 17, 31, 48)
        ]).count(), 4)
        self.assertEqual(Expense.objects.filter(note__startswith="SEED:").count(), 8)
        self.assertEqual(User.objects.filter(email__in=["admin@pos.com", "staff@pos.com", "staff2@pos.com"]).count(), 3)
        self.assertGreater(LedgerEntry.objects.count(), 100)
        self.assertEqual(Supplier.objects.filter(is_active=True).count(), 5)

        stocks_before = list(ProductVariant.objects.order_by("pk").values_list("pk", "stock_qty"))
        ledger_count = LedgerEntry.objects.count()
        bill_count = Bill.objects.count()
        purchase_count = Purchase.objects.count()
        return_count = SaleReturn.objects.count()
        expense_count = Expense.objects.count()

        call_command("seed_data", verbosity=0)

        self.assertEqual(list(ProductVariant.objects.order_by("pk").values_list("pk", "stock_qty")), stocks_before)
        self.assertEqual(LedgerEntry.objects.count(), ledger_count)
        self.assertEqual(Bill.objects.count(), bill_count)
        self.assertEqual(Purchase.objects.count(), purchase_count)
        self.assertEqual(SaleReturn.objects.count(), return_count)
        self.assertEqual(Expense.objects.count(), expense_count)