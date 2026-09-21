from datetime import date, timedelta

from django.core.cache import cache
from django.urls import reverse
from django.test import Client
from rest_framework.test import APITestCase

from .models import User


class AuthenticationTests(APITestCase):
    def setUp(self):
        cache.clear()

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

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.data["is_owner"])
        self.assertIn("sessionid", self.client.cookies)
