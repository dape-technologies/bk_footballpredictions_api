import re
from datetime import timedelta

from django.contrib.auth.models import Group
from django.core import mail
from django.core.cache import cache
from django.test import override_settings
from django.utils import timezone
from rest_framework.test import APITestCase

from .models import User, PrivilegedSession, SecurityChallenge, EmailNotice


@override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
class PrivilegedSecurityTests(APITestCase):
    def setUp(self):
        cache.clear()
        self.owner = User.objects.create_user(phone="0705111001", password="test-password-secure")
        self.owner.groups.add(Group.objects.get_or_create(name="Product Owner")[0])
        self.developer = User.objects.create_user(phone="0705111003", password="developer-password-secure")
        self.developer.groups.add(Group.objects.get_or_create(name="Developer")[0])
        self.customer = User.objects.create_user(phone="0705111002", password="test-password-secure")

    def code(self, action, **extra):
        result = self.client.post("/api/v1/auth/security/", {"action": action, "request_otp": True, "password": "test-password-secure", **extra}, format="json")
        self.assertEqual(result.status_code, 200, result.data)
        return re.search(r"code is (\d{6})", mail.outbox[-1].body).group(1)

    def test_customer_cannot_read_security_or_operations(self):
        self.client.force_login(self.customer)
        self.assertEqual(self.client.get("/api/v1/auth/security/").status_code, 403)
        self.assertEqual(self.client.get("/api/v1/operations/report/").status_code, 403)

    def test_developer_can_view_only_current_owner_otp(self):
        self.client.force_login(self.owner)
        code = self.code("destination", destination="0749471777")
        challenge = SecurityChallenge.objects.get()
        self.assertNotIn(code, challenge.code_ciphertext)
        owner_payload = self.client.get("/api/v1/auth/security/").data
        self.assertNotIn("current_owner_otps", owner_payload)
        self.client.force_login(self.developer)
        response = self.client.get("/api/v1/auth/security/")
        self.assertEqual(response["Cache-Control"], "no-store, private")
        payload = response.data
        self.assertEqual(payload["current_owner_otps"][0]["code"], code)
        self.assertEqual(payload["current_owner_otps"][0]["purpose"], "destination")

    def test_recovery_code_action_is_removed(self):
        self.client.force_login(self.owner)
        result = self.client.post("/api/v1/auth/security/", {"action": "owner_recovery_code", "password": "test-password-secure"}, format="json")
        self.assertEqual(result.status_code, 400)

    def test_destination_code_cannot_authorize_different_number(self):
        self.client.force_login(self.owner)
        otp = self.code("destination", destination="0749471777")
        response = self.client.post("/api/v1/auth/security/", {"action": "destination", "destination": "0788357724", "otp": otp}, format="json")
        self.assertEqual(response.status_code, 400)
        self.assertEqual(SecurityChallenge.objects.get().attempts, 1)

    def test_expired_session_is_logged_out(self):
        self.client.force_login(self.owner)
        PrivilegedSession.objects.filter(user=self.owner).update(last_seen_at=timezone.now() - timedelta(minutes=21))
        self.assertEqual(self.client.get("/api/v1/auth/security/").status_code, 401)

    def test_login_notice_queues_both_owner_recipients(self):
        self.client.force_login(self.owner)
        notice = EmailNotice.objects.get()
        self.assertEqual(set(notice.recipients), {"makubuyahannington93@gmail.com", "dapetech.2@gmail.com"})

    def test_five_invalid_codes_exhaust_challenge(self):
        self.client.force_login(self.owner)
        otp = self.code("destination", destination="0749471777")
        for _ in range(5):
            self.assertEqual(self.client.post("/api/v1/auth/security/", {"action": "destination", "destination": "0749471777", "otp": "wrong"}, format="json").status_code, 400)
        self.assertEqual(self.client.post("/api/v1/auth/security/", {"action": "destination", "destination": "0749471777", "otp": otp}, format="json").status_code, 400)
