from django.urls import path

from apps.billing import views


urlpatterns = [
    path("", views.billing_page, name="billing_home"),
    path("api/products/", views.product_search, name="billing_product_search"),
    path("api/customers/", views.customer_create, name="billing_customer_create"),
    path("api/held/", views.held_bills, name="billing_held_bills"),
    path("api/held/<int:held_id>/", views.held_bill_detail, name="billing_held_bill_detail"),
    path("api/checkout/", views.checkout_view, name="billing_checkout"),
    path("bills/", views.my_bills, name="billing_my_bills"),
    path("bills/<int:bill_id>/", views.bill_receipt, name="billing_receipt"),
]