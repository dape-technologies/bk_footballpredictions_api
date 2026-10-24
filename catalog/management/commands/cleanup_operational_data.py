from datetime import timedelta
from django.core.management.base import BaseCommand
from django.utils import timezone
from accounts.models import SecurityChallenge, PrivilegedSession
from catalog.models import WebVitalSample


class Command(BaseCommand):
    help = "Expire old OTP challenges and performance samples; financial and access histories are retained."

    def handle(self, *args, **options):
        SecurityChallenge.objects.filter(expires_at__lt=timezone.now() - timedelta(days=7)).delete()
        WebVitalSample.objects.filter(created_at__lt=timezone.now() - timedelta(days=90)).delete()
        self.stdout.write("Expired operational samples cleaned up.")
