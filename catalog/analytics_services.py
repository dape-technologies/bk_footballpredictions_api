import re
import secrets
from collections import defaultdict
from datetime import timedelta

from django.db.models import Count
from django.utils import timezone

from .models import JourneyEvent, Payment, WebVitalSample


def context(request):
    agent = request.META.get("HTTP_USER_AGENT", "").lower()
    device = "mobile" if re.search("mobile|android|iphone|ipad", agent) else "desktop"
    browser = next((name for token, name in [("edg", "Edge"), ("firefox", "Firefox"), ("chrome", "Chrome"), ("safari", "Safari")] if token in agent), "Other")
    return device, browser


def record_journey(request, kind, package=None, purchase=None, event_id=None):
    visitor = request.session.get("journey_visitor")
    if not visitor:
        visitor = secrets.token_hex(16)
        request.session["journey_visitor"] = visitor
    user = request.user if request.user.is_authenticated else None
    device, browser = context(request)
    customer_type = "returning" if user and user.purchases.filter(paid_at__isnull=False).exists() else "new"
    return JourneyEvent.objects.get_or_create(event_id=event_id or secrets.token_hex(16), defaults={
        "visitor": visitor, "kind": kind, "package": package, "purchase": purchase, "user": user,
        "device": device, "browser": browser, "customer_type": customer_type,
    })[0]


def funnel_report():
    since = timezone.now() - timedelta(days=30)
    events = JourneyEvent.objects.filter(created_at__gte=since).select_related("package")
    grouped = {key: defaultdict(lambda: defaultdict(set)) for key in ["package", "device", "browser", "hour", "customer_type"]}
    totals = defaultdict(set)
    for event in events.iterator():
        journey = f"{event.visitor}:{event.package_id}"
        totals[event.kind].add(journey)
        dimensions = {"package": event.package.name if event.package else "Unknown", "device": event.device,
            "browser": event.browser, "hour": timezone.localtime(event.created_at).strftime("%H:00"), "customer_type": event.customer_type}
        for dimension, label in dimensions.items():
            grouped[dimension][label][event.kind].add(journey)
    stages = ["package_view", "checkout_open", "prompt_sent", "payment_confirmed", "betslip_access"]
    def serialize(values):
        counts = {stage: len(values[stage]) for stage in stages}
        counts["view_to_paid_percent"] = round(counts["payment_confirmed"] * 100 / counts["package_view"], 1) if counts["package_view"] else None
        counts["checkout_without_confirmation"] = len(values["checkout_open"] - values["payment_confirmed"])
        return counts
    payments = Payment.objects.filter(created_at__gte=since)
    provider_rows = defaultdict(lambda: {"attempts": 0, "confirmed": 0, "failed": 0})
    for payment in payments.iterator():
        network = str(payment.provider_payload.get("provider", "") if isinstance(payment.provider_payload, dict) else "").upper()
        network = "MTN" if "MTN" in network else "Airtel" if "AIRTEL" in network else "Unknown"
        provider_rows[network]["attempts"] += 1
        provider_rows[network]["confirmed"] += payment.status == Payment.Status.PAID
        provider_rows[network]["failed"] += payment.status in ["failed", "expired", "cancelled"]
    return {"from": since, "stages": stages, "totals": serialize(totals),
        "segments": {dimension: [{"label": label, **serialize(values)} for label, values in rows.items()] for dimension, rows in grouped.items()},
        "networks": dict(provider_rows),
        "failure_categories": list(payments.exclude(failure_category="").values("failure_category").annotate(count=Count("id"))),
        "approval_note": "Relworx does not report a separate PIN approval event. Confirmed payments prove approval; approval is not separately measured.",
        "counting_note": "Unique visitor/package journeys per stage, last 30 days. Counts are observational; cross-device journeys and events before tracking began are not reconstructed."}


def record_confirmation(payment):
    original = JourneyEvent.objects.filter(purchase=payment.purchase, kind="checkout_open").order_by("created_at").first()
    JourneyEvent.objects.get_or_create(event_id=f"paid-{payment.pk}", defaults={
        "visitor": original.visitor if original else f"user-{payment.user_id}",
        "kind": "payment_confirmed", "package": payment.package, "purchase": payment.purchase,
        "user": payment.user, "device": original.device if original else "unknown",
        "browser": original.browser if original else "unknown", "customer_type": original.customer_type if original else "unknown",
    })


def vital_report():
    groups = defaultdict(list)
    for row in WebVitalSample.objects.filter(created_at__gte=timezone.now() - timedelta(days=30)).values("metric", "device", "value"):
        groups[(row["metric"], row["device"])].append(row["value"])
    thresholds = {"LCP": 2500, "INP": 200, "CLS": .1}
    import math
    return [{"metric": metric, "device": device, "samples": len(values),
             "p75": sorted(values)[max(0, math.ceil(.75 * len(values)) - 1)], "target": thresholds[metric]}
            for (metric, device), values in groups.items()]
