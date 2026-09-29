from decimal import Decimal

from django.core.exceptions import ValidationError
from django.test import TestCase

from apps.accounts.models import User
from apps.billing.models import Bill, Payment
from apps.billing.services import calculate_totals, checkout
from apps.catalog.models import Category, Product, ProductVariant
from apps.customers.models import Customer
from apps.inventory.models import StockMovement
from apps.ledger.models import LedgerEntry


class BillCalculationTests(TestCase):
    def test_exclusive_tax_and_percent_discount(self):
        result = calculate_totals(
            [{"qty": "1", "unit_price": "100", "tax_percent": "5", "discount": "0"}],
            discount_type=Bill.DiscountType.PERCENT,
            discount_value=Decimal("10"),
        )

        self.assertEqual(result["subtotal"], Decimal("100.00"))
        self.assertEqual(result["discount_amount"], Decimal("10.00"))
        self.assertEqual(result["tax_amount"], Decimal("4.50"))
        self.assertEqual(result["grand_total"], Decimal("94.50"))

    def test_inclusive_tax_is_extracted_from_price(self):
        result = calculate_totals(
            [{"qty": "1", "unit_price": "105", "tax_percent": "5", "discount": "0"}],
            tax_inclusive=True,
        )

        self.assertEqual(result["subtotal"], Decimal("100.00"))
        self.assertEqual(result["tax_amount"], Decimal("5.00"))
        self.assertEqual(result["grand_total"], Decimal("105.00"))

    def test_staff_discount_cap_includes_item_and_bill_discounts(self):
        with self.assertRaisesMessage(ValidationError, "Discount exceeds your maximum of 10%"):
            calculate_totals(
                [{"qty": "1", "unit_price": "100", "tax_percent": "0", "discount": "5"}],
                discount_type=Bill.DiscountType.AMOUNT,
                discount_value=Decimal("6"),
                max_discount_percent=Decimal("10"),
                can_discount=True,
            )

    def test_discount_cap_error_includes_bill_specific_currency_limit(self):
        with self.assertRaises(ValidationError) as raised:
            calculate_totals(
                [{"qty": "1", "unit_price": "249", "tax_percent": "5", "discount": "0"}],
                discount_value=Decimal("32"),
                max_discount_percent=Decimal("10"),
                can_discount=True,
            )

        self.assertEqual(
            raised.exception.messages,
            ["Discount exceeds your maximum of 10% (₹24.90 on this bill)."],
        )


class CheckoutTests(TestCase):
    def setUp(self):
        self.category = Category.objects.create(name="Fabrics")
        self.product = Product.objects.create(
            name="Cotton", sku="FAB-001", category=self.category, unit=Product.Unit.METER,
            cost_price=Decimal("50"), selling_price=Decimal("100"), mrp=Decimal("120"),
            tax_percent=Decimal("5"),
        )
        self.variant = ProductVariant.objects.create(product=self.product, sku="FAB-001-STD", stock_qty=Decimal("5"))
        self.staff = User.objects.create_user(
            email="cashier@example.com", password="Strong-password-123", name="Cashier",
            role=User.Role.STAFF, permissions={"can_give_discount": True, "max_discount_percent": 10},
        )
        self.customer = Customer.objects.create(name="Buyer", phone="9000000000")

    def test_checkout_deducts_stock_records_payment_and_returns_cash_change(self):
        result = checkout(
            user=self.staff,
            cart=[{"variant_id": self.variant.pk, "qty": "2", "discount": "0"}],
            payments=[{"mode": Payment.Mode.CASH, "amount": "500"}],
            customer=self.customer,
        )

        bill = result["bill"]
        self.variant.refresh_from_db()
        self.assertEqual(bill.grand_total, Decimal("210.00"))
        self.assertEqual(bill.paid_amount, Decimal("210.00"))
        self.assertEqual(bill.balance, Decimal("0.00"))
        self.assertEqual(result["change_due"], Decimal("290.00"))
        self.assertEqual(self.variant.stock_qty, Decimal("3.000"))
        self.assertEqual(StockMovement.objects.get(ref_id=str(bill.pk)).qty, Decimal("-2.000"))
        self.assertEqual(LedgerEntry.objects.filter(ref_id=str(bill.pk)).count(), 2)

    def test_checkout_rejects_discount_over_staff_limit_without_mutation(self):
        with self.assertRaisesMessage(ValidationError, "Discount exceeds your maximum of 10%"):
            checkout(
                user=self.staff,
                cart=[{"variant_id": self.variant.pk, "qty": "1", "discount": "0"}],
                payments=[{"mode": Payment.Mode.CASH, "amount": "100"}],
                customer=self.customer,
                discount_type=Bill.DiscountType.AMOUNT,
                discount_value="10.01",
            )

        self.variant.refresh_from_db()
        self.assertEqual(self.variant.stock_qty, Decimal("5.000"))
        self.assertEqual(Bill.objects.count(), 0)

    def test_checkout_rejects_insufficient_stock(self):
        with self.assertRaisesMessage(ValidationError, "Not enough stock"):
            checkout(
                user=self.staff,
                cart=[{"variant_id": self.variant.pk, "qty": "6", "discount": "0"}],
                payments=[{"mode": Payment.Mode.CASH, "amount": "1000"}],
            )

        self.assertEqual(Bill.objects.count(), 0)