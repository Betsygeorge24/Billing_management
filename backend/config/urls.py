from django.contrib import admin
from django.urls import include, path

from apps.accounts.views import EmailLoginView, logout_view, post_login_redirect
from apps.billing import views as billing_views
from apps.core.views import admin_dashboard, mobile_admin_home, mobile_bills, mobile_more, mobile_root


urlpatterns = [
    path("django-admin/", admin.site.urls),
    path("accounts/login/", EmailLoginView.as_view(), name="login"),
    path("accounts/logout/", logout_view, name="logout"),
    path("accounts/continue/", post_login_redirect, name="post_login_redirect"),
    path("admin-portal/", admin_dashboard, name="admin_dashboard"),
    path("admin-portal/", include("apps.core.admin_urls")),
    path("m/", mobile_root, name="mobile_root"),
    path("m/admin/", mobile_admin_home, name="mobile_admin_home"),
    path("m/admin/", include("apps.core.mobile_admin_urls")),
    path("m/admin/bills/", mobile_bills, name="mobile_bills"),
    path("m/admin/more/", mobile_more, name="mobile_more"),
    path("billing/", include("apps.billing.urls")),
    path("", include("django.contrib.auth.urls")),
]