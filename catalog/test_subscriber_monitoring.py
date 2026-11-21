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
from .models import (
    Package, Payment, PaymentAlert, Purchase, ReconciliationRun,
    SubscriptionContentGrant, SubscriptionOrder, SubscriptionPlan, WalletLedgerEntry,
)
from .payment_services import apply_relworx_result


def success_payload(payment, completed_at=None):
    return {
        "status": "success",
        "customer_reference": payment.reference,
        "internal_reference": payment.internal_reference,
        "amount": payment.amount,
        "currency": payment.currency,
        "completed_at": (completed_at or timezone.now()).isoformat(),
        "message": "Paid",
    }


class WeeklySubscriberMonitoringTests(APITestCase):
    def setUp(self):
        self.owner = User.objects.create_superuser(phone="0702600001", password="owner-pass")
        self.customer = User.objects.create_user(
            phone="0702600002", first_name="Waiting", last_name="Member", password="customer-pass",
        )
        self.plan, _ = SubscriptionPlan.objects.update_or_create(
            key="weekly",
            defaults={"name": "Weekly Plan", "price": 12000, "currency": "UGX", "duration_days": 7, "is_active": True},
        )
        self.order = SubscriptionOrder.objects.create(
            user=self.customer, plan=self.plan, price_snapshot=12000,
            currency_snapshot="UGX", duration_days_snapshot=7,
        )
        self.payment = Payment.objects.create(
            subscription_order=self.order, user=self.customer, amount=Decimal("12000"),
            base_amount=Decimal("12000"), provider_net_amount=Decimal("12000"),
            currency="UGX", payer_msisdn="+256702600002", status=Payment.Status.PENDING,
            internal_reference="weekly-monitoring-payment",
        )
        apply_relworx_result(self.payment, success_payload(self.payment), source="test")

    def test_owner_sees_paid_waiting_subscriber_and_countdown(self):
        self.client.force_login(self.owner)
        response = self.client.get("/api/v1/owner/weekly-subscribers/?status=waiting")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["summary"]["active"], 1)
        self.assertEqual(response.data["summary"]["waiting"], 1)
        row = response.data["results"][0]
        self.assertEqual(row["customer"]["phone"], self.customer.phone)
        self.assertEqual(row["content_status"], "waiting")
        self.assertGreater(row["remaining_seconds"], 6 * 86400)
        self.assertEqual(row["payment"]["reference"], self.payment.reference)

    def test_open_weekly_content_changes_waiting_to_available(self):
        package = Package.objects.create(
            name="Fresh Weekly", slug="fresh-weekly-monitor", offer_type=Package.OfferType.WEEKLY,
            package_type="Accumulator", price=0, closes_at=timezone.now() + timedelta(days=1),
            betslip_link="https://example.com/weekly", code="WEEKLY-MONITOR",
        )
        SubscriptionContentGrant.objects.create(order=self.order, package=package)
        self.client.force_login(self.owner)

        response = self.client.get("/api/v1/owner/weekly-subscribers/?status=available")

        self.assertEqual(response.data["summary"]["waiting"], 0)
        self.assertEqual(response.data["summary"]["available"], 1)
        self.assertEqual(response.data["results"][0]["available_content_count"], 1)

    def test_customer_cannot_read_owner_subscriber_roster(self):
        self.client.force_login(self.customer)
        self.assertEqual(self.client.get("/api/v1/owner/weekly-subscribers/").status_code, 403)


@override_settings(DEVELOPER_COMMISSION_RATE=Decimal("0.20"))
class PaymentCorrectnessTests(APITestCase):
    def setUp(self):
        self.customer = User.objects.create_user(phone="0702700001", password="customer-pass")
        self.package = Package.objects.create(
            name="Duplicate Guard", slug="duplicate-guard", package_type="Accumulator",
            price=10000, closes_at=timezone.now() + timedelta(days=1),
            betslip_link="https://example.com/guard", code="GUARD-1",
        )
        self.purchase = Purchase.objects.create(
            user=self.customer, package=self.package, price_snapshot=10000, currency_snapshot="UGX",
        )

    def make_payment(self, reference):
        return Payment.objects.create(
            purchase=self.purchase, package=self.package, user=self.customer,
            amount=Decimal("10000"), base_amount=Decimal("10000"),
            provider_net_amount=Decimal("10000"), currency="UGX",
            payer_msisdn="+256702700001", status=Payment.Status.PENDING,
            internal_reference=reference,
        )

    def test_distinct_second_success_is_credited_and_classified_without_duplicate_access(self):
        first = self.make_payment("primary-success")
        apply_relworx_result(first, success_payload(first), source="webhook")
        self.purchase.refresh_from_db()
        original_paid_at = self.purchase.paid_at
        second = self.make_payment("second-success")

        apply_relworx_result(second, success_payload(second), source="webhook")

        second.refresh_from_db()
        self.purchase.refresh_from_db()
        self.assertEqual(second.settlement_classification, Payment.SettlementClassification.DUPLICATE)
        self.assertEqual(second.duplicate_of_id, first.pk)
        self.assertEqual(self.purchase.paid_at, original_paid_at)
        self.assertEqual(WalletLedgerEntry.objects.filter(payment=second).count(), 2)
        self.assertTrue(second.events.filter(event_type="payment.duplicate_entitlement_settlement").exists())
        self.assertTrue(PaymentAlert.objects.filter(payment=second, severity=PaymentAlert.Severity.CRITICAL).exists())

    def test_same_payment_webhook_replay_remains_primary_and_idempotent(self):
        payment = self.make_payment("replayed-success")
        payload = success_payload(payment)
        apply_relworx_result(payment, payload, source="webhook")
        apply_relworx_result(payment, payload, source="webhook")
        payment.refresh_from_db()

        self.assertEqual(payment.settlement_classification, Payment.SettlementClassification.PRIMARY)
        self.assertIsNone(payment.duplicate_of_id)
        self.assertEqual(WalletLedgerEntry.objects.filter(payment=payment).count(), 2)

    @patch("catalog.views.request_mobile_money_payment")
    def test_unresolved_payment_returns_machine_readable_duplicate_warning(self, mocked_request):
        payment = self.make_payment("pending-warning")
        self.client.force_login(self.customer)

        response = self.client.post(
            f"/api/v1/me/purchases/{self.purchase.pk}/payment-attempts/",
            {"phone": "0702700001"}, format="json",
        )

        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.data["code"], "payment_in_progress")
        self.assertEqual(response.data["payment"]["id"], payment.pk)
        mocked_request.assert_not_called()

    @patch("catalog.views.request_mobile_money_payment")
    def test_unresolved_weekly_payment_blocks_a_different_order(self, mocked_request):
        plan, _ = SubscriptionPlan.objects.update_or_create(
            key="weekly",
            defaults={"name": "Weekly Plan", "price": 12000, "currency": "UGX", "duration_days": 7, "is_active": True},
        )
        first_order = SubscriptionOrder.objects.create(
            user=self.customer, plan=plan, price_snapshot=12000, currency_snapshot="UGX", duration_days_snapshot=7,
        )
        first_payment = Payment.objects.create(
            subscription_order=first_order, user=self.customer, amount=12000, currency="UGX",
            payer_msisdn="+256702700001", status=Payment.Status.NEEDS_REVIEW,
        )
        second_order = SubscriptionOrder.objects.create(
            user=self.customer, plan=plan, price_snapshot=12000, currency_snapshot="UGX", duration_days_snapshot=7,
        )
        self.client.force_login(self.customer)

        response = self.client.post(
            f"/api/v1/me/subscriptions/{second_order.pk}/payment-attempts/",
            {"phone": "0702700001"}, format="json",
        )

        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.data["code"], "payment_needs_review")
        self.assertEqual(response.data["payment"]["id"], first_payment.pk)
        mocked_request.assert_not_called()

    def test_second_weekly_success_is_credited_without_extending_access(self):
        plan, _ = SubscriptionPlan.objects.update_or_create(
            key="weekly",
            defaults={"name": "Weekly Plan", "price": 12000, "currency": "UGX", "duration_days": 7, "is_active": True},
        )
        first_order = SubscriptionOrder.objects.create(
            user=self.customer, plan=plan, price_snapshot=12000, currency_snapshot="UGX", duration_days_snapshot=7,
        )
        first = Payment.objects.create(
            subscription_order=first_order, user=self.customer, amount=12000, base_amount=12000,
            provider_net_amount=12000, currency="UGX", payer_msisdn="+256702700001",
            status=Payment.Status.PENDING, internal_reference="weekly-primary",
        )
        apply_relworx_result(first, success_payload(first), source="webhook")
        first_order.refresh_from_db()
        original_end = first_order.access_ends_at
        second_order = SubscriptionOrder.objects.create(
            user=self.customer, plan=plan, price_snapshot=12000, currency_snapshot="UGX", duration_days_snapshot=7,
        )
        second = Payment.objects.create(
            subscription_order=second_order, user=self.customer, amount=12000, base_amount=12000,
            provider_net_amount=12000, currency="UGX", payer_msisdn="+256702700001",
            status=Payment.Status.PENDING, internal_reference="weekly-duplicate",
        )

        apply_relworx_result(second, success_payload(second), source="webhook")

        first_order.refresh_from_db()
        second_order.refresh_from_db()
        second.refresh_from_db()
        self.assertEqual(first_order.access_ends_at, original_end)
        self.assertFalse(second_order.is_paid)
        self.assertEqual(second.settlement_classification, Payment.SettlementClassification.DUPLICATE)
        self.assertEqual(WalletLedgerEntry.objects.filter(payment=second).count(), 2)


class ReconciliationMonitoringTests(APITestCase):
    def setUp(self):
        developer_group, _ = Group.objects.get_or_create(name="Developer")
        self.developer = User.objects.create_user(phone="0702800001", password="developer-pass")
        self.developer.groups.add(developer_group)

    def test_commands_record_runs_and_developer_can_read_health(self):
        call_command("reconcile_payments", stdout=StringIO())
        call_command("reconcile_payouts", stdout=StringIO())
        self.client.force_login(self.developer)

        response = self.client.get("/api/v1/developer/reconciliation-health/")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["overall"], "healthy")
        self.assertEqual(response.data["jobs"]["payments"]["last_run_status"], "success")
        self.assertEqual(response.data["jobs"]["payouts"]["last_run_status"], "success")
        self.assertTrue(response.data["jobs"]["payments"]["recent_runs"])

    @override_settings(RECONCILIATION_STALE_AFTER_MINUTES=15)
    def test_old_success_is_reported_as_stale(self):
        call_command("reconcile_payments", stdout=StringIO())
        call_command("reconcile_payouts", stdout=StringIO())
        ReconciliationRun.objects.update(finished_at=timezone.now() - timedelta(minutes=16))
        self.client.force_login(self.developer)

        response = self.client.get("/api/v1/developer/reconciliation-health/")

        self.assertEqual(response.data["overall"], "stale")
