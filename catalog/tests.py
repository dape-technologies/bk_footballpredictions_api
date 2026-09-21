from datetime import timedelta

from django.utils import timezone
from rest_framework.test import APITestCase

from accounts.models import User
from .models import Package, Prediction, Subscription


class PremiumAccessTests(APITestCase):
    def setUp(self):
        self.customer = User.objects.create_user(phone="0702000001", first_name="Member", last_name="One", password="member-pass-27")
        self.other = User.objects.create_user(phone="0702000002", first_name="Other", last_name="Member", password="member-pass-27")
        self.owner = User.objects.create_superuser(phone="0702000003", first_name="BK", last_name="Owner", password="owner-pass-27")
        self.package = Package.objects.create(
            name="Daily Edge",
            slug="daily-edge",
            description="Daily member board",
            price=10000,
            duration_days=1,
        )
        self.prediction = Prediction.objects.create(
            home_team="Arsenal",
            away_team="Newcastle",
            competition="Premier League",
            kickoff_at=timezone.now() + timedelta(hours=3),
            access_level=Prediction.Access.PREMIUM,
            package=self.package,
            market="Match result",
            selection="Arsenal to win",
            odds="1.84",
            confidence=78,
            analysis="A premium explanation.",
            is_published=True,
            created_by=self.owner,
        )

    def test_guest_response_omits_every_premium_field(self):
        response = self.client.get("/api/v1/predictions/")

        self.assertEqual(response.status_code, 200)
        item = response.data[0]
        self.assertTrue(item["locked"])
        for field in ["market", "selection", "odds", "confidence", "analysis", "betslip_reference"]:
            self.assertNotIn(field, item)

    def test_pending_request_does_not_unlock_content(self):
        Subscription.objects.create(user=self.customer, package=self.package, price_snapshot=self.package.price)
        self.client.force_login(self.customer)

        response = self.client.get("/api/v1/predictions/")

        self.assertTrue(response.data[0]["locked"])
        self.assertNotIn("selection", response.data[0])

    def test_active_subscription_unlocks_content(self):
        Subscription.objects.create(
            user=self.customer,
            package=self.package,
            price_snapshot=self.package.price,
            status=Subscription.Status.ACTIVE,
            starts_at=timezone.now(),
            expires_at=timezone.now() + timedelta(days=1),
        )
        self.client.force_login(self.customer)

        response = self.client.get("/api/v1/predictions/")

        self.assertFalse(response.data[0]["locked"])
        self.assertEqual(response.data[0]["selection"], "Arsenal to win")

    def test_expired_subscription_is_revoked(self):
        Subscription.objects.create(
            user=self.customer,
            package=self.package,
            price_snapshot=self.package.price,
            status=Subscription.Status.ACTIVE,
            starts_at=timezone.now() - timedelta(days=2),
            expires_at=timezone.now() - timedelta(minutes=1),
        )
        self.client.force_login(self.customer)

        response = self.client.get("/api/v1/predictions/")

        self.assertTrue(response.data[0]["locked"])

    def test_customer_only_sees_own_subscriptions(self):
        Subscription.objects.create(user=self.other, package=self.package, price_snapshot=self.package.price)
        self.client.force_login(self.customer)

        response = self.client.get("/api/v1/me/subscriptions/")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data, [])

    def test_owner_can_approve_request_and_duration_is_applied(self):
        subscription = Subscription.objects.create(user=self.customer, package=self.package, price_snapshot=self.package.price)
        self.client.force_login(self.owner)

        response = self.client.post(
            f"/api/v1/owner/subscriptions/{subscription.id}/decide/",
            {"decision": "approve"},
            format="json",
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["status"], Subscription.Status.ACTIVE)
        self.assertIsNotNone(response.data["expires_at"])

    def test_customer_cannot_access_owner_dashboard(self):
        self.client.force_login(self.customer)

        response = self.client.get("/api/v1/owner/dashboard/")

        self.assertEqual(response.status_code, 403)

    def test_non_owner_staff_cannot_access_owner_dashboard(self):
        staff = User.objects.create_user(
            phone="0702000004",
            first_name="Support",
            last_name="Agent",
            password="staff-pass-27",
            is_staff=True,
        )
        self.client.force_login(staff)

        response = self.client.get("/api/v1/owner/dashboard/")

        self.assertEqual(response.status_code, 403)

    def test_owner_can_review_authentication_activity(self):
        self.client.post(
            "/api/v1/auth/login/",
            {"phone": self.customer.phone, "password": "wrong-password"},
            format="json",
        )
        self.client.force_login(self.owner)

        response = self.client.get("/api/v1/owner/activities/")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data[0]["action"], "auth.login_failed")
