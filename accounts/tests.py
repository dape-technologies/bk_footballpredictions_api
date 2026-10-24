from datetime import date, timedelta
import re

from django.contrib.auth import authenticate
from django.contrib.auth.models import Group
from django.core import mail
from django.core.cache import cache
from django.core.management import call_command
from django.test import Client, override_settings
from django.urls import reverse
from rest_framework.test import APITestCase

from .models import SecurityChallenge, User


@override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
class AuthenticationTests(APITestCase):
    def setUp(self):
        cache.clear()

    def complete_mfa(self, endpoint, challenge):
        code = re.search(r"code is (\d{6})", mail.outbox[-1].body).group(1)
        return self.client.post(endpoint, {
            "challenge_token": challenge.data["challenge_token"], "otp": code,
        }, format="json")

    def test_signed_out_me_response_is_unauthorized_not_forbidden(self):
        response = self.client.get(reverse("me"))

        self.assertEqual(response.status_code, 401)

    def test_login_requires_csrf_and_accepts_the_local_frontend_origin(self):
        client = Client(enforce_csrf_checks=True, HTTP_HOST="127.0.0.1:8000")
        without_token = client.post(
            reverse("login"),
            {"phone": "0701112200", "password": "pass"},
            content_type="application/json",
            HTTP_ORIGIN="http://localhost:5173",
        )
        csrf_token = client.get(reverse("csrf-token")).json()["csrfToken"]
        with_token = client.post(
            reverse("login"),
            {"phone": "0701112200", "password": "pass"},
            content_type="application/json",
            HTTP_X_CSRFTOKEN=csrf_token,
            HTTP_ORIGIN="http://localhost:5173",
        )

        self.assertEqual(without_token.status_code, 403)
        self.assertEqual(with_token.status_code, 400)

    def test_customer_can_register_and_session_is_created(self):
        response = self.client.post(
            reverse("register"),
            {
                "first_name": "Amina",
                "surname": "Nabirye",
                "date_of_birth": "1998-05-12",
                "phone": "0701112233",
                "password": "pass",
                "password_confirm": "pass",
            },
            format="json",
        )

        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.data["phone"], "0701112233")
        self.assertEqual(response.data["first_name"], "Amina")
        self.assertEqual(response.data["surname"], "Nabirye")
        self.assertIn("sessionid", self.client.cookies)

    def test_registration_rejects_mismatched_passwords(self):
        response = self.client.post(
            reverse("register"),
            {
                "first_name": "Amina",
                "surname": "Nabirye",
                "date_of_birth": "1998-05-12",
                "phone": "0701112233",
                "password": "pass",
                "password_confirm": "word",
            },
            format="json",
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn("password_confirm", response.data)

    def test_registration_rejects_duplicate_normalized_phone(self):
        User.objects.create_user(phone="0701112233", first_name="First", last_name="User", password="pass")

        response = self.client.post(
            reverse("register"),
            {
                "first_name": "Second",
                "surname": "User",
                "date_of_birth": "1998-05-12",
                "phone": "+256 701 112 233",
                "password": "pass",
                "password_confirm": "pass",
            },
            format="json",
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn("phone", response.data)

    def test_four_character_password_is_accepted(self):
        response = self.client.post(
            reverse("register"),
            {
                "first_name": "Short",
                "surname": "Password",
                "date_of_birth": "1998-05-12",
                "phone": "0701112255",
                "password": "1234",
                "password_confirm": "1234",
            },
            format="json",
        )

        self.assertEqual(response.status_code, 201)

    def test_registration_rejects_user_younger_than_eighteen(self):
        today = date.today()
        underage_birth_date = today.replace(year=today.year - 18) + timedelta(days=1)

        response = self.client.post(
            reverse("register"),
            {
                "first_name": "Young",
                "surname": "Member",
                "date_of_birth": underage_birth_date.isoformat(),
                "phone": "0701112266",
                "password": "pass",
                "password_confirm": "pass",
            },
            format="json",
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn("date_of_birth", response.data)

    def test_registration_accepts_user_on_eighteenth_birthday(self):
        today = date.today()
        try:
            birth_date = today.replace(year=today.year - 18)
        except ValueError:
            birth_date = today.replace(year=today.year - 18, day=28)

        response = self.client.post(
            reverse("register"),
            {
                "first_name": "Adult",
                "surname": "Member",
                "date_of_birth": birth_date.isoformat(),
                "phone": "0701112277",
                "password": "pass",
                "password_confirm": "pass",
            },
            format="json",
        )

        self.assertEqual(response.status_code, 201)

    def test_blocked_customer_cannot_log_in(self):
        User.objects.create_user(
            phone="0701112244",
            first_name="Blocked",
            last_name="User",
            password="strong-pass-27",
            is_blocked=True,
        )

        response = self.client.post(
            reverse("login"),
            {"phone": "0701112244", "password": "strong-pass-27"},
            format="json",
        )

        self.assertEqual(response.status_code, 400)

    def test_customer_login_still_completes_without_otp(self):
        customer = User.objects.create_user(phone="0701112245", password="strong-pass-27")
        response = self.client.post(reverse("login"), {
            "phone": customer.phone, "password": "strong-pass-27",
        }, format="json")
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.data["is_owner"])
        self.assertFalse(response.data["is_developer"])
        self.assertIn("_auth_user_id", self.client.session)
        self.assertEqual(len(mail.outbox), 0)

    def test_customer_cannot_create_an_admin_session(self):
        User.objects.create_user(
            phone="0701112288",
            first_name="Regular",
            last_name="Customer",
            password="strong-pass-27",
        )

        response = self.client.post(
            reverse("admin-login"),
            {"phone": "0701112288", "password": "strong-pass-27"},
            format="json",
        )

        self.assertEqual(response.status_code, 400)
        self.assertNotIn("sessionid", self.client.cookies)

    def test_product_owner_can_create_an_admin_session(self):
        owner = User.objects.create_user(
            phone="0701112299",
            first_name="Product",
            last_name="Owner",
            password="strong-pass-27",
            is_staff=True,
            is_superuser=True,
        )

        response = self.client.post(
            reverse("admin-login"),
            {"phone": owner.phone, "password": "strong-pass-27"},
            format="json",
        )

        self.assertEqual(response.status_code, 202)
        self.assertTrue(response.data["otp_required"])
        self.assertEqual(response["Cache-Control"], "no-store, private")
        self.assertEqual(set(mail.outbox[-1].to), {"makubuyahannington93@gmail.com", "dapetech.2@gmail.com"})
        code = re.search(r"code is (\d{6})", mail.outbox[-1].body).group(1)
        self.assertNotIn(code, SecurityChallenge.objects.get(purpose="login").code_ciphertext)
        self.assertNotIn("_auth_user_id", self.client.session)
        response = self.complete_mfa(reverse("admin-login"), response)
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.data["is_owner"])
        self.assertIn("_auth_user_id", self.client.session)

    def test_developer_can_create_a_separate_privileged_session(self):
        developer = User.objects.create_user(
            phone="0701112300", first_name="Platform", last_name="Developer",
            password="strong-pass-27", is_staff=False,
        )
        group, _ = Group.objects.get_or_create(name="Developer")
        developer.groups.add(group)
        response = self.client.post(
            reverse("admin-login"),
            {"phone": developer.phone, "password": "strong-pass-27"},
            format="json",
        )
        self.assertEqual(response.status_code, 202)
        self.assertEqual(set(mail.outbox[-1].to), {"dapetech.2@gmail.com"})
        self.assertNotIn("_auth_user_id", self.client.session)
        response = self.complete_mfa(reverse("admin-login"), response)
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.data["is_developer"])
        self.assertFalse(response.data["is_owner"])
        self.assertIn("sessionid", self.client.cookies)

    @override_settings(
        ADMIN_LOGIN_USERNAME="admin",
        ADMIN_SEED_PHONE="0700000000",
        ADMIN_SEED_PASSWORD="bkpassword",
    )
    def test_seeded_admin_alias_is_idempotent_and_can_log_in(self):
        call_command("ensure_admin", verbosity=0)
        owner = User.objects.get(phone="0700000000")
        owner.set_password("owner-selected-password")
        owner.save(update_fields=["password"])
        call_command("ensure_admin", verbosity=0)

        owner = User.objects.get(phone="0700000000")
        self.assertEqual(User.objects.filter(phone="0700000000").count(), 1)
        self.assertTrue(owner.is_staff)
        self.assertTrue(owner.is_superuser)
        self.assertTrue(owner.check_password("owner-selected-password"))
        self.assertFalse(owner.check_password("bkpassword"))
        self.assertEqual(authenticate(username="admin", password="owner-selected-password"), owner)

        response = self.client.post(
            reverse("admin-login"),
            {"phone": "admin", "password": "owner-selected-password"},
            format="json",
        )

        self.assertEqual(response.status_code, 202)
        response = self.complete_mfa(reverse("admin-login"), response)
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.data["is_owner"])

    def test_privileged_public_login_also_requires_otp(self):
        owner = User.objects.create_superuser(phone="0701112399", password="strong-pass-27")
        response = self.client.post(reverse("login"), {"phone": owner.phone, "password": "strong-pass-27"}, format="json")
        self.assertEqual(response.status_code, 202)
        self.assertNotIn("_auth_user_id", self.client.session)
        response = self.complete_mfa(reverse("login"), response)
        self.assertEqual(response.status_code, 200)
        self.assertIn("_auth_user_id", self.client.session)

    def test_login_otp_is_session_bound_and_exhausts_after_five_attempts(self):
        owner = User.objects.create_superuser(phone="0701112400", password="strong-pass-27")
        challenge = self.client.post(reverse("admin-login"), {"phone": owner.phone, "password": "strong-pass-27"}, format="json")
        token = challenge.data["challenge_token"]
        other_client = self.client_class()
        self.assertEqual(other_client.post(reverse("admin-login"), {"challenge_token": token, "otp": "000000"}, format="json").status_code, 400)
        for _ in range(5):
            self.assertEqual(self.client.post(reverse("admin-login"), {"challenge_token": token, "otp": "000000"}, format="json").status_code, 400)
        code = re.search(r"code is (\d{6})", mail.outbox[0].body).group(1)
        self.assertEqual(self.client.post(reverse("admin-login"), {"challenge_token": token, "otp": code}, format="json").status_code, 400)

    @override_settings(
        ADMIN_LOGIN_USERNAME="admin",
        ADMIN_SEED_PHONE="0700000000",
        ADMIN_SEED_PASSWORD="replacement-password",
    )
    def test_seeded_admin_password_can_be_explicitly_reset(self):
        owner = User.objects.create_superuser(
            phone="0700000000",
            first_name="System",
            last_name="Administrator",
            date_of_birth="1990-01-01",
            password="owner-selected-password",
        )

        call_command("ensure_admin", reset_password=True, verbosity=0)

        owner.refresh_from_db()
        self.assertTrue(owner.check_password("replacement-password"))
        self.assertFalse(owner.check_password("owner-selected-password"))

    @override_settings(
        ADMIN_LOGIN_USERNAME="admin",
        ADMIN_SEED_PHONE="0700000000",
        ADMIN_SEED_PASSWORD="bkpassword",
    )
    def test_seeded_admin_alias_can_use_django_admin(self):
        call_command("ensure_admin", verbosity=0)

        response = self.client.get(reverse("admin:login"))
        self.assertRedirects(response, "/owner/login?next=/django-admin/", fetch_redirect_response=False)
        challenge = self.client.post(reverse("admin-login"), {"phone": "admin", "password": "bkpassword"}, format="json")
        self.assertEqual(challenge.status_code, 202)
        self.assertEqual(self.complete_mfa(reverse("admin-login"), challenge).status_code, 200)
        self.assertIn("_auth_user_id", self.client.session)
