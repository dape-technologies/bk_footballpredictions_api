from datetime import timedelta

from django.core.files.uploadedfile import SimpleUploadedFile
from django.utils import timezone
from rest_framework.test import APITestCase

from accounts.models import User
from .models import Package, Payment, Prediction, RecentWin, Subscription


class PremiumAccessTests(APITestCase):
    def setUp(self):
        self.customer = User.objects.create_user(phone="0702000001", first_name="Member", last_name="One", password="member-pass-27")
        self.other = User.objects.create_user(phone="0702000002", first_name="Other", last_name="Member", password="member-pass-27")
        self.owner = User.objects.create_superuser(phone="0702000003", first_name="BK", last_name="Owner", password="owner-pass-27")
        self.package = Package.objects.create(
            name="Daily Edge",
            slug="daily-edge",
            package_type="Accumulator",
            description="Daily member board",
            price=10000,
            duration_days=1,
            win_probability=78,
            commences_at=timezone.now() + timedelta(hours=3),
            betslip_link="https://example.com/slips/daily-edge",
            code="BK-DAY-78",
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

    def test_public_package_exposes_purchase_details_but_not_slip_secrets(self):
        response = self.client.get("/api/v1/packages/")

        self.assertEqual(response.status_code, 200)
        item = response.data[0]
        self.assertEqual(item["package_type"], "Accumulator")
        self.assertEqual(item["win_probability"], 78)
        self.assertIn("commences_at", item)
        self.assertNotIn("betslip_link", item)
        self.assertNotIn("code", item)

    def test_inactive_package_is_hidden_from_customers(self):
        self.package.is_active = False
        self.package.save(update_fields=["is_active", "updated_at"])

        response = self.client.get("/api/v1/packages/")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data, [])

    def test_owner_package_toggle_controls_public_visibility(self):
        self.client.force_login(self.owner)

        deactivate = self.client.patch(
            f"/api/v1/owner/packages/{self.package.pk}/",
            {"is_active": False},
            format="json",
        )
        self.assertEqual(deactivate.status_code, 200)
        self.assertFalse(deactivate.data["is_active"])

        self.client.logout()
        hidden = self.client.get("/api/v1/packages/")
        self.assertEqual(hidden.status_code, 200)
        self.assertEqual(hidden.data, [])

        self.client.force_login(self.owner)
        reactivate = self.client.patch(
            f"/api/v1/owner/packages/{self.package.pk}/",
            {"is_active": True},
            format="json",
        )
        self.assertEqual(reactivate.status_code, 200)
        self.assertTrue(reactivate.data["is_active"])

        self.client.logout()
        visible = self.client.get("/api/v1/packages/")
        self.assertEqual(visible.status_code, 200)
        self.assertEqual([item["id"] for item in visible.data], [self.package.pk])

    def test_owner_can_delete_package_with_purchase_and_prediction_history(self):
        subscription = Subscription.objects.create(
            user=self.customer,
            package=self.package,
            price_snapshot=self.package.price,
        )
        payment = Payment.objects.create(
            subscription=subscription,
            user=self.customer,
            package=self.package,
            amount=self.package.price,
        )
        self.client.force_login(self.owner)

        response = self.client.delete(f"/api/v1/owner/packages/{self.package.pk}/")

        self.assertEqual(response.status_code, 204)
        self.package.refresh_from_db()
        self.assertFalse(self.package.is_active)
        self.assertIsNotNone(self.package.deleted_at)
        self.assertTrue(Subscription.objects.filter(pk=subscription.pk).exists())
        self.assertTrue(Payment.objects.filter(pk=payment.pk).exists())
        self.assertTrue(Prediction.objects.filter(pk=self.prediction.pk).exists())
        self.assertEqual(self.client.get("/api/v1/owner/packages/").data, [])

        dashboard = self.client.get("/api/v1/owner/dashboard/")
        self.assertEqual(dashboard.status_code, 200)
        self.assertEqual(dashboard.data["active_packages"], 0)
        self.assertEqual(dashboard.data["package_demand"], [])

        self.client.logout()
        self.assertEqual(self.client.get("/api/v1/packages/").data, [])

    def test_pending_request_does_not_unlock_content(self):
        Subscription.objects.create(user=self.customer, package=self.package, price_snapshot=self.package.price)
        self.client.force_login(self.customer)

        response = self.client.get("/api/v1/predictions/")

        self.assertTrue(response.data[0]["locked"])
        self.assertNotIn("selection", response.data[0])

        subscriptions = self.client.get("/api/v1/me/subscriptions/")
        self.assertEqual(subscriptions.data[0]["betslip_link"], "")
        self.assertEqual(subscriptions.data[0]["code"], "")

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

        subscriptions = self.client.get("/api/v1/me/subscriptions/")
        self.assertEqual(subscriptions.data[0]["betslip_link"], self.package.betslip_link)
        self.assertEqual(subscriptions.data[0]["code"], self.package.code)

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

    def test_buying_a_slip_creates_a_pending_payment(self):
        self.client.force_login(self.customer)

        response = self.client.post(
            "/api/v1/me/subscriptions/",
            {"package_id": self.package.id},
            format="json",
        )

        self.assertEqual(response.status_code, 201)
        payment = Payment.objects.get(subscription_id=response.data["id"])
        self.assertEqual(payment.status, Payment.Status.PENDING)
        self.assertEqual(payment.amount, self.package.price)
        self.assertEqual(response.data["payment_status"], Payment.Status.PENDING)
        self.assertEqual(response.data["payment_reference"], payment.reference)

    def test_confirmed_payment_activates_betslip_access(self):
        subscription = Subscription.objects.create(
            user=self.customer,
            package=self.package,
            price_snapshot=self.package.price,
        )
        payment = Payment.objects.create(
            subscription=subscription,
            user=self.customer,
            package=self.package,
            amount=self.package.price,
        )
        self.client.force_login(self.owner)

        response = self.client.post(
            f"/api/v1/owner/payments/{payment.id}/resolve/",
            {"decision": "confirm"},
            format="json",
        )

        payment.refresh_from_db()
        subscription.refresh_from_db()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(payment.status, Payment.Status.PAID)
        self.assertIsNotNone(payment.paid_at)
        self.assertEqual(subscription.status, Subscription.Status.ACTIVE)
        self.assertTrue(subscription.grants_access)

    def test_customer_cannot_view_payment_dashboard(self):
        self.client.force_login(self.customer)

        response = self.client.get("/api/v1/owner/payments/")

        self.assertEqual(response.status_code, 403)

    def test_owner_can_upload_a_recent_win_photo_and_caption(self):
        image = SimpleUploadedFile(
            "recent-win.gif",
            b"GIF87a\x01\x00\x01\x00\x80\x00\x00\x00\x00\x00\xff\xff\xff!\xf9\x04\x01\x00\x00\x00\x00,\x00\x00\x00\x00\x01\x00\x01\x00\x00\x02\x02D\x01\x00;",
            content_type="image/gif",
        )
        self.client.force_login(self.owner)

        response = self.client.post(
            "/api/v1/owner/recent-wins/",
            {"caption": "Weekend accumulator landed.", "image": image, "is_published": True},
            format="multipart",
        )

        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.data["caption"], "Weekend accumulator landed.")
        win = RecentWin.objects.get(pk=response.data["id"])
        self.assertTrue(bool(win.image))

        self.client.logout()
        public_response = self.client.get("/api/v1/recent-wins/")
        self.assertEqual(public_response.status_code, 200)
        self.assertEqual(public_response.data[0]["caption"], "Weekend accumulator landed.")
        self.assertTrue(public_response.data[0]["image_url"])
        win.image.delete(save=False)

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
