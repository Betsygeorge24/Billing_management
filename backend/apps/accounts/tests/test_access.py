from django.test import TestCase
from django.urls import reverse

from apps.accounts.models import User


class PortalAccessTests(TestCase):
    def setUp(self):
        self.staff = User.objects.create_user(
            email="cashier@example.com",
            password="Strong-password-123",
            name="Cashier",
            role=User.Role.STAFF,
        )
        self.admin = User.objects.create_user(
            email="owner@example.com",
            password="Strong-password-123",
            name="Owner",
            role=User.Role.ADMIN,
        )

    def test_staff_is_limited_to_billing(self):
        self.client.force_login(self.staff)

        billing_response = self.client.get("/billing/")
        self.assertEqual(billing_response.status_code, 200)
        self.assertContains(billing_response, 'aria-label="Main navigation"')
        self.assertContains(billing_response, ">Billing</span>")
        self.assertContains(billing_response, ">Transactions</span>")
        self.assertNotContains(billing_response, ">Overview</span>")
        self.assertNotContains(billing_response, ">Mobile view</span>")
        self.assertEqual(self.client.get("/admin-portal/").status_code, 403)
        self.assertEqual(self.client.get("/m/admin/").status_code, 403)

    def test_admin_can_open_all_phase_one_portals(self):
        self.client.force_login(self.admin)

        self.assertEqual(self.client.get("/billing/").status_code, 200)
        self.assertEqual(self.client.get("/admin-portal/").status_code, 200)
        mobile_dashboard = self.client.get("/m/admin/")
        self.assertEqual(mobile_dashboard.status_code, 200)
        self.assertContains(mobile_dashboard, 'class="phone-frame"')

    def test_admin_mobile_user_agent_uses_same_dashboard_url(self):
        self.client.force_login(self.admin)

        response = self.client.get(reverse("post_login_redirect"), HTTP_USER_AGENT="Mozilla Android Mobile")

        self.assertRedirects(response, reverse("admin_dashboard"))

    def test_inactive_user_cannot_authenticate(self):
        self.staff.is_active = False
        self.staff.save(update_fields=["is_active"])

        self.assertFalse(self.client.login(email=self.staff.email, password="Strong-password-123"))