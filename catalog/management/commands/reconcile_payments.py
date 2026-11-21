from django.core.management.base import BaseCommand
from django.db.models import Q
from django.utils import timezone

from catalog.models import Payment, ReconciliationRun
from catalog.payment_services import reconcile_payment


class Command(BaseCommand):
    help = "Reconcile submitted and pending Relworx payments without relying on an open browser."

    def add_arguments(self, parser):
        parser.add_argument("--batch-size", type=int, default=100)
        parser.add_argument("--max-attempts", type=int, default=12)
        parser.add_argument("--max-age-hours", type=int, default=24)

    def handle(self, *args, **options):
        run = ReconciliationRun.objects.create(job=ReconciliationRun.Job.PAYMENTS)
        now = timezone.now()
        try:
            payment_ids = list(
                Payment.objects.filter(status__in=[Payment.Status.SUBMITTED, Payment.Status.PENDING])
                .filter(Q(reconciliation_next_at__isnull=True) | Q(reconciliation_next_at__lte=now))
                .order_by("created_at")
                .values_list("id", flat=True)[: max(1, options["batch_size"])]
            )
            outcomes = {"updated": 0, "error": 0, "mismatch": 0, "skipped": 0}
            for payment_id in payment_ids:
                _, outcome = reconcile_payment(
                    payment_id,
                    max_attempts=max(1, options["max_attempts"]),
                    max_age_hours=max(1, options["max_age_hours"]),
                )
                outcomes[outcome] = outcomes.get(outcome, 0) + 1

            run.status = ReconciliationRun.Status.DEGRADED if outcomes["error"] or outcomes["mismatch"] else ReconciliationRun.Status.SUCCESS
            run.scanned_count = len(payment_ids)
            run.outcomes = outcomes
            run.finished_at = timezone.now()
            run.save(update_fields=["status", "scanned_count", "outcomes", "finished_at"])
        except Exception as error:
            run.status = ReconciliationRun.Status.FAILED
            run.error_summary = str(error)[:500]
            run.finished_at = timezone.now()
            run.save(update_fields=["status", "error_summary", "finished_at"])
            raise

        self.stdout.write(
            self.style.SUCCESS(
                f"Reconciled {len(payment_ids)} payment(s): "
                + ", ".join(f"{key}={value}" for key, value in outcomes.items())
            )
        )
