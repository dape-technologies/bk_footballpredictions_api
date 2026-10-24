from django.conf import settings
from django.contrib.auth.models import Group
from django.core.management.base import BaseCommand

from accounts.models import User


class Command(BaseCommand):
    help = "Create or update the environment-configured developer account without resetting an existing password."

    def handle(self, *args, **options):
        phone = User.normalize_phone(settings.DEVELOPER_SEED_PHONE)
        password = settings.DEVELOPER_SEED_PASSWORD
        if not phone or not password:
            self.stdout.write("Developer seed credentials are not configured; skipping.")
            return
        user, created = User.objects.get_or_create(
            phone=phone,
            defaults={"first_name": "Platform", "last_name": "Developer", "is_staff": False},
        )
        user.is_staff = False
        user.is_active = True
        if created:
            user.set_password(password)
        user.save()
        group, _ = Group.objects.get_or_create(name="Developer")
        user.groups.add(group)
        self.stdout.write(self.style.SUCCESS(f"{'Created' if created else 'Updated'} developer account {phone}."))
