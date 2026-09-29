from django.urls import path

from apps.core import admin_views


urlpatterns = [
    path("products/", admin_views.products, name="mobile_admin_products"),
    path("products/new/", admin_views.product_create, name="mobile_admin_product_create"),
    path("products/<int:product_id>/", admin_views.product_edit, name="mobile_admin_product_edit"),
    path("products/<int:product_id>/deactivate/", admin_views.product_deactivate, name="mobile_admin_product_deactivate"),
    path("variants/<int:variant_id>/stock/", admin_views.stock_adjust, name="mobile_admin_stock_adjust"),
    path("categories/", admin_views.categories, name="mobile_admin_categories"),
    path("staff/", admin_views.staff_list, name="mobile_admin_staff"),
    path("staff/new/", admin_views.staff_create, name="mobile_admin_staff_create"),
    path("staff/<int:user_id>/", admin_views.staff_edit, name="mobile_admin_staff_edit"),
    path("suppliers/", admin_views.suppliers, name="mobile_admin_suppliers"),
    path("suppliers/new/", admin_views.supplier_create, name="mobile_admin_supplier_create"),
    path("suppliers/<int:supplier_id>/", admin_views.supplier_detail, name="mobile_admin_supplier_detail"),
    path("suppliers/<int:supplier_id>/edit/", admin_views.supplier_edit, name="mobile_admin_supplier_edit"),
    path("purchases/", admin_views.purchase_list, name="mobile_admin_purchases"),
    path("purchases/new/", admin_views.purchase_create, name="mobile_admin_purchase_create"),
    path("customers/", admin_views.customers, name="mobile_admin_customers"),
    path("customers/new/", admin_views.customer_create, name="mobile_admin_customer_create"),
    path("customers/<int:customer_id>/", admin_views.customer_detail, name="mobile_admin_customer_detail"),
    path("customers/<int:customer_id>/edit/", admin_views.customer_edit, name="mobile_admin_customer_edit"),
    path("returns/", admin_views.returns_list, name="mobile_admin_returns"),
    path("returns/new/", admin_views.return_create, name="mobile_admin_return_create"),
    path("bills/", admin_views.bills, name="mobile_admin_bills"),
    path("bills/<int:bill_id>/", admin_views.bill_detail, name="mobile_admin_bill_detail"),
    path("bills/<int:bill_id>/void/", admin_views.bill_void, name="mobile_admin_bill_void"),
    path("bills/<int:bill_id>/collect/", admin_views.bill_collect, name="mobile_admin_bill_collect"),
    path("ledger/", admin_views.ledger, name="mobile_admin_ledger"),
    path("expenses/", admin_views.expenses, name="mobile_admin_expenses"),
    path("reports/", admin_views.reports, name="mobile_admin_reports"),
    path("settings/", admin_views.settings_view, name="mobile_admin_settings"),
    path("audit/", admin_views.audit_log, name="mobile_admin_audit"),
]