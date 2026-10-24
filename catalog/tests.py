import hashlib
import hmac
import time
from datetime import timedelta
from decimal import Decimal
from io import StringIO
from unittest.mock import patch

from django.contrib.auth.models import Group
from django.core.management import call_command
from django.test import override_settings
from django.utils import timezone
from rest_framework.test import APITestCase

from accounts.models import User
from .models import Package, Payment, PaymentAlert, PaymentEvent, PayoutRequest, Prediction, Purchase, WalletLedgerEntry
from .wallet_services import wallet_account


class OwnerRolePermissionTests(APITestCase):
    owner_urls = [
        "/api/v1/owner/dashboard/",
        "/api/v1/owner/activities/",
        "/api/v1/owner/packages/",
        "/api/v1/owner/payments/",
        "/api/v1/owner/payments/report/",
        "/api/v1/owner/payment-alerts/",
        "/api/v1/owner/performance/",
        "/api/v1/owner/wallet/",
        "/api/v1/owner/predictions/",
        "/api/v1/owner/recent-wins/",
        "/api/v1/owner/testimonials/",
        "/api/v1/owner/customers/",
    ]

    def setUp(self):
        self.customer = User.objects.create_user(
            phone="0703000001",
            first_name="Regular",
            last_name="Customer",
            password="customer-pass-27",
        )
        self.staff = User.objects.create_user(
            phone="0703000002",
            first_name="Ordinary",
            last_name="Staff",
            password="staff-pass-27",
            is_staff=True,
        )
        self.owner = User.objects.create_user(
            phone="0703000003",
            first_name="Product",
            last_name="Owner",
            password="owner-pass-27",
            is_staff=True,
        )
        owner_group, _ = Group.objects.get_or_create(name="Product Owner")
        self.owner.groups.add(owner_group)

    def assert_owner_views_return(self, expected_status):
        for url in self.owner_urls:
            with self.subTest(url=url):
                self.assertEqual(self.client.get(url).status_code, expected_status)

    def test_anonymous_user_cannot_access_any_owner_view(self):
        self.assert_owner_views_return(401)

    def test_customer_cannot_access_any_owner_view(self):
        self.client.force_login(self.customer)
        self.assert_owner_views_return(403)

    def test_staff_without_product_owner_role_cannot_access_any_owner_view(self):
        self.client.force_login(self.staff)
        self.assert_owner_views_return(403)

    def test_product_owner_group_can_access_every_owner_view(self):
        self.client.force_login(self.owner)
        self.assert_owner_views_return(200)


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
            "amount": payment.amount,
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

    def test_inactive_package_is_not_public_or_purchasable(self):
        self.package.is_active = False
        self.package.save(update_fields=["is_active"])

        self.assertEqual(self.client.get("/api/v1/packages/").data, [])
        self.client.force_login(self.customer)
        response = self.client.post("/api/v1/me/purchases/", {"package_id": self.package.id}, format="json")

        self.assertEqual(response.status_code, 400)
        self.assertEqual(Purchase.objects.count(), 0)

    def test_owner_can_deactivate_a_package(self):
        self.client.force_login(self.owner)

        response = self.client.patch(
            f"/api/v1/owner/packages/{self.package.id}/",
            {"is_active": False},
            format="json",
        )

        self.package.refresh_from_db()
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.data["is_active"])
        self.assertFalse(self.package.is_active)
        self.assertFalse(self.package.is_open)

    def test_guest_cannot_create_purchase(self):
        response = self.client.post("/api/v1/me/purchases/", {"package_id": self.package.id}, format="json")
        self.assertIn(response.status_code, [401, 403])

    def test_closed_package_cannot_be_purchased(self):
        self.package.closes_at = timezone.now()
        self.package.save(update_fields=["closes_at"])
        self.client.force_login(self.customer)
        response = self.client.post("/api/v1/me/purchases/", {"package_id": self.package.id}, format="json")
        self.assertEqual(response.status_code, 400)

    def test_payment_rejects_package_outside_relworx_ugx_limits(self):
        self.package.price = 100
        self.package.save(update_fields=["price"])
        purchase = self.create_purchase()

        response = self.client.post(
            f"/api/v1/me/purchases/{purchase.id}/payment-attempts/",
            {"phone": "0773454899"},
            format="json",
        )

        self.assertEqual(response.status_code, 409)
        self.assertEqual(purchase.payments.count(), 0)

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
        self.assertEqual(Payment.objects.get(purchase=purchase).status, Payment.Status.SUBMITTED)
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
        self.assertEqual(payment.base_amount, Decimal("10000"))
        self.assertEqual(payment.provider_fee_estimate, payment.amount - payment.base_amount)
        self.assertEqual(WalletLedgerEntry.objects.filter(payment=payment).count(), 2)

    @patch("catalog.payment_services.check_mobile_money_payment")
    def test_status_refresh_recovers_a_missed_success_webhook(self, mocked_check):
        purchase, payment = self.start_payment()
        mocked_check.return_value = {
            "success": True,
            "status": "success",
            "message": "Request payment completed successfully.",
            "customer_reference": payment.reference,
            "internal_reference": payment.internal_reference,
            "amount": payment.amount,
            "currency": "UGX",
            "provider": "MTN_UGANDA",
            "completed_at": timezone.now().isoformat(),
        }

        response = self.client.post(f"/api/v1/me/payments/{payment.id}/refresh/", {}, format="json")

        purchase.refresh_from_db()
        payment.refresh_from_db()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(payment.status, Payment.Status.PAID)
        self.assertTrue(purchase.is_paid)

    @patch("catalog.payment_services.check_mobile_money_payment")
    def test_status_refresh_keeps_provider_pending_payment_pending(self, mocked_check):
        _, payment = self.start_payment()
        mocked_check.return_value = {
            "success": True,
            "status": "pending",
            "customer_reference": payment.reference,
            "internal_reference": payment.internal_reference,
        }

        response = self.client.post(f"/api/v1/me/payments/{payment.id}/refresh/", {}, format="json")

        payment.refresh_from_db()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["status"], Payment.Status.PENDING)
        self.assertEqual(payment.status, Payment.Status.PENDING)

    def test_user_cannot_refresh_another_users_payment(self):
        _, payment = self.start_payment()
        self.client.force_login(self.other)

        response = self.client.post(f"/api/v1/me/payments/{payment.id}/refresh/", {}, format="json")

        self.assertEqual(response.status_code, 404)

    def test_amount_currency_and_internal_reference_mismatches_never_unlock(self):
        cases = [
            {"amount": 9999},
            {"currency": "KES"},
            {"internal_reference": "wrong-internal"},
        ]
        for index, overrides in enumerate(cases):
            if index:
                PaymentAlert.objects.all().delete()
                PaymentEvent.objects.all().delete()
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

    def test_success_records_provider_fields_events_and_receipt(self):
        _, payment = self.start_payment()
        response = self.signed_webhook(
            payment,
            charge="250.50",
            provider_transaction_id="MTN-TXN-123",
        )
        payment.refresh_from_db()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(payment.provider_transaction_id, "MTN-TXN-123")
        self.assertEqual(str(payment.provider_charge), "250.50")
        self.assertIsNotNone(payment.confirmed_at)
        self.assertTrue(payment.receipt_number.startswith("BK-RCP-"))
        self.assertTrue(payment.events.filter(event_type="payment.confirmed").exists())

    def test_customer_can_open_own_receipt_but_not_another_customers(self):
        _, payment = self.start_payment()
        self.signed_webhook(payment)
        own = self.client.get(f"/api/v1/me/payments/{payment.id}/receipt/")
        self.assertEqual(own.status_code, 200)
        self.assertContains(own, "Official payment receipt")
        self.client.force_login(self.other)
        forbidden = self.client.get(f"/api/v1/me/payments/{payment.id}/receipt/")
        self.assertEqual(forbidden.status_code, 404)

    def test_owner_payment_detail_report_filters_and_csv_export(self):
        _, payment = self.start_payment()
        self.signed_webhook(payment, charge="100", provider_transaction_id="TXN-REPORT")
        self.client.force_login(self.owner)

        detail = self.client.get(f"/api/v1/owner/payments/{payment.id}/")
        report = self.client.get("/api/v1/owner/payments/report/?status=paid")
        exported = self.client.get("/api/v1/owner/payments/export.csv/?status=paid")

        self.assertEqual(detail.status_code, 200)
        self.assertGreaterEqual(len(detail.data["events"]), 3)
        self.assertEqual(report.status_code, 200)
        self.assertEqual(report.data["summary"]["confirmed"], 1)
        self.assertEqual(Decimal(str(report.data["summary"]["net_revenue"])), payment.amount - Decimal("100"))
        self.assertEqual(exported.status_code, 200)
        self.assertIn("text/csv", exported["Content-Type"])
        self.assertIn("TXN-REPORT", exported.content.decode())


    def test_measured_performance_uses_settled_published_predictions(self):
        self.prediction.result = Prediction.Result.WON
        self.prediction.save(update_fields=["result", "updated_at"])
        response = self.client.get("/api/v1/performance/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["summary"]["settled"], 1)
        self.assertEqual(response.data["summary"]["hit_rate"], 100)

    @patch("catalog.payment_services.check_mobile_money_payment")
    def test_scheduled_reconciliation_recovers_payment_without_open_browser(self, mocked_check):
        purchase, payment = self.start_payment()
        payment.reconciliation_next_at = timezone.now() - timedelta(seconds=1)
        payment.save(update_fields=["reconciliation_next_at", "updated_at"])
        mocked_check.return_value = {
            "success": True,
            "status": "success",
            "message": "Request payment completed successfully.",
            "customer_reference": payment.reference,
            "internal_reference": payment.internal_reference,
            "amount": payment.amount,
            "currency": "UGX",
            "provider": "MTN_UGANDA",
            "provider_transaction_id": "TXN-CRON",
            "completed_at": timezone.now().isoformat(),
        }

        output = StringIO()
        call_command("reconcile_payments", stdout=output)

        payment.refresh_from_db()
        purchase.refresh_from_db()
        self.assertEqual(payment.status, Payment.Status.PAID)
        self.assertTrue(purchase.is_paid)
        self.assertIn("updated=1", output.getvalue())

    def test_owner_has_no_manual_payment_resolution_endpoint(self):
        _, payment = self.start_payment()
        self.client.force_login(self.owner)
        response = self.client.post(f"/api/v1/owner/payments/{payment.id}/resolve/", {"decision": "confirm"}, format="json")
        self.assertEqual(response.status_code, 404)


@override_settings(
    RELWORX_WEBHOOK_SIGNING_KEY="webhook-secret",
    RELWORX_WEBHOOK_URL="http://testserver/api/payments/webhooks/relworx",
    RELWORX_PAYOUTS_ENABLED=True,
    PAYOUT_EXECUTION_MODE="immediate",
    PAYMENT_OWNER_PAYOUT_MSISDN="0700000000",
    PAYMENT_DEVELOPER_PAYOUT_MSISDN="0711111111",
)
class WalletPayoutTests(APITestCase):
    def setUp(self):
        owner_group, _ = Group.objects.get_or_create(name="Product Owner")
        developer_group, _ = Group.objects.get_or_create(name="Developer")
        self.owner = User.objects.create_user(phone="0704000001", first_name="Wallet", last_name="Owner", password="pass")
        self.owner.groups.add(owner_group)
        self.developer = User.objects.create_user(phone="0704000002", first_name="Platform", last_name="Developer", password="pass")
        self.developer.groups.add(developer_group)
        account = wallet_account("owner")
        WalletLedgerEntry.objects.create(
            wallet_account=account, entry_type=WalletLedgerEntry.Type.PAYMENT_CREDIT,
            amount=Decimal("5000"), idempotency_key="test-owner-credit",
        )

    def signature(self, payload):
        timestamp = int(time.time())
        signed = f"http://testserver/api/payments/webhooks/relworx{timestamp}"
        for key in sorted(["status", "customer_reference", "internal_reference"]):
            signed += f"{key}{payload.get(key, '')}"
        value = hmac.new(b"webhook-secret", signed.encode(), hashlib.sha256).hexdigest()
        return f"t={timestamp},v={value}"

    def test_roles_cannot_cross_wallet_boundaries(self):
        self.client.force_login(self.owner)
        self.assertEqual(self.client.get("/api/v1/owner/wallet/").status_code, 200)
        self.assertEqual(self.client.get("/api/v1/developer/wallet/").status_code, 403)
        self.client.force_login(self.developer)
        self.assertEqual(self.client.get("/api/v1/developer/wallet/").status_code, 200)
        self.assertEqual(self.client.get("/api/v1/owner/wallet/").status_code, 403)

    @patch("catalog.payout_services.request_mobile_money_payout")
    def test_live_payout_is_idempotent_and_signed_webhook_settles_it(self, mocked_send):
        mocked_send.return_value = {"success": True, "internal_reference": "RWX-PAYOUT-1"}
        self.client.force_login(self.owner)
        body = {"amount": 1000, "request_token": "same-payout-token-12345"}
        first = self.client.post("/api/v1/owner/payout-requests/", body, format="json")
        second = self.client.post("/api/v1/owner/payout-requests/", body, format="json")
        self.assertEqual(first.status_code, 201)
        self.assertEqual(second.status_code, 201)
        self.assertEqual(PayoutRequest.objects.count(), 1)
        self.assertEqual(mocked_send.call_count, 1)
        payout = PayoutRequest.objects.get()
        payload = {
            "status": "success", "customer_reference": payout.reference,
            "internal_reference": "RWX-PAYOUT-1", "provider_transaction_id": "TX-PAYOUT-1",
            "amount": "1000", "currency": "UGX", "charge": "15",
        }
        response = self.client.post(
            "/api/payments/webhooks/relworx", payload, format="json",
            HTTP_RELWORX_SIGNATURE=self.signature(payload),
        )
        payout.refresh_from_db()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(payout.status, PayoutRequest.Status.COMPLETED)
        self.assertEqual(payout.provider_fee_actual, Decimal("15"))
        self.assertTrue(WalletLedgerEntry.objects.filter(idempotency_key=f"payout:{payout.pk}:fee").exists())
