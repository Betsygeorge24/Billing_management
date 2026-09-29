import csv
from decimal import Decimal

from django.contrib import messages
from django.core.exceptions import ValidationError
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import Count, Q, Sum
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone, dateparse
from django.views.decorators.http import require_POST

from apps.accounts.models import User
from apps.audit.models import AuditLog
from apps.billing.models import Bill, BillItem
from apps.billing.services import collect_bill_balance, void_bill
from apps.catalog.models import Category, Product, ProductVariant
from apps.catalog.services import adjust_stock
from apps.core.access import admin_required
from apps.core.admin_forms import (
    BalanceCollectionForm, CategoryForm, CustomerForm, CustomerPaymentForm,
    ExpenseForm, ProductForm, ProductVariantFormSet, PurchaseForm, PurchaseItemFormSet,
    ReturnHeaderForm, ReturnItemForm, ShopSettingsForm, StaffForm, StockAdjustmentForm,
    SupplierForm, SupplierPaymentForm, VoidBillForm,
)
from apps.customers.models import Customer
from apps.customers.services import collect_customer_payment
from apps.ledger.models import Expense, LedgerEntry
from apps.ledger.services import record_expense
from apps.sales_returns.models import SaleReturn
from apps.sales_returns.services import process_return
from apps.shop_settings.models import ShopSettings
from apps.suppliers.models import Purchase, Supplier
from apps.suppliers.services import collect_supplier_payment, receive_purchase, supplier_balance


def _is_mobile_admin(request):
    return request.path.startswith("/m/admin/")


def _mobile_route_name(name):
    return name.replace("admin_", "mobile_admin_", 1)


def _admin_redirect(request, name, **kwargs):
    if _is_mobile_admin(request):
        name = _mobile_route_name(name)
    return redirect(name, **kwargs)


def _admin_render(request, desktop_template, mobile_template, context):
    return render(request, mobile_template if _is_mobile_admin(request) else desktop_template, context)


def _list(request, *, title, eyebrow, columns, rows, description="", actions=None, stats=None):
    mobile = _is_mobile_admin(request)
    page = Paginator(rows, 20 if mobile else 30).get_page(request.GET.get("page"))
    normalized_rows = []
    for row in page.object_list:
        row = dict(row)
        row["labelled_cells"] = list(zip(columns, row.get("cells", [])))
        if mobile and row.get("url", "").startswith("/admin-portal/"):
            row["url"] = row["url"].replace("/admin-portal/", "/m/admin/", 1)
        normalized_rows.append(row)
    context = {
        "title": title, "eyebrow": eyebrow, "columns": columns, "page_obj": page,
        "rows": normalized_rows, "description": description,
        "actions": [(label, _mobile_route_name(url_name) if mobile else url_name) for label, url_name in (actions or [])], "stats": stats or [],
        "bill_statuses": Bill.Status.choices if title == "Bills & transactions" else [],
        "mobile_admin": mobile,
    }
    return _admin_render(request, "admin_portal/list.html", "mobile/admin/list.html", context)


def _form(request, *, title, eyebrow, form, submit_label="Save", formset=None, help_text="", **extra_context):
    context = {
        "title": title, "eyebrow": eyebrow, "form": form, "formset": formset,
        "submit_label": submit_label, "help_text": help_text,
    }
    context.update(extra_context)
    context["mobile_admin"] = _is_mobile_admin(request)
    return _admin_render(request, "admin_portal/form.html", "mobile/admin/form.html", context)


@admin_required
def products(request):
    query = request.GET.get("q", "").strip()
    products_qs = Product.objects.select_related("category").prefetch_related("variants")
    if query:
        products_qs = products_qs.filter(Q(name__icontains=query) | Q(sku__icontains=query) | Q(barcode__icontains=query))
    products_qs = products_qs.order_by("name")
    rows = [{"cells": [p.name, p.sku, p.category.name, p.unit, f"₹{p.selling_price:.2f}", "Active" if p.is_active else "Inactive", sum((v.stock_qty for v in p.variants.all()), Decimal("0"))], "url": f"/admin-portal/products/{p.pk}/", "action": "Edit"} for p in products_qs]
    response = _list(request, title="Products", eyebrow="CATALOG", columns=["Product", "SKU", "Category", "Unit", "Price", "State", "Stock"], rows=rows,
                     description="Manage catalog details and variants. Adjust stock with a recorded reason.", actions=[("Add product", "admin_product_create"), ("Categories", "admin_categories")])
    return response


@admin_required
def product_create(request):
    product = Product()
    form = ProductForm(request.POST or None, request.FILES or None, instance=product)
    formset = ProductVariantFormSet(request.POST or None, instance=product)
    if request.method == "POST" and form.is_valid() and formset.is_valid():
        with transaction.atomic():
            saved = form.save()
            formset.instance = saved
            formset.save()
            AuditLog.objects.create(user=request.user, action="product_created", object_type="Product", object_id=str(saved.pk))
        messages.success(request, "Product created.")
        return _admin_redirect(request, "admin_products")
    return _form(request, title="Add product", eyebrow="CATALOG", form=form, formset=formset,
                 submit_label="Create product", product=product, variants=[])


@admin_required
def product_edit(request, product_id):
    product = get_object_or_404(Product, pk=product_id)
    form = ProductForm(request.POST or None, request.FILES or None, instance=product)
    formset = ProductVariantFormSet(request.POST or None, instance=product)
    if request.method == "POST" and form.is_valid() and formset.is_valid():
        with transaction.atomic():
            form.save()
            formset.save()
            AuditLog.objects.create(user=request.user, action="product_updated", object_type="Product", object_id=str(product.pk))
        messages.success(request, "Product updated.")
        return _admin_redirect(request, "admin_products")
    return _form(request, title=f"Edit {product.name}", eyebrow="CATALOG", form=form, formset=formset,
                 submit_label="Save changes", product=product, variants=product.variants.all())


@admin_required
@require_POST
def product_deactivate(request, product_id):
    product = get_object_or_404(Product, pk=product_id)
    product.is_active = False
    product.save(update_fields=["is_active", "updated_at"])
    AuditLog.objects.create(user=request.user, action="product_deactivated", object_type="Product", object_id=str(product.pk))
    messages.success(request, "Product deactivated; historical bills are unchanged.")
    return _admin_redirect(request, "admin_products")


@admin_required
def categories(request):
    form = CategoryForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, "Category saved.")
        return _admin_redirect(request, "admin_categories")
    qs = Category.objects.annotate(product_count=Count("products")).order_by("name")
    rows = [{"cells": [category.name, category.product_count or 0, "Active" if category.is_active else "Inactive"]} for category in qs]
    return _admin_render(request, "admin_portal/categories.html", "mobile/admin/categories.html", {
        "title": "Categories", "eyebrow": "CATALOG", "columns": ["Category", "Products", "State"],
        "page_obj": Paginator(rows, 30).get_page(request.GET.get("page")), "form": form,
        "mobile_rows": [{"labelled_cells": list(zip(["Category", "Products", "State"], row["cells"]))} for row in rows],
    })


@admin_required
def stock_adjust(request, variant_id):
    variant = get_object_or_404(ProductVariant.objects.select_related("product"), pk=variant_id)
    form = StockAdjustmentForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        try:
            adjust_stock(variant_id=variant.pk, quantity_change=form.cleaned_data["quantity_change"], reason=form.cleaned_data["reason"], user=request.user)
        except ValidationError as error:
            form.add_error(None, error)
        else:
            messages.success(request, "Stock adjustment recorded.")
            return _admin_redirect(request, "admin_product_edit", product_id=variant.product_id)
    return _form(request, title=f"Adjust stock: {variant}", eyebrow="INVENTORY", form=form, submit_label="Record adjustment",
                 help_text=f"Current stock: {variant.stock_qty} {variant.product.get_unit_display()}")


@admin_required
def staff_list(request):
    query = request.GET.get("q", "").strip()
    staff = User.objects.filter(role=User.Role.STAFF)
    if query:
        staff = staff.filter(Q(name__icontains=query) | Q(email__icontains=query) | Q(phone__icontains=query))
    rows = [{"cells": [member.name, member.email, member.phone or "—", f"{member.permissions.get('max_discount_percent', 0)}%", "Active" if member.is_active else "Inactive"], "url": f"/admin-portal/staff/{member.pk}/", "action": "Edit"} for member in staff]
    return _list(request, title="Staff", eyebrow="TEAM", columns=["Name", "Email", "Phone", "Discount cap", "State"], rows=rows,
                 description="Manage cashier accounts, discount limits, and operational permissions.", actions=[("Add staff", "admin_staff_create")])


@admin_required
def staff_create(request):
    form = StaffForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        member = form.save()
        AuditLog.objects.create(user=request.user, action="staff_created", object_type="User", object_id=str(member.pk))
        messages.success(request, "Staff account created.")
        return _admin_redirect(request, "admin_staff")
    return _form(request, title="Add staff account", eyebrow="TEAM", form=form, submit_label="Create account")


@admin_required
def staff_edit(request, user_id):
    member = get_object_or_404(User, pk=user_id, role=User.Role.STAFF)
    form = StaffForm(request.POST or None, instance=member)
    if request.method == "POST" and form.is_valid():
        form.save()
        AuditLog.objects.create(user=request.user, action="staff_updated", object_type="User", object_id=str(member.pk))
        messages.success(request, "Staff account updated.")
        return _admin_redirect(request, "admin_staff")
    return _form(request, title=f"Edit {member.name}", eyebrow="TEAM", form=form, submit_label="Save staff")


@admin_required
def suppliers(request):
    query = request.GET.get("q", "").strip()
    qs = Supplier.objects.all()
    if query:
        qs = qs.filter(Q(name__icontains=query) | Q(phone__icontains=query) | Q(gstin__icontains=query))
    rows = [{"cells": [s.name, s.phone or "—", s.gstin or "—", f"₹{supplier_balance(s):.2f}", "Active" if s.is_active else "Inactive"], "url": f"/admin-portal/suppliers/{s.pk}/", "action": "Open"} for s in qs]
    return _list(request, title="Suppliers", eyebrow="PURCHASING", columns=["Supplier", "Phone", "GSTIN", "Outstanding", "State"], rows=rows,
                 description="Supplier directory, purchase history, and payment collection.", actions=[("Add supplier", "admin_supplier_create"), ("Purchases", "admin_purchases")])


@admin_required
def supplier_create(request):
    form = SupplierForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        supplier = form.save()
        messages.success(request, "Supplier saved.")
        return _admin_redirect(request, "admin_supplier_detail", supplier_id=supplier.pk)
    return _form(request, title="Add supplier", eyebrow="PURCHASING", form=form)


@admin_required
def supplier_edit(request, supplier_id):
    supplier = get_object_or_404(Supplier, pk=supplier_id)
    form = SupplierForm(request.POST or None, instance=supplier)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, "Supplier updated.")
        return _admin_redirect(request, "admin_supplier_detail", supplier_id=supplier.pk)
    return _form(request, title=f"Edit {supplier.name}", eyebrow="PURCHASING", form=form)


@admin_required
def supplier_detail(request, supplier_id):
    supplier = get_object_or_404(Supplier, pk=supplier_id)
    form = SupplierPaymentForm(request.POST or None, initial={"date": timezone.localdate()})
    balance = supplier_balance(supplier)
    form.fields["amount"].widget.attrs["max"] = str(max(balance, Decimal("0")))
    if request.method == "POST" and form.is_valid():
        try:
            collect_supplier_payment(supplier=supplier, user=request.user, **form.cleaned_data)
        except ValidationError as error:
            form.add_error(None, error)
        else:
            messages.success(request, "Supplier payment recorded.")
            return _admin_redirect(request, "admin_supplier_detail", supplier_id=supplier.pk)
    context = {
        "supplier": supplier, "balance": balance, "form": form,
        "purchases": supplier.purchases.prefetch_related("items")[:20], "payments": supplier.payments.all()[:20],
    }
    return _admin_render(request, "admin_portal/supplier_detail.html", "mobile/admin/supplier_detail.html", context)


@admin_required
def purchase_list(request):
    qs = Purchase.objects.select_related("supplier").prefetch_related("items").order_by("-date", "-id")
    rows = [{"cells": [p.invoice_no, p.supplier.name, p.date, f"₹{p.total:.2f}", f"₹{p.paid_amount:.2f}", p.get_status_display()]} for p in qs]
    return _list(request, title="Purchases", eyebrow="PURCHASING", columns=["Invoice", "Supplier", "Date", "Total", "Paid", "Status"], rows=rows,
                 actions=[("Record purchase", "admin_purchase_create"), ("Suppliers", "admin_suppliers")])


@admin_required
def purchase_create(request):
    form = PurchaseForm(request.POST or None, initial={"date": timezone.localdate()})
    formset = PurchaseItemFormSet(request.POST or None, instance=Purchase())
    if request.method == "POST" and form.is_valid() and formset.is_valid():
        purchase_items = []
        for item in formset.cleaned_data:
            if item and not item.get("DELETE") and item.get("variant"):
                if item["variant"].product_id != item["product"].pk:
                    form.add_error(None, "Each variant must belong to its selected product.")
                    break
                purchase_items.append({"variant_id": item["variant"].pk, "qty": item["qty"], "unit_cost": item["unit_cost"], "tax_percent": item["tax_percent"]})
        if not form.errors and purchase_items:
            try:
                receive_purchase(supplier=form.cleaned_data["supplier"], invoice_no=form.cleaned_data["invoice_no"],
                                 purchase_date=form.cleaned_data["date"], items=purchase_items,
                                 paid_amount=request.POST.get("paid_amount", "0"), user=request.user)
            except ValidationError as error:
                form.add_error(None, error)
            else:
                messages.success(request, "Purchase received and stock updated.")
                return _admin_redirect(request, "admin_purchases")
        elif not purchase_items:
            form.add_error(None, "Add at least one purchase item.")
    return _form(request, title="Receive purchase", eyebrow="PURCHASING", form=form, formset=formset,
                 submit_label="Receive stock", help_text="Use one row per product variant. Paid amount is optional.")


@admin_required
def customers(request):
    query = request.GET.get("q", "").strip()
    qs = Customer.objects.all()
    if query:
        qs = qs.filter(Q(name__icontains=query) | Q(phone__icontains=query))
    rows = [{"cells": [c.name, c.phone or "—", c.gstin or "—", f"₹{c.credit_balance:.2f}", "Active" if c.is_active else "Inactive"], "url": f"/admin-portal/customers/{c.pk}/", "action": "Open"} for c in qs]
    return _list(request, title="Customers", eyebrow="CUSTOMERS", columns=["Name", "Phone", "GSTIN", "Outstanding", "State"], rows=rows,
                 actions=[("Add customer", "admin_customer_create")])


@admin_required
def customer_create(request):
    form = CustomerForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        customer = form.save()
        messages.success(request, "Customer saved.")
        return _admin_redirect(request, "admin_customer_detail", customer_id=customer.pk)
    return _form(request, title="Add customer", eyebrow="CUSTOMERS", form=form)


@admin_required
def customer_edit(request, customer_id):
    customer = get_object_or_404(Customer, pk=customer_id)
    form = CustomerForm(request.POST or None, instance=customer)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, "Customer updated.")
        return _admin_redirect(request, "admin_customer_detail", customer_id=customer.pk)
    return _form(request, title=f"Edit {customer.name}", eyebrow="CUSTOMERS", form=form)


@admin_required
def customer_detail(request, customer_id):
    customer = get_object_or_404(Customer, pk=customer_id)
    form = CustomerPaymentForm(request.POST or None, initial={"date": timezone.localdate()})
    if request.method == "POST" and form.is_valid():
        try:
            collect_customer_payment(customer=customer, user=request.user, **form.cleaned_data)
        except ValidationError as error:
            form.add_error(None, error)
        else:
            messages.success(request, "Customer payment collected.")
            return _admin_redirect(request, "admin_customer_detail", customer_id=customer.pk)
    bills = customer.bills.order_by("-created_at")[:30]
    context = {"customer": customer, "form": form, "bills": bills, "payments": customer.payments.all()[:20]}
    return _admin_render(request, "admin_portal/customer_detail.html", "mobile/admin/customer_detail.html", context)


@admin_required
def returns_list(request):
    qs = SaleReturn.objects.select_related("bill", "staff").prefetch_related("items").order_by("-created_at")
    rows = [{"cells": [r.return_no, r.bill.bill_no, r.created_at.strftime("%d/%m/%Y %H:%M"), r.staff.name, r.refund_mode, f"₹{r.refund_amount:.2f}"]} for r in qs]
    return _list(request, title="Returns", eyebrow="AFTER-SALES", columns=["Return", "Bill", "Date", "Processed by", "Refund", "Amount"], rows=rows,
                 description="Find a bill to validate quantities and issue a refund.", actions=[("Process return", "admin_return_create")])


@admin_required
def return_create(request):
    bill_no = request.GET.get("bill_no", "").strip()
    bill = Bill.objects.filter(bill_no=bill_no).first() if bill_no else None
    form = ReturnHeaderForm(request.POST or None, initial={"bill_no": bill_no})
    item_forms = []
    if bill:
        for item in bill.items.all():
            previously_returned = item.return_items.aggregate(total=Sum("qty"))["total"] or Decimal("0")
            item_forms.append({"item": item, "returned_qty": previously_returned, "remaining_qty": item.qty - previously_returned})
    if request.method == "POST" and form.is_valid():
        bill = Bill.objects.filter(bill_no=form.cleaned_data["bill_no"].strip()).first()
        if not bill:
            form.add_error("bill_no", "Bill number not found.")
        else:
            selections = []
            item_forms = []
            for bill_item in bill.items.all():
                line_form = ReturnItemForm(request.POST, prefix=f"item-{bill_item.pk}", initial={"bill_item_id": bill_item.pk})
                previously_returned = bill_item.return_items.aggregate(total=Sum("qty"))["total"] or Decimal("0")
                item_forms.append({"item": bill_item, "returned_qty": previously_returned, "remaining_qty": bill_item.qty - previously_returned, "form": line_form})
                if line_form.is_valid() and line_form.cleaned_data.get("qty"):
                    selections.append({"bill_item_id": bill_item.pk, "qty": line_form.cleaned_data["qty"], "restock": line_form.cleaned_data["restock"]})
            if selections and all(line["form"].is_valid() for line in item_forms):
                try:
                    process_return(bill=bill, items=selections, reason=form.cleaned_data["reason"], refund_mode=form.cleaned_data["refund_mode"], user=request.user)
                except ValidationError as error:
                    form.add_error(None, error)
                else:
                    messages.success(request, "Return processed and stock/ledger updated.")
                    return _admin_redirect(request, "admin_returns")
            else:
                form.add_error(None, "Enter a return quantity for at least one item and correct any item errors.")
    return _admin_render(request, "admin_portal/return_form_v2.html", "mobile/admin/return_form.html", {"form": form, "bill": bill, "item_forms": item_forms})


@admin_required
def bills(request):
    qs = Bill.objects.select_related("customer", "staff").order_by("-created_at")
    query = request.GET.get("q", "").strip()
    status = request.GET.get("status", "")
    start = dateparse.parse_date(request.GET.get("start", ""))
    end = dateparse.parse_date(request.GET.get("end", ""))
    if query:
        qs = qs.filter(Q(bill_no__icontains=query) | Q(customer__name__icontains=query) | Q(staff__name__icontains=query))
    if status in {value for value, _ in Bill.Status.choices}:
        qs = qs.filter(status=status)
    if start:
        qs = qs.filter(created_at__date__gte=start)
    if end:
        qs = qs.filter(created_at__date__lte=end)
    rows = [{"cells": [b.bill_no, b.created_at.strftime("%d/%m/%Y %H:%M"), b.customer.name if b.customer else "Walk-in", b.staff.name, f"₹{b.grand_total:.2f}", f"₹{b.balance:.2f}", b.get_status_display()], "url": f"/admin-portal/bills/{b.pk}/", "action": "Manage"} for b in qs]
    return _list(request, title="Bills & transactions", eyebrow="BILLING", columns=["Bill", "Date", "Customer", "Cashier", "Total", "Due", "Status"], rows=rows,
                 description="Inspect bills, collect pending balances, and void eligible transactions.")


@admin_required
def bill_detail(request, bill_id):
    bill = get_object_or_404(Bill.objects.select_related("customer", "staff").prefetch_related("items", "payments", "returns"), pk=bill_id)
    void_form = VoidBillForm(prefix="void")
    collection_form = BalanceCollectionForm(prefix="collect")
    return _admin_render(request, "admin_portal/bill_detail.html", "mobile/admin/bill_detail.html", {"bill": bill, "void_form": void_form, "collection_form": collection_form})


@admin_required
@require_POST
def bill_void(request, bill_id):
    bill = get_object_or_404(Bill, pk=bill_id)
    form = VoidBillForm(request.POST, prefix="void")
    if form.is_valid():
        try:
            void_bill(bill=bill, reason=form.cleaned_data["reason"], user=request.user)
        except ValidationError as error:
            messages.error(request, " ".join(error.messages))
        else:
            messages.success(request, "Bill voided and stock/ledger reversals recorded.")
    else:
        messages.error(request, "A reason is required to void a bill.")
    return _admin_redirect(request, "admin_bill_detail", bill_id=bill.pk)


@admin_required
@require_POST
def bill_collect(request, bill_id):
    bill = get_object_or_404(Bill, pk=bill_id)
    form = BalanceCollectionForm(request.POST, prefix="collect")
    if form.is_valid():
        try:
            collect_bill_balance(bill=bill, user=request.user, **form.cleaned_data)
        except ValidationError as error:
            messages.error(request, " ".join(error.messages))
        else:
            messages.success(request, "Bill balance collected.")
    else:
        messages.error(request, "Enter a valid collection amount and method.")
    return _admin_redirect(request, "admin_bill_detail", bill_id=bill.pk)


@admin_required
def ledger(request):
    qs = LedgerEntry.objects.all()
    start = request.GET.get("start", "")
    end = request.GET.get("end", "")
    account = request.GET.get("account", "")
    if start:
        qs = qs.filter(date__gte=start)
    if end:
        qs = qs.filter(date__lte=end)
    if account:
        qs = qs.filter(account_type=account)
    qs = qs.order_by("date", "id")
    debit = qs.aggregate(total=Sum("debit"))["total"] or Decimal("0")
    credit = qs.aggregate(total=Sum("credit"))["total"] or Decimal("0")
    running_balance = Decimal("0")
    rows = []
    for entry in qs:
        running_balance += entry.debit - entry.credit
        rows.append({"cells": [entry.date, entry.get_account_type_display(), entry.ref_type, entry.ref_id, entry.description, f"₹{entry.debit:.2f}", f"₹{entry.credit:.2f}", f"₹{running_balance:.2f}"]})
    return _list(request, title="General ledger", eyebrow="FINANCE", columns=["Date", "Account", "Reference", "ID", "Description", "Debit", "Credit", "Running balance"], rows=rows,
                 description=f"Filtered totals · Debit ₹{debit:.2f} · Credit ₹{credit:.2f}", actions=[("Reports", "admin_reports"), ("Expenses", "admin_expenses")])


@admin_required
def expenses(request):
    form = ExpenseForm(request.POST or None, initial={"date": timezone.localdate()})
    if request.method == "POST" and form.is_valid():
        record_expense(user=request.user, **form.cleaned_data)
        messages.success(request, "Expense recorded and posted to the ledger.")
        return _admin_redirect(request, "admin_expenses")
    qs = Expense.objects.order_by("-date", "-id")[:100]
    rows = [{"cells": [e.date, e.category, e.note or "—", e.mode, f"₹{e.amount:.2f}"]} for e in qs]
    columns = ["Date", "Category", "Note", "Mode", "Amount"]
    context = {"form": form, "columns": columns,
               "page_obj": Paginator(rows, 30).get_page(request.GET.get("page")),
               "mobile_rows": [{"labelled_cells": list(zip(columns, row["cells"]))} for row in rows]}
    return _admin_render(request, "admin_portal/expenses.html", "mobile/admin/expenses.html", context)


@admin_required
def reports(request):
    kind = request.GET.get("kind", "sales")
    report_title, columns, rows = "Sales", ["Date", "Bills", "Sales"], []
    if kind == "sales":
        report_title = "Sales report"
        day_rows = Bill.objects.exclude(status__in=[Bill.Status.HELD, Bill.Status.VOID]).values("created_at__date").annotate(count=Count("id"), total=Sum("grand_total")).order_by("-created_at__date")[:60]
        rows = [[r["created_at__date"], r["count"], f"₹{r['total'] or 0:.2f}"] for r in day_rows]
    elif kind == "stock":
        report_title, columns = "Stock valuation", ["Product", "SKU", "Variant", "Stock", "Cost", "Value", "State"]
        variants = ProductVariant.objects.select_related("product").filter(product__is_active=True).order_by("product__name")
        rows = [[v.product.name, v.sku, str(v), str(v.stock_qty), f"₹{v.product.cost_price:.2f}", f"₹{v.stock_qty * v.product.cost_price:.2f}", "Low" if v.stock_qty <= v.product.min_stock_alert else "OK"] for v in variants]
    elif kind == "tax":
        report_title, columns = "GST summary", ["Rate", "Taxable subtotal", "Tax collected"]
        aggregates = BillItem.objects.exclude(bill__status__in=[Bill.Status.HELD, Bill.Status.VOID]).values("tax_percent").annotate(base=Sum("line_total") - Sum("tax_amount"), tax=Sum("tax_amount"))
        rows = [[f"{r['tax_percent']}%", f"₹{r['base'] or 0:.2f}", f"₹{r['tax'] or 0:.2f}"] for r in aggregates]
    elif kind == "returns":
        report_title, columns = "Returns report", ["Return", "Bill", "Date", "Reason", "Refund"]
        returns = SaleReturn.objects.select_related("bill").order_by("-created_at")
        rows = [[r.return_no, r.bill.bill_no, r.created_at.date(), r.reason, f"₹{r.refund_amount:.2f}"] for r in returns]
    elif kind == "suppliers":
        report_title, columns = "Supplier outstanding", ["Supplier", "Phone", "Outstanding"]
        rows = [[s.name, s.phone, f"₹{supplier_balance(s):.2f}"] for s in Supplier.objects.filter(is_active=True)]
    elif kind == "customers":
        report_title, columns = "Customer outstanding", ["Customer", "Phone", "Outstanding"]
        rows = [[c.name, c.phone, f"₹{c.credit_balance:.2f}"] for c in Customer.objects.filter(credit_balance__gt=0)]
    elif kind == "product-sales":
        report_title, columns = "Product sales", ["Product", "SKU", "Quantity sold", "Net sales"]
        sales = BillItem.objects.exclude(bill__status__in=[Bill.Status.HELD, Bill.Status.VOID]).values("name_snapshot", "sku_snapshot").annotate(qty=Sum("qty"), total=Sum("line_total")).order_by("name_snapshot")
        rows = [[r["name_snapshot"], r["sku_snapshot"], r["qty"], f"₹{r['total']:.2f}"] for r in sales]
    if request.GET.get("export") == "csv":
        response = HttpResponse(content_type="text/csv; charset=utf-8")
        response["Content-Disposition"] = f'attachment; filename="{kind}-report.csv"'
        writer = csv.writer(response)
        writer.writerow(columns)
        writer.writerows(rows)
        return response
    context = {"kind": kind, "report_title": report_title, "columns": columns, "rows": rows,
               "mobile_rows": [{"labelled_cells": list(zip(columns, row))} for row in rows]}
    return _admin_render(request, "admin_portal/reports.html", "mobile/admin/reports.html", context)


@admin_required
def settings_view(request):
    settings_obj = ShopSettings.get_solo()
    form = ShopSettingsForm(request.POST or None, request.FILES or None, instance=settings_obj)
    if request.method == "POST" and form.is_valid():
        before = {field: str(getattr(settings_obj, field)) for field in form.changed_data}
        form.save()
        AuditLog.objects.create(user=request.user, action="shop_settings_updated", object_type="ShopSettings", object_id="1", before_data=before)
        messages.success(request, "Shop settings saved.")
        return _admin_redirect(request, "admin_settings")
    return _form(request, title="Shop settings", eyebrow="CONFIGURATION", form=form, submit_label="Save settings")


@admin_required
def audit_log(request):
    qs = AuditLog.objects.select_related("user").order_by("-created_at")
    query = request.GET.get("q", "").strip()
    if query:
        qs = qs.filter(Q(action__icontains=query) | Q(object_type__icontains=query) | Q(object_id__icontains=query) | Q(user__email__icontains=query))
    rows = [{"cells": [e.created_at.strftime("%d/%m/%Y %H:%M:%S"), e.user.name if e.user else "System", e.action, e.object_type, e.object_id, str(e.after_data or e.before_data)[:100]]} for e in qs]
    return _list(request, title="Audit log", eyebrow="SECURITY", columns=["Timestamp", "User", "Action", "Object", "ID", "Details"], rows=rows)