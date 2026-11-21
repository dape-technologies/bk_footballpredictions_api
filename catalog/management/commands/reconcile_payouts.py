from django.core.management.base import BaseCommand
from django.db.models import Q
from django.utils import timezone

from catalog.models import PayoutRequest, ReconciliationRun
from catalog.payout_services import reconcile_payout


class Command(BaseCommand):
    help = "Reconcile processing Relworx payouts without relying on an open browser."

    def add_arguments(self, parser):
        parser.add_argument("--batch-size", type=int, default=100)

    def handle(self, *args, **options):
        run = ReconciliationRun.objects.create(job=ReconciliationRun.Job.PAYOUTS)
        try:
            ids = list(
                PayoutRequest.objects.filter(status=PayoutRequest.Status.PROCESSING)
                .filter(Q(reconciliation_next_at__isnull=True) | Q(reconciliation_next_at__lte=timezone.now()))
                .order_by("created_at").values_list("id", flat=True)[:max(1, options["batch_size"])]
            )
            outcomes = {}
            for payout_id in ids:
                _, outcome = reconcile_payout(payout_id)
                outcomes[outcome] = outcomes.get(outcome, 0) + 1
            run.status = ReconciliationRun.Status.DEGRADED if outcomes.get("error") or outcomes.get("mismatch") else ReconciliationRun.Status.SUCCESS
            run.scanned_count = len(ids)
            run.outcomes = outcomes
            run.finished_at = timezone.now()
            run.save(update_fields=["status", "scanned_count", "outcomes", "finished_at"])
            self.stdout.write(self.style.SUCCESS(f"Reconciled {len(ids)} payout(s): {outcomes}"))
        except Exception as error:
            run.status = ReconciliationRun.Status.FAILED
            run.error_summary = str(error)[:500]
            run.finished_at = timezone.now()
            run.save(update_fields=["status", "error_summary", "finished_at"])
            raise
