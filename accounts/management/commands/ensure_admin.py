from datetime import date

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from accounts.models import User


class Command(BaseCommand):
    help = "Create or refresh the configured seeded administrator account."

    def handle(self, *args, **options):
        alias = settings.ADMIN_LOGIN_USERNAME
        phone = User.normalize_phone(settings.ADMIN_SEED_PHONE)
        password = settings.ADMIN_SEED_PASSWORD

        if not alias or not phone or not password:
            raise CommandError(
                "ADMIN_LOGIN_USERNAME, ADMIN_SEED_PHONE, and ADMIN_SEED_PASSWORD must all be configured."
            )
        if len(phone) < 10 or len(phone) > 15:
            raise CommandError("ADMIN_SEED_PHONE must normalize to 10-15 digits.")

        user = User.objects.filter(phone=phone).first()
        if user and not user.is_superuser:
            raise CommandError(
                "ADMIN_SEED_PHONE already belongs to a non-superuser; choose another seed phone."
            )

        created = user is None
        if created:
            user = User(
                phone=phone,
                first_name="System",
                last_name="Administrator",
                date_of_birth=date(1990, 1, 1),
            )

        user.is_active = True
        user.is_staff = True
        user.is_superuser = True
        user.is_blocked = False
        user.set_password(password)
        user.save()

        action = "Created" if created else "Updated"
        self.stdout.write(self.style.SUCCESS(f"{action} seeded administrator '{alias}'."))
