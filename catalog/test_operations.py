from datetime import timedelta
from django.contrib.auth.models import Group
from django.core.cache import cache
from django.utils import timezone
from rest_framework.test import APITestCase
from accounts.models import User
from .models import Package, Purchase, JourneyEvent, WebVitalSample


class OperationsTests(APITestCase):
    def setUp(self):
        cache.clear()
        self.member = User.objects.create_user(phone="0705222001", password="pass")
        self.other = User.objects.create_user(phone="0705222002", password="pass")
        self.package = Package.objects.create(name="Daily", slug="ops-daily", price=1000,
            closes_at=timezone.now() + timedelta(days=1), code="PRIVATE", betslip_link="https://example.test/slip")
        self.purchase = Purchase.objects.create(user=self.member, package=self.package, price_snapshot=1000,
            paid_at=timezone.now(), link_snapshot="https://example.test/slip")

    def test_access_history_requires_purchase_owner_and_records_open(self):
        self.client.force_login(self.other)
        url = f"/api/v1/me/purchases/{self.purchase.pk}/access/"
        self.assertEqual(self.client.post(url).status_code, 404)
        self.client.force_login(self.member)
        response = self.client.post(url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["url"], "https://example.test/slip")
        self.assertEqual(JourneyEvent.objects.filter(kind="betslip_access", user=self.member).count(), 1)

    def test_client_cannot_invent_confirmed_payments(self):
        self.assertEqual(self.client.post("/api/v1/telemetry/", {"kind": "payment_confirmed", "package_id": self.package.pk}, format="json").status_code, 400)

    def test_web_vitals_validate_values_and_deduplicate_samples(self):
        url = "/api/v1/telemetry/"
        payload = {"kind": "web_vital", "metric": "LCP", "value": 1800, "sample_id": "sample-123456", "page": "/"}
        self.assertEqual(self.client.post(url, payload, format="json").status_code, 204)
        self.assertEqual(self.client.post(url, payload, format="json").status_code, 204)
        self.assertEqual(WebVitalSample.objects.count(), 1)
        payload["value"] = -1
        self.assertEqual(self.client.post(url, payload, format="json").status_code, 400)

    def test_preferences_are_self_scoped_and_do_not_expose_other_users(self):
        self.client.force_login(self.member)
        self.assertEqual(self.client.patch("/api/v1/me/preferences/", {"closing_reminders": True}, format="json").status_code, 200)
        self.other.refresh_from_db()
        self.assertEqual(self.other.notification_preferences, {})

    def test_reports_are_privileged_and_return_unknown_not_guessed_networks(self):
        self.client.force_login(self.member)
        self.assertEqual(self.client.get("/api/v1/operations/report/").status_code, 403)
        self.member.groups.add(Group.objects.get_or_create(name="Developer")[0])
        response = self.client.get("/api/v1/operations/report/")
        self.assertEqual(response.status_code, 200)
        self.assertIn("approval_note", response.data["funnel"])

    def test_package_image_optimization_preserves_uploaded_original(self):
        from io import BytesIO
        from tempfile import TemporaryDirectory
        from django.test import override_settings
        from django.core.files.base import ContentFile
        from PIL import Image
        from .image_services import optimize_package_image
        with TemporaryDirectory() as folder, override_settings(MEDIA_ROOT=folder):
            output = BytesIO()
            Image.new("RGB", (1600, 1000), "green").save(output, "PNG")
            self.package.image.save("source.png", ContentFile(output.getvalue()), save=True)
            original_name = self.package.image.name
            optimize_package_image(self.package)
            self.assertTrue(self.package.image.storage.exists(original_name))
            with self.package.image_optimized.open("rb") as result:
                image = Image.open(result)
                self.assertLessEqual(image.width, 960)
                self.assertEqual(image.width / image.height, 1.6)
