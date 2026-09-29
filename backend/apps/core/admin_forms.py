from decimal import Decimal

from django import forms
from django.forms import inlineformset_factory

from apps.accounts.models import User
from apps.billing.models import Payment
from apps.catalog.models import Category, Product, ProductVariant
from apps.customers.models import Customer, CustomerPayment
from apps.ledger.models import Expense
from apps.sales_returns.models import SaleReturn
from apps.shop_settings.models import ShopSettings
from apps.suppliers.models import Purchase, PurchaseItem, Supplier, SupplierPayment


class StyledModelForm(forms.ModelForm):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            existing = field.widget.attrs.get("class", "")
            field.widget.attrs["class"] = f"{existing} form-control".strip()
            if isinstance(field.widget, forms.CheckboxInput):
                field.widget.attrs["class"] = "form-check-input"
            if isinstance(field.widget, forms.Select):
                field.widget.attrs["class"] = f"{existing} form-select".strip()


class CategoryForm(StyledModelForm):
    class Meta:
        model = Category
        fields = ["name", "description", "is_active"]


class ProductForm(StyledModelForm):
    class Meta:
        model = Product
        fields = ["name", "sku", "barcode", "category", "hsn_code", "unit", "cost_price", "selling_price", "mrp", "tax_percent", "min_stock_alert", "is_active", "image"]


class ProductVariantForm(StyledModelForm):
    class Meta:
        model = ProductVariant
        fields = ["size", "color", "sku", "barcode", "price_override"]


ProductVariantFormSet = inlineformset_factory(
    Product, ProductVariant, form=ProductVariantForm, extra=1, can_delete=True,
)


class StockAdjustmentForm(forms.Form):
    quantity_change = forms.DecimalField(max_digits=12, decimal_places=3, label="Quantity change", help_text="Use a negative value to reduce stock.")
    reason = forms.CharField(max_length=240, widget=forms.Textarea(attrs={"rows": 3}))

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            field.widget.attrs["class"] = "form-control"


class StaffForm(StyledModelForm):
    password = forms.CharField(required=False, widget=forms.PasswordInput, help_text="Required when creating a staff account; leave blank to keep the current password.")
    can_give_discount = forms.BooleanField(required=False)
    max_discount_percent = forms.DecimalField(required=False, min_value=0, max_value=100, decimal_places=2, initial=0)
    can_process_return = forms.BooleanField(required=False)
    can_view_own_reports = forms.BooleanField(required=False)
    can_use_customer_credit = forms.BooleanField(required=False, initial=True)

    class Meta:
        model = User
        fields = ["name", "email", "phone", "role", "is_active"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        permissions = self.instance.permissions if self.instance and isinstance(self.instance.permissions, dict) else {}
        for name in ("can_give_discount", "max_discount_percent", "can_process_return", "can_view_own_reports", "can_use_customer_credit"):
            if name not in self.data and self.instance.pk:
                self.fields[name].initial = permissions.get(name, self.fields[name].initial)
        self.fields["role"].initial = User.Role.STAFF

    def clean(self):
        cleaned = super().clean()
        if not self.instance.pk and not cleaned.get("password"):
            self.add_error("password", "Set an initial password for this account.")
        if cleaned.get("role") == User.Role.ADMIN and not self.instance.is_superuser:
            self.add_error("role", "Only an existing superuser may grant the admin role.")
        return cleaned

    def save(self, commit=True):
        user = super().save(commit=False)
        user.permissions = {
            "can_give_discount": self.cleaned_data.get("can_give_discount", False),
            "max_discount_percent": str(self.cleaned_data.get("max_discount_percent") or Decimal("0")),
            "can_process_return": self.cleaned_data.get("can_process_return", False),
            "can_view_own_reports": self.cleaned_data.get("can_view_own_reports", False),
            "can_use_customer_credit": self.cleaned_data.get("can_use_customer_credit", True),
        }
        password = self.cleaned_data.get("password")
        if password:
            user.set_password(password)
        if commit:
            user.save()
        return user


class SupplierForm(StyledModelForm):
    class Meta:
        model = Supplier
        fields = ["name", "phone", "email", "address", "gstin", "opening_balance", "is_active"]


class PurchaseForm(StyledModelForm):
    class Meta:
        model = Purchase
        fields = ["supplier", "invoice_no", "date"]
        widgets = {"date": forms.DateInput(attrs={"type": "date"})}


class PurchaseItemForm(StyledModelForm):
    class Meta:
        model = PurchaseItem
        fields = ["product", "variant", "qty", "unit_cost", "tax_percent"]


PurchaseItemFormSet = inlineformset_factory(
    Purchase, PurchaseItem, form=PurchaseItemForm, extra=3, can_delete=True,
)


class SupplierPaymentForm(StyledModelForm):
    class Meta:
        model = SupplierPayment
        fields = ["amount", "mode", "date", "note"]
        widgets = {"date": forms.DateInput(attrs={"type": "date"})}


class CustomerForm(StyledModelForm):
    class Meta:
        model = Customer
        fields = ["name", "phone", "address", "gstin", "is_active"]


class CustomerPaymentForm(StyledModelForm):
    class Meta:
        model = CustomerPayment
        fields = ["amount", "mode", "date", "note"]
        widgets = {"date": forms.DateInput(attrs={"type": "date"})}


class ReturnHeaderForm(forms.Form):
    bill_no = forms.CharField(max_length=40, label="Bill number")
    reason = forms.CharField(widget=forms.Textarea(attrs={"rows": 3}))
    refund_mode = forms.ChoiceField(choices=SaleReturn._meta.get_field("refund_mode").choices)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            field.widget.attrs["class"] = "form-control" if not isinstance(field.widget, forms.Select) else "form-select"


class ReturnItemForm(forms.Form):
    bill_item_id = forms.IntegerField(widget=forms.HiddenInput)
    qty = forms.DecimalField(max_digits=12, decimal_places=3, min_value=0, required=False)
    restock = forms.BooleanField(required=False, initial=True)


class VoidBillForm(forms.Form):
    reason = forms.CharField(max_length=240, widget=forms.Textarea(attrs={"rows": 3}))

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["reason"].widget.attrs["class"] = "form-control"


class BalanceCollectionForm(forms.Form):
    amount = forms.DecimalField(max_digits=12, decimal_places=2, min_value=Decimal("0.01"))
    mode = forms.ChoiceField(choices=[(Payment.Mode.CASH, "Cash"), (Payment.Mode.CARD, "Card"), (Payment.Mode.UPI, "UPI")])
    reference = forms.CharField(max_length=100, required=False)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            field.widget.attrs["class"] = "form-control" if not isinstance(field.widget, forms.Select) else "form-select"


class ExpenseForm(StyledModelForm):
    class Meta:
        model = Expense
        fields = ["category", "amount", "date", "note", "mode"]
        widgets = {"date": forms.DateInput(attrs={"type": "date"})}


class ShopSettingsForm(StyledModelForm):
    class Meta:
        model = ShopSettings
        fields = ["shop_name", "address", "gstin", "phone", "logo", "invoice_prefix", "footer_note", "default_tax", "currency", "paper_size", "allow_negative_stock"]