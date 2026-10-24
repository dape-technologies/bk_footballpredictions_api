from django.core.management.base import BaseCommand
from accounts.models import EmailNotice
from accounts.security_services import deliver_notice


class Command(BaseCommand):
    help = "Deliver queued security notification emails."

    def handle(self, *args, **options):
        ids = list(EmailNotice.objects.filter(sent_at=None, attempts__lt=10).order_by("created_at").values_list("pk", flat=True)[:50])
        for pk in ids:
            deliver_notice(pk)
        self.stdout.write(f"Processed {len(ids)} queued notification(s).")
