from datetime import timedelta
from io import BytesIO
from tempfile import TemporaryDirectory

from django.contrib.auth.models import Group
from django.core.files.base import ContentFile
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import override_settings
from django.utils import timezone
from PIL import Image
from rest_framework.test import APITestCase

from accounts.models import User
from .models import FreePick, Package


def png_bytes(color="green"):
    image = BytesIO()
    Image.new("RGB", (640, 900), color).save(image, "PNG")
    return image.getvalue()


class PublicFreePickTests(APITestCase):
    def test_empty_state_is_available_without_authentication(self):
        response = self.client.get("/api/v1/free-pick/")

        self.assertEqual(response.status_code, 204)
        self.assertEqual(self.client.get("/api/v1/free-pick/image/").status_code, 404)

    def test_live_free_pick_reveals_until_its_closing_time(self):
        with TemporaryDirectory() as folder, override_settings(PRIVATE_MEDIA_ROOT=folder):
            free_pick = FreePick.objects.create(
                name="Today's free pick",
                package_type="Free accumulator",
                win_probability=76,
                closes_at=timezone.now() + timedelta(hours=2),
                betslip_link="https://example.com/free-pick",
                code="BK-FREE-76",
                is_active=True,
            )
            free_pick.reveal_image.save("free-pick.png", ContentFile(png_bytes()), save=True)

            response = self.client.get("/api/v1/free-pick/")

            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.data["id"], free_pick.id)
            self.assertEqual(response.data["code"], "BK-FREE-76")
            self.assertTrue(response.data["reveal_image_url"].endswith("/api/v1/free-pick/image/"))
            self.assertEqual(self.client.get("/api/v1/packages/").data, [])

            image_response = self.client.get("/api/v1/free-pick/image/")
            self.assertEqual(image_response.status_code, 200)
            self.assertEqual(image_response["Cache-Control"], "no-store, private")
            image_response.close()

            free_pick.closes_at = timezone.now() - timedelta(seconds=1)
            free_pick.save(update_fields=["closes_at", "updated_at"])

            self.assertEqual(self.client.get("/api/v1/free-pick/").status_code, 204)
            self.assertEqual(self.client.get("/api/v1/free-pick/image/").status_code, 404)


class OwnerFreePickTests(APITestCase):
    def setUp(self):
        self.owner = User.objects.create_user(
            phone="0703111000",
            first_name="Product",
            last_name="Owner",
            password="owner-pass-27",
            is_staff=True,
        )
        owner_group, _ = Group.objects.get_or_create(name="Product Owner")
        self.owner.groups.add(owner_group)
        self.client.force_login(self.owner)

    def free_pick_payload(self, slug, color):
        return {
            "name": f"Free pick {slug}",
            "package_type": "Free accumulator",
            "win_probability": "75",
            "closes_at": (timezone.now() + timedelta(hours=3)).isoformat(),
            "betslip_link": f"https://example.com/{slug}",
            "code": slug.upper(),
            "is_active": "true",
            "reveal_image": SimpleUploadedFile(f"{slug}.png", png_bytes(color), content_type="image/png"),
        }

    def test_owner_can_upload_one_active_free_pick_without_a_cover(self):
        with TemporaryDirectory() as public_folder, TemporaryDirectory() as private_folder, override_settings(
            MEDIA_ROOT=public_folder,
            PRIVATE_MEDIA_ROOT=private_folder,
        ):
            first = self.client.post(
                "/api/v1/owner/free-picks/",
                self.free_pick_payload("free-one", "green"),
                format="multipart",
            )
            self.assertEqual(first.status_code, 201, first.data)
            self.assertTrue(first.data["is_active"])
            self.assertTrue(first.data["reveal_image_url"].endswith(f"/api/v1/owner/free-picks/{first.data['id']}/reveal-image/"))

            second = self.client.post(
                "/api/v1/owner/free-picks/",
                self.free_pick_payload("free-two", "blue"),
                format="multipart",
            )
            self.assertEqual(second.status_code, 201, second.data)

            self.assertFalse(FreePick.objects.get(pk=first.data["id"]).is_active)
            self.assertTrue(FreePick.objects.get(pk=second.data["id"]).is_active)
            self.assertEqual(Package.objects.count(), 0)
