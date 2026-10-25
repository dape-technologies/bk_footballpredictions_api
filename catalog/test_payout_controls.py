from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth.models import Group
from django.core import mail
from django.test import override_settings
from rest_framework.test import APITestCase

from accounts.models import EmailNotice, User
from catalog.models import WalletLedgerEntry
from catalog.wallet_services import wallet_account, create_payout


@override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
                   PAYMENT_OWNER_PAYOUT_MSISDN="0749471777", PAYOUT_EXECUTION_MODE="request_only")
class PayoutControlTests(APITestCase):
    def setUp(self):
        from django.core.cache import cache
        cache.clear()
        self.owner = User.objects.create_user(phone="0704999001", password="test-password")
        self.owner.groups.add(Group.objects.get_or_create(name="Product Owner")[0])
        self.dev = User.objects.create_user(phone="0704999002", password="test-password")
        self.dev.groups.add(Group.objects.get_or_create(name="Developer")[0])
        self.account = wallet_account("owner")
        WalletLedgerEntry.objects.create(wallet_account=self.account, amount=5000,
            entry_type="payment_credit", idempotency_key="control-test")

    def test_freeze_is_developer_only_and_blocks_owner_payout(self):
        self.client.force_login(self.owner)
        self.assertEqual(self.client.patch("/api/v1/developer/payment-gateway/", {"owner_payouts_frozen": True}, format="json").status_code, 403)
        self.client.force_login(self.dev)
        self.assertEqual(self.client.patch("/api/v1/developer/payment-gateway/", {"owner_payouts_frozen": True}, format="json").status_code, 200)
        from django.core.exceptions import ValidationError
        with self.assertRaisesMessage(ValidationError, "Wallet frozen"):
            create_payout("owner", 500, self.owner)

    def test_fee_buffer_prevents_withdraw_all(self):
        from django.core.exceptions import ValidationError
        with self.assertRaisesMessage(ValidationError, "1,500"):
            create_payout("owner", 5000, self.owner)

    def test_owner_payout_requires_password_and_alerts_both_contacts(self):
        self.client.force_login(self.owner)
        mail.outbox.clear()
        endpoint = "/api/v1/owner/payout-requests/"
        self.assertEqual(self.client.post(endpoint, {"amount": 500}, format="json").status_code, 400)
        self.assertEqual(self.client.post(endpoint, {"amount": 500, "password": "wrong"}, format="json").status_code, 400)
        response = self.client.post(endpoint, {"amount": 500, "password": "test-password"}, format="json")
        self.assertEqual(response.status_code, 201)
        notice = EmailNotice.objects.get(dedup_key__startswith="owner-payout:")
        self.assertEqual(set(notice.recipients), {"makubuyahannington93@gmail.com", "dapetech.2@gmail.com"})
        self.assertIn("UGX 500.00", notice.body)
        self.assertIn(response.data["request"]["reference"], notice.body)

    def test_owner_user_listing_hides_developer(self):
        self.client.force_login(self.owner)
        response = self.client.get("/api/v1/owner/customers/")
        self.assertNotIn(self.dev.pk, [item["id"] for item in response.data])
