from django.test import TestCase
from django.urls import resolve, reverse

from config.views import spa_index


class HealthCheckTests(TestCase):
    def test_health_check_returns_ok(self):
        response = self.client.get(reverse("health-check"))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "ok")


class AdminRoutingTests(TestCase):
    def test_custom_admin_path_is_served_by_the_spa(self):
        self.assertIs(resolve("/admin").func, spa_index)
        self.assertIs(resolve("/admin/login").func, spa_index)

    def test_django_admin_uses_a_separate_path(self):
        self.assertEqual(reverse("admin:index"), "/django-admin/")
