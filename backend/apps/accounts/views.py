from django.contrib.auth import logout
from django.contrib.auth.views import LoginView
from django.contrib.auth.decorators import login_required
from django.shortcuts import redirect
from django.urls import reverse

from apps.accounts.forms import EmailAuthenticationForm


class EmailLoginView(LoginView):
    template_name = "registration/login.html"
    authentication_form = EmailAuthenticationForm


@login_required
def post_login_redirect(request):
    if request.user.is_admin:
        return redirect("admin_dashboard")
    return redirect("billing_home")


def logout_view(request):
    if request.method == "POST":
        logout(request)
        return redirect("login")
    return redirect(reverse("login"))