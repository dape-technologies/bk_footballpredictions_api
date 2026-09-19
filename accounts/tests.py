from django.urls import reverse
from rest_framework.test import APITestCase

from .models import User


class AuthenticationTests(APITestCase):
    def test_customer_can_register_and_session_is_created(self):
        response = self.client.post(
            reverse("register"),
            {"display_name": "Amina N.", "phone": "0701112233", "password": "strong-pass-27"},
            format="json",
        )

        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.data["phone"], "0701112233")
        self.assertIn("sessionid", self.client.cookies)

    def test_blocked_customer_cannot_log_in(self):
        User.objects.create_user(
            phone="0701112244",
            display_name="Blocked User",
            password="strong-pass-27",
            is_blocked=True,
        )

        response = self.client.post(
            reverse("login"),
            {"phone": "0701112244", "password": "strong-pass-27"},
            format="json",
        )

        self.assertEqual(response.status_code, 400)

