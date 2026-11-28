from django.conf import settings
from django.db.models import Q
from django.utils import timezone

from .models import Payment, PayoutRequest, ReconciliationRun


def reconciliation_health_payload(*, include_history=False):
    now = timezone.now()
    stale_after = max(1, settings.RECONCILIATION_STALE_AFTER_MINUTES)
    jobs = {}
    for job in ReconciliationRun.Job.values:
        runs = ReconciliationRun.objects.filter(job=job)
        latest = runs.first()
        last_success = runs.filter(status=ReconciliationRun.Status.SUCCESS).first()
        age_seconds = int((now - last_success.finished_at).total_seconds()) if last_success and last_success.finished_at else None
        if not latest:
            health = "never_run"
        elif not last_success or age_seconds > stale_after * 60:
            health = "stale"
        elif latest.status in (ReconciliationRun.Status.DEGRADED, ReconciliationRun.Status.FAILED):
            health = "degraded"
        else:
            health = "healthy"
        item = {
            "health": health,
            "expected_interval_minutes": settings.RECONCILIATION_EXPECTED_INTERVAL_MINUTES,
            "stale_after_minutes": stale_after,
            "last_run_at": latest.finished_at if latest else None,
            "last_run_status": latest.status if latest else "never_run",
            "last_success_at": last_success.finished_at if last_success else None,
            "seconds_since_success": age_seconds,
        }
        if include_history:
            item["recent_runs"] = [
                {
                    "id": run.pk,
                    "status": run.status,
                    "started_at": run.started_at,
                    "finished_at": run.finished_at,
                    "scanned_count": run.scanned_count,
                    "outcomes": run.outcomes,
                    "error_summary": run.error_summary,
                }
                for run in runs[:10]
            ]
        jobs[job] = item

    jobs[ReconciliationRun.Job.PAYMENTS].update({
        "due_count": Payment.objects.filter(status__in=Payment.ACTIVE_STATUSES).filter(
            Q(reconciliation_next_at__isnull=True) | Q(reconciliation_next_at__lte=now)
        ).count(),
        "needs_review_count": Payment.objects.filter(status=Payment.Status.NEEDS_REVIEW).count(),
    })
    jobs[ReconciliationRun.Job.PAYOUTS].update({
        "due_count": PayoutRequest.objects.filter(status=PayoutRequest.Status.PROCESSING).filter(
            Q(reconciliation_next_at__isnull=True) | Q(reconciliation_next_at__lte=now)
        ).count(),
    })
    health_values = {item["health"] for item in jobs.values()}
    overall = "healthy"
    if "never_run" in health_values:
        overall = "never_run"
    if "stale" in health_values:
        overall = "stale"
    elif "degraded" in health_values:
        overall = "degraded"
    return {"generated_at": now, "overall": overall, "jobs": jobs}
