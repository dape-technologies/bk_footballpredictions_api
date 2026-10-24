import math
import re
from datetime import timedelta
from uuid import uuid4

from django.shortcuts import get_object_or_404
from django.utils import timezone
from django.utils.dateparse import parse_date
from rest_framework.views import APIView
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.throttling import AnonRateThrottle, UserRateThrottle
from rest_framework.exceptions import ValidationError

from accounts.security_views import IsPrivileged
from .models import Package, JourneyEvent, WebVitalSample, Purchase
from .analytics_services import record_journey, funnel_report, vital_report, context


class EventRateThrottle(AnonRateThrottle):
    rate = "120/minute"


class MetricsRateThrottle(UserRateThrottle):
    rate = "120/minute"


class TelemetryView(APIView):
    permission_classes = [AllowAny]
    throttle_classes = [EventRateThrottle, MetricsRateThrottle]

    def post(self, request):
        kind = request.data.get("kind")
        if kind == "package_view":
            package = get_object_or_404(Package, pk=request.data.get("package_id"), deleted_at=None, is_active=True)
            event_id = str(request.data.get("event_id", ""))
            if not re.fullmatch(r"[a-zA-Z0-9-]{8,64}", event_id):
                raise ValidationError({"event_id": "Invalid event identifier."})
            record_journey(request, kind, package, event_id=event_id)
        elif kind == "web_vital":
            if request.user.is_authenticated and request.user.notification_preferences.get("performance_measurement") is False:
                return Response(status=204)
            metric = request.data.get("metric")
            try:
                value = float(request.data.get("value"))
                if metric not in ["LCP", "INP", "CLS"] or not math.isfinite(value) or value < 0 or value > (100 if metric == "CLS" else 600000):
                    raise ValueError()
            except (TypeError, ValueError):
                raise ValidationError({"value": "Invalid metric sample."})
            sample_id = str(request.data.get("sample_id", ""))
            if not re.fullmatch(r"[a-zA-Z0-9_-]{8,100}", sample_id):
                raise ValidationError({"sample_id": "Invalid metric identifier."})
            device, _ = context(request)
            page = str(request.data.get("page", "/"))
            page = page if page in ["/", "/owner/dashboard", "/developer/dashboard", "/account"] else "/other"
            WebVitalSample.objects.update_or_create(sample_id=sample_id, defaults={"metric": metric, "value": value, "device": device, "page": page})
        else:
            raise ValidationError({"kind": "Unknown telemetry event."})
        return Response(status=204)


class OperationsReportView(APIView):
    permission_classes = [IsPrivileged]

    def get(self, request):
        return Response({"funnel": funnel_report(), "web_vitals": vital_report()})


class BetslipAccessView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request, pk):
        purchase = get_object_or_404(Purchase.objects.select_related("package"), pk=pk, user=request.user, paid_at__isnull=False)
        record_journey(request, "betslip_access", purchase.package, purchase)
        return Response({"url": purchase.link_snapshot})


class CustomerPreferencesView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        preferences = request.user.notification_preferences
        notices = []
        if preferences.get("closing_reminders"):
            for purchase in request.user.purchases.select_related("package").filter(paid_at__isnull=False,
                    package__closes_at__gt=timezone.now(), package__closes_at__lte=timezone.now() + timedelta(days=1))[:10]:
                notices.append({"kind": "closing", "message": f"{purchase.package.name} closes for new purchases at {timezone.localtime(purchase.package.closes_at).strftime('%d %b %H:%M')}. Your paid access remains in purchase history."})
        if preferences.get("product_updates"):
            for package in Package.objects.filter(deleted_at=None, is_active=True, closes_at__gt=timezone.now(),
                    created_at__gte=timezone.now() - timedelta(days=7)).order_by("-created_at")[:10]:
                notices.append({"kind": "package", "message": f"Available package: {package.name}."})
        return Response({"preferences": preferences, "notices": notices})

    def patch(self, request):
        allowed = {"product_updates", "closing_reminders", "performance_measurement"}
        if set(request.data) - allowed or any(not isinstance(value, bool) for value in request.data.values()):
            raise ValidationError({"preferences": "Choose supported boolean preferences."})
        request.user.notification_preferences = {**request.user.notification_preferences, **request.data}
        request.user.save(update_fields=["notification_preferences"])
        return self.get(request)
