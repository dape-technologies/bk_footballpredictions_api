import re
from datetime import timedelta

from django.contrib.auth.models import Group
from django.core import mail
from django.core.cache import cache
from django.test import override_settings
from django.utils import timezone
from rest_framework.test import APITestCase

from .models import User, PrivilegedSession, SecurityChallenge, RecoveryCode, EmailNotice


@override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
class PrivilegedSecurityTests(APITestCase):
    def setUp(self):
        cache.clear()
        self.owner = User.objects.create_user(phone="0705111001", password="test-password-secure")
        self.owner.groups.add(Group.objects.get_or_create(name="Product Owner")[0])
        self.customer = User.objects.create_user(phone="0705111002", password="test-password-secure")

    def code(self, action, **extra):
        result = self.client.post("/api/v1/auth/security/", {"action": action, "request_otp": True, "password": "test-password-secure", **extra}, format="json")
        self.assertEqual(result.status_code, 200, result.data)
        return re.search(r"code is (\d{6})", mail.outbox[-1].body).group(1)

    def test_customer_cannot_read_security_or_operations(self):
        self.client.force_login(self.customer)
        self.assertEqual(self.client.get("/api/v1/auth/security/").status_code, 403)
        self.assertEqual(self.client.get("/api/v1/operations/report/").status_code, 403)

    def test_recovery_codes_are_hashed_and_single_use(self):
        self.client.force_login(self.owner)
        otp = self.code("recovery_codes")
        result = self.client.post("/api/v1/auth/security/", {"action": "recovery_codes", "otp": otp}, format="json")
        self.assertEqual(result.status_code, 200)
        self.assertEqual(len(result.data["codes"]), 8)
        code = result.data["codes"][0]
        self.assertFalse(RecoveryCode.objects.filter(digest=code).exists())
        payload = {"action": "destination", "destination": "0749471777", "password": "test-password-secure", "recovery_code": code}
        self.assertEqual(self.client.post("/api/v1/auth/security/", payload, format="json").status_code, 200)
        self.assertEqual(self.client.post("/api/v1/auth/security/", payload, format="json").status_code, 400)

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
        otp = self.code("recovery_codes")
        for _ in range(5):
            self.assertEqual(self.client.post("/api/v1/auth/security/", {"action": "recovery_codes", "otp": "wrong"}, format="json").status_code, 400)
        self.assertEqual(self.client.post("/api/v1/auth/security/", {"action": "recovery_codes", "otp": otp}, format="json").status_code, 400)
