import hashlib
import hmac
import time
from datetime import timedelta
from unittest.mock import patch

from django.test import override_settings
from django.utils import timezone
from rest_framework.test import APITestCase

from accounts.models import User
from .models import Package, Payment, Prediction, Purchase


@override_settings(
    RELWORX_API_KEY="test-key",
    RELWORX_ACCOUNT_NO="RELBTEST",
    RELWORX_WEBHOOK_SIGNING_KEY="webhook-secret",
    RELWORX_WEBHOOK_URL="http://testserver/api/v1/payments/relworx/webhook/",
    RELWORX_WEBHOOK_TOLERANCE_SECONDS=300,
)
class PurchaseFlowTests(APITestCase):
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
            win_probability=78,
            closes_at=timezone.now() + timedelta(hours=3),
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

    def create_purchase(self):
        self.client.force_login(self.customer)
        response = self.client.post("/api/v1/me/purchases/", {"package_id": self.package.id}, format="json")
        self.assertEqual(response.status_code, 201)
        return Purchase.objects.get(pk=response.data["id"])

    @patch("catalog.views.request_mobile_money_payment")
    def start_payment(self, mocked_request, purchase=None):
        purchase = purchase or self.create_purchase()
        mocked_request.return_value = {
            "success": True,
            "message": "Request payment in progress.",
            "internal_reference": "relworx-internal-1",
        }
        response = self.client.post(
            f"/api/v1/me/purchases/{purchase.id}/payment-attempts/",
            {"phone": "0773454899"},
            format="json",
        )
        self.assertEqual(response.status_code, 202)
        payment = Payment.objects.get(pk=response.data["id"])
        self.assertEqual(payment.payer_msisdn, "+256773454899")
        return purchase, payment

    def signed_webhook(self, payment, **overrides):
        payload = {
            "status": "success",
            "message": "Request payment completed successfully.",
            "customer_reference": payment.reference,
            "internal_reference": payment.internal_reference,
            "amount": 10000,
            "currency": "UGX",
            "provider": "mtn_mobile_money",
            "completed_at": timezone.now().isoformat(),
        }
        payload.update(overrides)
        timestamp = int(time.time())
        signed = f"http://testserver/api/v1/payments/relworx/webhook/{timestamp}"
        for key in sorted(["status", "customer_reference", "internal_reference"]):
            signed += f"{key}{payload.get(key, '')}"
        signature = hmac.new(b"webhook-secret", signed.encode(), hashlib.sha256).hexdigest()
        return self.client.post(
            "/api/v1/payments/relworx/webhook/",
            payload,
            format="json",
            HTTP_RELWORX_SIGNATURE=f"t={timestamp},v={signature}",
        )

    def test_public_package_hides_secrets_and_closed_package_remains_visible(self):
        response = self.client.get("/api/v1/packages/")
        self.assertEqual(response.status_code, 200)
        self.assertNotIn("code", response.data[0])
        self.assertNotIn("betslip_link", response.data[0])
        self.package.closes_at = timezone.now() - timedelta(seconds=1)
        self.package.save(update_fields=["closes_at"])
        response = self.client.get("/api/v1/packages/")
        self.assertEqual(len(response.data), 1)
        self.assertFalse(response.data[0]["is_open"])

    def test_soft_deleted_package_is_not_public(self):
        self.package.deleted_at = timezone.now()
        self.package.save(update_fields=["deleted_at"])
        self.assertEqual(self.client.get("/api/v1/packages/").data, [])

    def test_guest_cannot_create_purchase(self):
        response = self.client.post("/api/v1/me/purchases/", {"package_id": self.package.id}, format="json")
        self.assertIn(response.status_code, [401, 403])

    def test_closed_package_cannot_be_purchased(self):
        self.package.closes_at = timezone.now()
        self.package.save(update_fields=["closes_at"])
        self.client.force_login(self.customer)
        response = self.client.post("/api/v1/me/purchases/", {"package_id": self.package.id}, format="json")
        self.assertEqual(response.status_code, 400)

    def test_pending_purchase_does_not_reveal_secrets(self):
        purchase = self.create_purchase()
        response = self.client.get(f"/api/v1/me/purchases/{purchase.id}/")
        self.assertFalse(response.data["is_paid"])
        self.assertEqual(response.data["code"], "")
        self.assertEqual(response.data["betslip_link"], "")

    @patch("catalog.views.request_mobile_money_payment")
    def test_payment_provider_failure_is_retryable(self, mocked_request):
        from .relworx import RelworxError

        purchase = self.create_purchase()
        mocked_request.side_effect = RelworxError("Provider unavailable.")
        response = self.client.post(
            f"/api/v1/me/purchases/{purchase.id}/payment-attempts/",
            {"phone": "0773454899"},
            format="json",
        )
        self.assertEqual(response.status_code, 502)
        self.assertEqual(Payment.objects.get(purchase=purchase).status, Payment.Status.FAILED)
        mocked_request.side_effect = None
        mocked_request.return_value = {"success": True, "message": "Pending", "internal_reference": "retry-ref"}
        retry = self.client.post(
            f"/api/v1/me/purchases/{purchase.id}/payment-attempts/",
            {"phone": "0701000000"},
            format="json",
        )
        self.assertEqual(retry.status_code, 202)
        self.assertEqual(purchase.payments.count(), 2)

    @patch("catalog.views.request_mobile_money_payment")
    def test_uncertain_provider_response_stays_pending_to_prevent_double_charge(self, mocked_request):
        from .relworx import RelworxError

        purchase = self.create_purchase()
        mocked_request.side_effect = RelworxError("Confirmation pending.", uncertain=True)
        response = self.client.post(
            f"/api/v1/me/purchases/{purchase.id}/payment-attempts/",
            {"phone": "0773454899"},
            format="json",
        )
        self.assertEqual(response.status_code, 202)
        self.assertEqual(Payment.objects.get(purchase=purchase).status, Payment.Status.PENDING)
        retry = self.client.post(
            f"/api/v1/me/purchases/{purchase.id}/payment-attempts/",
            {"phone": "0773454899"},
            format="json",
        )
        self.assertEqual(retry.status_code, 409)

    def test_unsigned_and_incorrectly_signed_webhooks_are_rejected(self):
        _, payment = self.start_payment()
        payload = {"status": "success", "customer_reference": payment.reference, "internal_reference": payment.internal_reference}
        unsigned = self.client.post("/api/v1/payments/relworx/webhook/", payload, format="json")
        bad = self.client.post(
            "/api/v1/payments/relworx/webhook/",
            payload,
            format="json",
            HTTP_RELWORX_SIGNATURE=f"t={int(time.time())},v=wrong",
        )
        self.assertEqual(unsigned.status_code, 401)
        self.assertEqual(bad.status_code, 401)

    def test_stale_webhook_is_rejected(self):
        _, payment = self.start_payment()
        payload = {"status": "success", "customer_reference": payment.reference, "internal_reference": payment.internal_reference}
        timestamp = int(time.time()) - 301
        response = self.client.post(
            "/api/v1/payments/relworx/webhook/",
            payload,
            format="json",
            HTTP_RELWORX_SIGNATURE=f"t={timestamp},v=anything",
        )
        self.assertEqual(response.status_code, 401)

    def test_success_webhook_completes_purchase_and_is_idempotent(self):
        purchase, payment = self.start_payment()
        first = self.signed_webhook(payment)
        second = self.signed_webhook(payment)
        purchase.refresh_from_db()
        payment.refresh_from_db()
        self.assertEqual(first.status_code, 200)
        self.assertEqual(second.status_code, 200)
        self.assertTrue(purchase.is_paid)
        self.assertEqual(payment.status, Payment.Status.PAID)
        self.assertEqual(purchase.code_snapshot, "BK-DAY-78")

    def test_amount_currency_and_internal_reference_mismatches_never_unlock(self):
        cases = [
            {"amount": 9999},
            {"currency": "KES"},
            {"internal_reference": "wrong-internal"},
        ]
        for index, overrides in enumerate(cases):
            if index:
                Payment.objects.all().delete()
                Purchase.objects.all().delete()
            purchase, payment = self.start_payment()
            response = self.signed_webhook(payment, **overrides)
            purchase.refresh_from_db()
            self.assertEqual(response.status_code, 400)
            self.assertFalse(purchase.is_paid)

    def test_payment_started_before_close_can_finish_after_close(self):
        purchase, payment = self.start_payment()
        self.package.closes_at = timezone.now() - timedelta(minutes=1)
        self.package.save(update_fields=["closes_at"])
        self.assertEqual(self.signed_webhook(payment).status_code, 200)
        purchase.refresh_from_db()
        self.assertTrue(purchase.is_paid)

    def test_paid_snapshot_survives_package_edits(self):
        purchase, payment = self.start_payment()
        self.signed_webhook(payment)
        self.package.code = "NEW-CODE"
        self.package.betslip_link = "https://example.com/new"
        self.package.save(update_fields=["code", "betslip_link"])
        response = self.client.get(f"/api/v1/me/purchases/{purchase.id}/")
        self.assertEqual(response.data["code"], "BK-DAY-78")
        self.assertEqual(response.data["betslip_link"], "https://example.com/slips/daily-edge")

    def test_user_cannot_read_another_users_purchase(self):
        purchase = self.create_purchase()
        self.client.force_login(self.other)
        response = self.client.get(f"/api/v1/me/purchases/{purchase.id}/")
        self.assertEqual(response.status_code, 404)

    def test_paid_purchase_unlocks_premium_prediction_permanently(self):
        purchase, payment = self.start_payment()
        self.signed_webhook(payment)
        response = self.client.get("/api/v1/predictions/")
        self.assertFalse(response.data[0]["locked"])
        self.assertEqual(response.data[0]["selection"], "Arsenal to win")
        purchase.refresh_from_db()
        self.assertIsNotNone(purchase.paid_at)

    def test_owner_has_no_manual_payment_resolution_endpoint(self):
        _, payment = self.start_payment()
        self.client.force_login(self.owner)
        response = self.client.post(f"/api/v1/owner/payments/{payment.id}/resolve/", {"decision": "confirm"}, format="json")
        self.assertEqual(response.status_code, 404)
