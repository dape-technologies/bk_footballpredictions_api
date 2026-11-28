from datetime import timedelta
from unittest.mock import patch

from django.utils import timezone
from rest_framework.test import APITestCase

from accounts.models import User
from .models import Package, Payment, SubscriptionContentGrant, SubscriptionOrder, SubscriptionPlan
from .payment_services import apply_relworx_result


class WeeklySubscriptionTests(APITestCase):
    def setUp(self):
        self.customer = User.objects.create_user(
            phone="0702400001", first_name="Weekly", last_name="Member", password="member-pass-27",
        )
        self.owner = User.objects.create_superuser(
            phone="0702400002", first_name="BK", last_name="Owner", password="owner-pass-27",
        )
        self.plan, _ = SubscriptionPlan.objects.update_or_create(
            key="weekly",
            defaults={
                "name": "Weekly Plan", "description": "Seven days of access.",
                "price": 12000, "currency": "UGX", "duration_days": 7, "is_active": True,
            },
        )
        self.weekly = Package.objects.create(
            name="Weekly Current", slug="weekly-current", offer_type=Package.OfferType.WEEKLY,
            package_type="Accumulator", price=0, closes_at=timezone.now() + timedelta(days=2),
            betslip_link="https://example.com/weekly/current", code="WK-CURRENT",
        )
        self.one_off = Package.objects.create(
            name="One Off", slug="one-off", offer_type=Package.OfferType.ONE_OFF,
            package_type="Accumulator", price=10000, closes_at=timezone.now() + timedelta(days=2),
            betslip_link="https://example.com/one-off", code="ONE-OFF",
        )

    def create_order(self):
        self.client.force_login(self.customer)
        response = self.client.post("/api/v1/me/subscriptions/", {}, format="json")
        self.assertEqual(response.status_code, 201)
        return SubscriptionOrder.objects.get(pk=response.data["id"])

    def pay_order(self, order=None):
        order = order or self.create_order()
        with patch("catalog.views.request_mobile_money_payment") as mocked_request:
            mocked_request.return_value = {
                "success": True, "message": "Pending", "internal_reference": f"weekly-{order.pk}",
            }
            response = self.client.post(
                f"/api/v1/me/subscriptions/{order.pk}/payment-attempts/",
                {"phone": "0773454899"}, format="json",
            )
        self.assertEqual(response.status_code, 202)
        payment = Payment.objects.get(pk=response.data["id"])
        confirmed_at = timezone.now()
        apply_relworx_result(payment, {
            "status": "success",
            "customer_reference": payment.reference,
            "internal_reference": payment.internal_reference,
            "amount": payment.amount,
            "currency": payment.currency,
            "completed_at": confirmed_at.isoformat(),
            "message": "Paid",
        }, source="test")
        order.refresh_from_db()
        return order, payment, confirmed_at

    def test_public_catalog_keeps_weekly_content_out_of_one_off_packages(self):
        response = self.client.get("/api/v1/packages/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual([item["slug"] for item in response.data], ["one-off"])

        weekly = self.client.get("/api/v1/weekly-plan/")
        self.assertEqual(weekly.status_code, 200)
        self.assertEqual(weekly.data["duration_days"], 7)

    def test_confirmation_starts_exact_window_and_grants_current_content(self):
        order, payment, confirmed_at = self.pay_order()

        self.assertEqual(order.access_starts_at, confirmed_at)
        self.assertEqual(order.access_ends_at, confirmed_at + timedelta(days=7))
        self.assertTrue(SubscriptionContentGrant.objects.filter(order=order, package=self.weekly).exists())
        self.assertFalse(SubscriptionContentGrant.objects.filter(order=order, package=self.one_off).exists())
        self.assertEqual(self.client.get("/api/v1/me/weekly-content/").status_code, 200)
        access = self.client.post(f"/api/v1/me/weekly-content/{self.weekly.pk}/access/", {}, format="json")
        self.assertEqual(access.status_code, 200)
        self.assertEqual(access.data["url"], self.weekly.betslip_link)
        self.assertEqual(payment.product_name, self.plan.name)

    def test_early_renewal_is_blocked_and_expiry_revokes_every_route(self):
        order, _, _ = self.pay_order()
        self.assertEqual(self.client.post("/api/v1/me/subscriptions/", {}, format="json").status_code, 409)

        order.access_ends_at = timezone.now() - timedelta(seconds=1)
        order.save(update_fields=["access_ends_at", "updated_at"])

        self.assertEqual(self.client.get("/api/v1/me/weekly-content/").status_code, 403)
        self.assertEqual(
            self.client.post(f"/api/v1/me/weekly-content/{self.weekly.pk}/access/", {}, format="json").status_code,
            403,
        )
        self.assertEqual(self.client.post("/api/v1/me/subscriptions/", {}, format="json").status_code, 201)

    def test_active_subscriber_can_purchase_a_one_off_package(self):
        self.pay_order()

        purchase_response = self.client.post(
            "/api/v1/me/purchases/", {"package_id": self.one_off.pk}, format="json",
        )
        self.assertEqual(purchase_response.status_code, 201)

        with patch("catalog.views.request_mobile_money_payment") as mocked_request:
            mocked_request.return_value = {
                "success": True, "message": "Pending", "internal_reference": "one-off-active-member",
            }
            payment_response = self.client.post(
                f"/api/v1/me/purchases/{purchase_response.data['id']}/payment-attempts/",
                {"phone": "0773454899"}, format="json",
            )

        self.assertEqual(payment_response.status_code, 202)
        self.assertEqual(payment_response.data["checkout_type"], "package")

    def test_weekly_content_cannot_be_purchased_as_a_one_off(self):
        self.pay_order()

        response = self.client.post(
            "/api/v1/me/purchases/", {"package_id": self.weekly.pk}, format="json",
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn("Weekly content is included through the Weekly Plan.", str(response.data))

    def test_owner_upload_is_granted_to_active_subscriber_and_duplicate_success_is_idempotent(self):
        order, payment, confirmed_at = self.pay_order()
        self.client.force_login(self.owner)
        response = self.client.post("/api/v1/owner/packages/", {
            "name": "Fresh Weekly",
            "slug": "fresh-weekly",
            "offer_type": "weekly",
            "package_type": "Accumulator",
            "price": 0,
            "currency": "UGX",
            "win_probability": 80,
            "closes_at": (timezone.now() + timedelta(days=1)).isoformat(),
            "betslip_link": "https://example.com/weekly/fresh",
            "code": "WK-FRESH",
            "is_active": True,
        }, format="json")
        self.assertEqual(response.status_code, 201)
        fresh = Package.objects.get(pk=response.data["id"])
        self.assertTrue(SubscriptionContentGrant.objects.filter(order=order, package=fresh).exists())

        payload = {
            "status": "success", "customer_reference": payment.reference,
            "internal_reference": payment.internal_reference, "amount": payment.amount,
            "currency": payment.currency, "completed_at": confirmed_at.isoformat(),
        }
        apply_relworx_result(payment, payload, source="duplicate-test")
        self.assertEqual(SubscriptionContentGrant.objects.filter(order=order, package=self.weekly).count(), 1)

    def test_weekly_offer_type_cannot_replace_an_issued_offer(self):
        order = SubscriptionOrder.objects.create(
            user=self.customer, plan=self.plan, price_snapshot=self.plan.price,
            currency_snapshot="UGX", duration_days_snapshot=7,
        )
        SubscriptionContentGrant.objects.create(order=order, package=self.weekly)
        self.client.force_login(self.owner)

        response = self.client.patch(
            f"/api/v1/owner/packages/{self.weekly.pk}/", {"offer_type": "one_off"}, format="json",
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn("offer_type", response.data)
