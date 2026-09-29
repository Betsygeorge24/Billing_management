from datetime import datetime, time, timedelta
from decimal import Decimal

from django.core.management.base import BaseCommand
from django.db import transaction
from django.db.models import F
from django.utils import timezone

from apps.accounts.models import User
from apps.audit.models import AuditLog
from apps.billing.models import Bill, BillItem, Payment
from apps.billing.services import calculate_totals
from apps.catalog.models import Category, Product, ProductVariant
from apps.customers.models import Customer
from apps.inventory.models import StockMovement
from apps.ledger.models import Expense, LedgerEntry
from apps.sales_returns.models import SaleReturn
from apps.sales_returns.services import process_return
from apps.shop_settings.models import ShopSettings
from apps.suppliers.models import Supplier
from apps.suppliers.services import receive_purchase


class Command(BaseCommand):
    help = "Create idempotent POS demo users, catalog, purchases, sales, returns and finance history."

    category_names = ("Sarees", "Shirts", "Trousers", "Kurtas", "Fabrics by meter", "Kids wear")

    def handle(self, *args, **options):
        del args, options
        with transaction.atomic():
            admin = self._user("admin@pos.com", "Admin@123", "POS Administrator", User.Role.ADMIN, True, True, {})
            cashier = self._user("staff@pos.com", "Staff@123", "Primary Cashier", User.Role.STAFF, False, False, {
                "can_give_discount": True, "max_discount_percent": 10, "can_process_return": True,
                "can_view_own_reports": True, "can_use_customer_credit": True,
            })
            second_cashier = self._user("staff2@pos.com", "Staff@123", "Second Cashier", User.Role.STAFF, False, False, {
                "can_give_discount": False, "max_discount_percent": 0, "can_process_return": False,
                "can_view_own_reports": False, "can_use_customer_credit": False,
            })
            categories = {name: Category.objects.get_or_create(name=name)[0] for name in self.category_names}
            self._sample_products(categories)
            suppliers = self._suppliers()
            customers = self._customers()
            settings = ShopSettings.get_solo()
            if settings.shop_name == "POS Billing":
                settings.shop_name = "Thread & Till Demo Shop"
                settings.address = "12 Market Road, Bengaluru"
                settings.phone = "+91 98765 43210"
                settings.gstin = "29ABCDE1234F1Z5"
                settings.invoice_prefix = "INV"
                settings.footer_note = "Thank you for shopping with Thread & Till."
                settings.default_tax = Decimal("5.00")
                settings.paper_size = "80MM"
                settings.save()
            self._purchases(suppliers, admin)
            self._sales(cashier, second_cashier, customers)
            self._returns(cashier)
            self._expenses(admin)
        counts = {
            "products": Product.objects.count(),
            "variants": ProductVariant.objects.count(),
            "purchases": sum(supplier.purchases.count() for supplier in suppliers),
            "demo_bills": Bill.objects.filter(bill_no__startswith=f"DEMO-{timezone.localdate().year}-").count(),
            "returns": SaleReturn.objects.filter(bill__bill_no__in=[
                f"DEMO-{timezone.localdate().year}-{number:04d}" for number in (3, 17, 31, 48)
            ]).count(),
            "expenses": Expense.objects.filter(note__startswith="SEED:").count(),
        }
        self.stdout.write(self.style.SUCCESS(f"Demo data ready: {counts}"))

    def _user(self, email, password, name, role, is_staff, is_superuser, permissions):
        user, created = User.objects.get_or_create(email=email, defaults={
            "name": name, "role": role, "is_staff": is_staff,
            "is_superuser": is_superuser, "permissions": permissions,
        })
        if created:
            user.set_password(password)
            user.save(update_fields=["password"])
        return user

    def _sample_products(self, categories):
        catalog = [
            ("Sarees", "Handloom cotton saree", "SAREE-001", "PCS", "1450", "2499", "2999", [("Free size", "Indigo", 24)]),
            ("Shirts", "Linen blend shirt", "SHIRT-001", "PCS", "420", "799", "999", [("S", "White", 18), ("M", "White", 22), ("L", "White", 18)]),
            ("Trousers", "Straight fit trouser", "TROUSER-001", "PCS", "610", "1199", "1499", [("30", "Charcoal", 14), ("32", "Charcoal", 20), ("34", "Charcoal", 16)]),
            ("Kurtas", "Printed cotton kurta", "KURTA-001", "PCS", "730", "1399", "1699", [("M", "Rust", 16), ("L", "Rust", 18), ("XL", "Rust", 14)]),
            ("Fabrics by meter", "Everyday cotton fabric", "FABRIC-001", "METER", "115", "249", "299", [("44 inch", "Bottle green", 110)]),
            ("Kids wear", "Everyday kids set", "KIDS-001", "PCS", "245", "499", "649", [("4-5Y", "Yellow", 16), ("6-7Y", "Yellow", 18)]),
        ]
        for category_name, name, sku, unit, cost, price, mrp, variants in catalog:
            self._product(categories[category_name], name, sku, unit, cost, price, mrp, variants)

        category_specs = [
            ("Sarees", "SAREE", "Handwoven saree", Product.Unit.PCS, 980, 1899, 2299, [("Free size", "Maroon"), ("Free size", "Teal")]),
            ("Shirts", "SHIRT", "Casual cotton shirt", Product.Unit.PCS, 330, 699, 899, [("M", "Blue"), ("L", "Blue")]),
            ("Trousers", "PANT", "Cotton blend trouser", Product.Unit.PCS, 490, 999, 1299, [("30", "Navy"), ("32", "Navy")]),
            ("Kurtas", "KURTA", "Daily wear kurta", Product.Unit.PCS, 540, 1099, 1399, [("M", "Indigo"), ("L", "Indigo")]),
            ("Fabrics by meter", "FAB", "Printed dress fabric", Product.Unit.METER, 95, 199, 249, [("44 inch", "Floral")]),
            ("Kids wear", "KIDS", "Kids cotton outfit", Product.Unit.PCS, 190, 399, 549, [("5-6Y", "Coral"), ("7-8Y", "Coral")]),
        ]
        for category_name, prefix, label, unit, cost, price, mrp, variants in category_specs:
            for index in range(1, 7):
                sku = f"DEMO-{prefix}-{index:02d}"
                adjusted_cost = Decimal(cost + (index * 7))
                adjusted_price = Decimal(price + (index * 13))
                adjusted_mrp = Decimal(mrp + (index * 13))
                name = f"{label} {index:02d}"
                stock_variants = [(size, f"{color} {index}", 12 + ((index * 3) % 12)) for size, color in variants]
                self._product(categories[category_name], name, sku, unit, adjusted_cost, adjusted_price, adjusted_mrp, stock_variants)

    def _product(self, category, name, sku, unit, cost, price, mrp, variants):
        product, _ = Product.objects.get_or_create(sku=sku, defaults={
            "name": name, "category": category, "unit": unit,
            "cost_price": Decimal(str(cost)), "selling_price": Decimal(str(price)),
            "mrp": Decimal(str(mrp)), "tax_percent": Decimal("5.00"),
            "min_stock_alert": Decimal("3.000"), "is_active": True,
        })
        for size, color, stock in variants:
            slug = "-".join("".join(character for character in value.upper() if character.isalnum()) for value in (size, color))
            variant_sku = f"{sku}-{slug}"
            ProductVariant.objects.get_or_create(product=product, size=size, color=color, defaults={
                "sku": variant_sku, "barcode": variant_sku.replace("-", "")[:80],
                "stock_qty": Decimal(str(stock)),
            })

    def _suppliers(self):
        records = [
            ("Kaveri Fabrics Wholesale", "9845011101", "orders@kaveritextiles.example", "29AABCK1001F1Z2"),
            ("Southern Loom House", "9845011102", "sales@southernloom.example", "29AABCS2002F1Z3"),
            ("Metro Apparel Distributors", "9845011103", "trade@metroapparel.example", "29AABCM3003F1Z4"),
            ("Bharat Kidswear Supply", "9845011104", "hello@bharatkids.example", "29AABCB4004F1Z5"),
            ("City Textile Mills", "9845011105", "billing@citymills.example", "29AABCC5005F1Z6"),
        ]
        suppliers = []
        for name, phone, email, gstin in records:
            supplier, _ = Supplier.objects.get_or_create(name=name, defaults={
                "phone": phone, "email": email, "address": "Bengaluru, Karnataka", "gstin": gstin,
            })
            suppliers.append(supplier)
        return suppliers

    def _customers(self):
        records = [
            ("Aarav Mehta", "9000000001", "Bengaluru"), ("Diya Nair", "9000000002", "Mysuru"),
            ("Kabir Shah", "9000000003", "Bengaluru"), ("Ananya Rao", "9000000004", "Tumakuru"),
            ("Ishaan Gupta", "9000000005", "Bengaluru"), ("Meera Iyer", "9000000006", "Mandya"),
            ("Vivaan Das", "9000000007", "Bengaluru"), ("Sara Khan", "9000000008", "Hassan"),
            ("Rohan Joshi", "9000000009", "Bengaluru"), ("Tara Menon", "9000000010", "Mangaluru"),
        ]
        customers = []
        for name, phone, city in records:
            customer, _ = Customer.objects.get_or_create(phone=phone, defaults={
                "name": name, "address": f"{city}, Karnataka",
            })
            customers.append(customer)
        return customers

    def _purchases(self, suppliers, user):
        today = timezone.localdate()
        purchases = [
            (0, 28, [("SHIRT-001", "M", "White", "12", "410", "5"), ("KURTA-001", "L", "Rust", "8", "700", "5")], "2200"),
            (1, 17, [("SAREE-001", "Free size", "Indigo", "5", "1400", "5"), ("FABRIC-001", "44 inch", "Bottle green", "25", "110", "5")], "1500"),
            (2, 7, [("TROUSER-001", "32", "Charcoal", "10", "590", "5"), ("KIDS-001", "4-5Y", "Yellow", "12", "235", "5")], "0"),
        ]
        for purchase_index, (supplier_index, days_ago, lines, paid) in enumerate(purchases, 1):
            invoice = f"SEED-PO-{timezone.localdate().year}-{purchase_index:03d}"
            if suppliers[supplier_index].purchases.filter(invoice_no=invoice).exists():
                continue
            items = []
            for product_sku, size, color, qty, cost, tax in lines:
                variant = ProductVariant.objects.get(product__sku=product_sku, size=size, color=color)
                items.append({"variant_id": variant.pk, "qty": qty, "unit_cost": cost, "tax_percent": tax})
            receive_purchase(
                supplier=suppliers[supplier_index], invoice_no=invoice,
                purchase_date=today - timedelta(days=days_ago), items=items, paid_amount=paid, user=user,
            )

    def _sales(self, cashier, second_cashier, customers):
        today = timezone.localdate()
        year = today.year
        bill_prefix = f"DEMO-{year}-"
        if Bill.objects.filter(bill_no__startswith=bill_prefix).count() >= 60:
            return
        variants = list(ProductVariant.objects.select_related("product").filter(product__is_active=True).order_by("sku"))
        if not variants:
            return
        modes = (Payment.Mode.CASH, Payment.Mode.UPI, Payment.Mode.CARD)
        for index in range(1, 61):
            bill_no = f"{bill_prefix}{index:04d}"
            if Bill.objects.filter(bill_no=bill_no).exists():
                continue
            variant = variants[((index - 1) * 7) % len(variants)]
            quantity = Decimal(1 + (index % 3 == 0))
            price = variant.price_override if variant.price_override is not None else variant.product.selling_price
            totals = calculate_totals([{
                "qty": quantity, "unit_price": price,
                "tax_percent": variant.product.tax_percent, "discount": Decimal("0"),
            }])
            customer = None if index % 4 == 0 else customers[(index * 3) % len(customers)]
            credit_due = Decimal("0.00")
            if customer and index % 9 == 0:
                credit_due = (totals["grand_total"] * Decimal("0.20")).quantize(Decimal("0.01"))
            paid = totals["grand_total"] - credit_due
            staff = cashier if index % 4 else second_cashier
            bill = Bill.objects.create(
                bill_no=bill_no, customer=customer, staff=staff,
                subtotal=totals["subtotal"], discount_type=Bill.DiscountType.AMOUNT,
                discount_value=Decimal("0"), discount_amount=totals["discount_amount"],
                tax_amount=totals["tax_amount"], grand_total=totals["grand_total"],
                paid_amount=paid, balance=credit_due, status=Bill.Status.COMPLETED,
            )
            bill_date = today - timedelta(days=((index - 1) * 29 // 59))
            bill_datetime = timezone.make_aware(datetime.combine(bill_date, time(10 + (index % 9), (index * 11) % 60)))
            Bill.objects.filter(pk=bill.pk).update(created_at=bill_datetime)
            line = totals["lines"][0]
            BillItem.objects.create(
                bill=bill, product=variant.product, variant=variant,
                name_snapshot=variant.product.name + (f" ({variant.size})" if variant.size else "") + (f" / {variant.color}" if variant.color else ""),
                sku_snapshot=variant.sku, qty=quantity, unit_price=price,
                discount=line["discount"], tax_percent=line["tax_percent"],
                tax_amount=line["tax_amount"], line_total=line["line_total"],
            )
            ProductVariant.objects.filter(pk=variant.pk).update(stock_qty=F("stock_qty") - quantity)
            StockMovement.objects.create(
                variant=variant, type=StockMovement.MovementType.SALE, qty=-quantity,
                ref_type="BILL", ref_id=str(bill.pk), note=f"Seed demo sale {bill_no}", user=staff,
            )
            LedgerEntry.objects.create(
                date=bill_date, account_type=LedgerEntry.AccountType.SALES, ref_type="BILL",
                ref_id=str(bill.pk), credit=bill.grand_total, description=f"Sale {bill_no}",
            )
            if paid:
                mode = modes[index % len(modes)]
                Payment.objects.create(bill=bill, mode=mode, amount=paid, reference=f"SEED-PAY-{index:04d}")
                payment_account = LedgerEntry.AccountType.CASH if mode == Payment.Mode.CASH else LedgerEntry.AccountType.BANK
                LedgerEntry.objects.create(
                    date=bill_date, account_type=payment_account, ref_type="BILL", ref_id=str(bill.pk),
                    debit=paid, description=f"{mode.title()} payment for {bill_no}",
                )
            if credit_due:
                Payment.objects.create(bill=bill, mode=Payment.Mode.CREDIT, amount=credit_due)
                LedgerEntry.objects.create(
                    date=bill_date, account_type=LedgerEntry.AccountType.CUSTOMER, ref_type="BILL",
                    ref_id=str(bill.pk), debit=credit_due, description=f"Customer credit for {bill_no}",
                )
                Customer.objects.filter(pk=customer.pk).update(credit_balance=F("credit_balance") + credit_due)
            AuditLog.objects.get_or_create(
                action="bill_created", object_type="Bill", object_id=str(bill.pk),
                defaults={"user": staff, "after_data": {"bill_no": bill_no, "grand_total": str(bill.grand_total), "seed": True}},
            )

    def _returns(self, cashier):
        today = timezone.localdate()
        return_specs = ((3, "Size exchange"), (17, "Customer changed mind"), (31, "Quality inspection"), (48, "Wrong color selected"))
        for bill_number, reason in return_specs:
            bill_no = f"DEMO-{today.year}-{bill_number:04d}"
            bill = Bill.objects.filter(bill_no=bill_no).first()
            if not bill or bill.returns.exists():
                continue
            bill_item = bill.items.first()
            if not bill_item:
                continue
            quantity = min(Decimal("1"), bill_item.qty)
            process_return(
                bill=bill, items=[{"bill_item_id": bill_item.pk, "qty": quantity, "restock": bill_number != 31}],
                reason=reason, refund_mode="CASH", user=cashier,
            )

    def _expenses(self, user):
        today = timezone.localdate()
        expenses = [
            ("Shop rent", "45000", "Monthly shop rent", "CASH"),
            ("Utilities", "3850", "Electricity and water", "UPI"),
            ("Packaging", "1275", "Carry bags and invoice rolls", "CASH"),
            ("Transport", "2200", "Supplier delivery charges", "CARD"),
            ("Staff refreshments", "640", "Tea and refreshments", "CASH"),
            ("Repairs", "1800", "Alteration machine service", "UPI"),
            ("Marketing", "3200", "Local festival promotion", "CARD"),
            ("Internet", "999", "Monthly internet service", "UPI"),
        ]
        for index, (category, amount, note, mode) in enumerate(expenses):
            expense_date = today - timedelta(days=index * 3)
            seed_note = f"SEED: {note}"
            if Expense.objects.filter(category=category, date=expense_date, note=seed_note).exists():
                continue
            Expense.objects.create(category=category, amount=Decimal(amount), date=expense_date, note=seed_note, mode=mode)
            LedgerEntry.objects.create(
                date=expense_date, account_type=LedgerEntry.AccountType.EXPENSE, ref_type="EXPENSE",
                ref_id=f"SEED-{index + 1:03d}", debit=Decimal(amount), description=f"{category}: {seed_note}",
            )
            cash_account = LedgerEntry.AccountType.CASH if mode == "CASH" else LedgerEntry.AccountType.BANK
            LedgerEntry.objects.create(
                date=expense_date, account_type=cash_account, ref_type="EXPENSE",
                ref_id=f"SEED-{index + 1:03d}", credit=Decimal(amount), description=f"{mode.title()} expense: {category}",
            )
            AuditLog.objects.get_or_create(
                action="expense_created", object_type="Expense", object_id=f"SEED-{index + 1:03d}",
                defaults={"user": user, "after_data": {"category": category, "amount": amount, "seed": True}},
            )