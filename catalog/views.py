import base64
import csv
import hashlib
import hmac
import time
from datetime import datetime, time as datetime_time, timedelta
from decimal import Decimal
from io import StringIO

from django.conf import settings
from django.db import transaction
from django.db.models import Avg, Case, Count, IntegerField, Q, Sum, When
from django.http import HttpResponse
from django.shortcuts import render
from django.utils import timezone
from django.utils.dateparse import parse_date
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from accounts.models import ActivityLog, User
from accounts.permissions import IsProductOwner
from accounts.serializers import ActivityLogSerializer, UserSerializer
from accounts.services import record_activity
from .models import Package, Payment, PaymentAlert, Prediction, Purchase, RecentWin, Testimonial
from .payment_services import (
    PaymentVerificationError,
    apply_relworx_result,
    create_payment_alert,
    reconcile_payment,
    record_payment_event,
    record_verification_mismatch,
)
from .relworx import RelworxError, request_mobile_money_payment
from .serializers import (
    PackageSerializer,
    PaymentAttemptSerializer,
    PaymentAlertSerializer,
    PaymentDetailSerializer,
    PaymentSerializer,
    PublicPackageSerializer,
    PredictionSerializer,
    PurchaseCreateSerializer,
    PurchaseSerializer,
    RecentWinSerializer,
    TestimonialSerializer,
)


class OwnerAuditMixin:
    audit_category = ActivityLog.Category.CONTENT

    def record_change(self, instance, verb):
        record_activity(
            self.request,
            category=self.audit_category,
            action=f"{instance._meta.model_name}.{verb}",
            description=f"{self.request.user.full_name} {verb} {instance}.",
            target=instance,
        )

    def perform_create(self, serializer):
        instance = serializer.save()
        self.record_change(instance, "created")

    def perform_update(self, serializer):
        instance = serializer.save()
        self.record_change(instance, "updated")

    def perform_destroy(self, instance):
        self.record_change(instance, "deleted")
        instance.delete()


class PublicPackageViewSet(viewsets.ReadOnlyModelViewSet):
    serializer_class = PublicPackageSerializer
    permission_classes = [AllowAny]
    lookup_field = "slug"

    def get_queryset(self):
        now = timezone.now()
        return Package.objects.filter(deleted_at__isnull=True, is_active=True).annotate(
            open_order=Case(
                When(closes_at__gt=now, then=0),
                default=1,
                output_field=IntegerField(),
            )
        ).order_by("open_order", "display_order", "price", "name")


class PublicPredictionViewSet(viewsets.ReadOnlyModelViewSet):
    serializer_class = PredictionSerializer
    permission_classes = [AllowAny]

    def get_queryset(self):
        return Prediction.objects.filter(is_published=True).select_related("package")


class PublicRecentWinViewSet(viewsets.ReadOnlyModelViewSet):
    serializer_class = RecentWinSerializer
    permission_classes = [AllowAny]

    def get_queryset(self):
        return RecentWin.objects.filter(is_published=True)


class PublicTestimonialViewSet(viewsets.ReadOnlyModelViewSet):
    serializer_class = TestimonialSerializer
    permission_classes = [AllowAny]

    def get_queryset(self):
        return Testimonial.objects.filter(is_published=True)


def _date_bounds(request):
    start = parse_date(request.query_params.get("from", ""))
    end = parse_date(request.query_params.get("to", ""))
    start_at = timezone.make_aware(datetime.combine(start, datetime_time.min)) if start else None
    end_at = timezone.make_aware(datetime.combine(end + timedelta(days=1), datetime_time.min)) if end else None
    return start, end, start_at, end_at


def prediction_performance_payload(queryset):
    settled = queryset.exclude(result=Prediction.Result.PENDING)
    summary = settled.aggregate(
        settled=Count("id"),
        won=Count("id", filter=Q(result=Prediction.Result.WON)),
        lost=Count("id", filter=Q(result=Prediction.Result.LOST)),
        void=Count("id", filter=Q(result=Prediction.Result.VOID)),
        average_odds=Avg("odds"),
        average_confidence=Avg("confidence"),
    )
    decisions = summary["won"] + summary["lost"]
    summary["hit_rate"] = round(summary["won"] * 100 / decisions, 2) if decisions else 0
    summary["average_odds"] = round(float(summary["average_odds"] or 0), 2)
    summary["average_confidence"] = round(float(summary["average_confidence"] or 0), 2)
    by_package = list(
        settled.values("package_id", "package__name")
        .annotate(
            settled=Count("id"),
            won=Count("id", filter=Q(result=Prediction.Result.WON)),
            lost=Count("id", filter=Q(result=Prediction.Result.LOST)),
            void=Count("id", filter=Q(result=Prediction.Result.VOID)),
            average_odds=Avg("odds"),
        )
        .order_by("package__name")
    )
    for row in by_package:
        decisions = row["won"] + row["lost"]
        row["hit_rate"] = round(row["won"] * 100 / decisions, 2) if decisions else 0
        row["average_odds"] = round(float(row["average_odds"] or 0), 2)
    return {"summary": summary, "by_package": by_package}


class PublicPerformanceView(APIView):
    permission_classes = [AllowAny]

    def get(self, request):
        queryset = Prediction.objects.filter(is_published=True)
        _, _, start_at, end_at = _date_bounds(request)
        if start_at:
            queryset = queryset.filter(kickoff_at__gte=start_at)
        if end_at:
            queryset = queryset.filter(kickoff_at__lt=end_at)
        return Response(prediction_performance_payload(queryset))


class MyPurchasesView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        purchases = request.user.purchases.select_related("package", "user").prefetch_related("payments__package", "payments__user")
        return Response(PurchaseSerializer(purchases, many=True, context={"request": request}).data)

    def post(self, request):
        serializer = PurchaseCreateSerializer(data=request.data, context={"request": request})
        serializer.is_valid(raise_exception=True)
        with transaction.atomic():
            purchase = serializer.save()
        record_activity(
            request,
            category=ActivityLog.Category.SUBSCRIPTION,
            action="purchase.created",
            description=f"{request.user.full_name} started a purchase for {purchase.package.name}.",
            target=purchase,
        )
        purchase = Purchase.objects.select_related("package", "user").prefetch_related("payments__package", "payments__user").get(pk=purchase.pk)
        response_status = status.HTTP_201_CREATED if getattr(serializer, "was_created", False) else status.HTTP_200_OK
        return Response(PurchaseSerializer(purchase, context={"request": request}).data, status=response_status)


class MyPurchaseDetailView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, pk):
        purchase = request.user.purchases.select_related("package", "user").prefetch_related("payments__package", "payments__user").filter(pk=pk).first()
        if not purchase:
            return Response({"detail": "Purchase not found."}, status=status.HTTP_404_NOT_FOUND)
        return Response(PurchaseSerializer(purchase, context={"request": request}).data)


class PaymentAttemptView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request, pk):
        serializer = PaymentAttemptSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        with transaction.atomic():
            purchase = Purchase.objects.select_for_update().select_related("package").filter(pk=pk, user=request.user).first()
            if not purchase:
                return Response({"detail": "Purchase not found."}, status=status.HTTP_404_NOT_FOUND)
            if purchase.is_paid:
                return Response({"detail": "This package has already been purchased."}, status=status.HTTP_409_CONFLICT)
            if not purchase.package.is_open:
                return Response({"detail": "This package is closed and cannot be purchased."}, status=status.HTTP_409_CONFLICT)
            if purchase.currency_snapshot.upper() != "UGX":
                return Response({"detail": "Mobile-money checkout currently supports UGX packages only."}, status=status.HTTP_409_CONFLICT)
            if not Decimal("500") <= purchase.price_snapshot <= Decimal("5000000"):
                return Response({"detail": "Relworx accepts UGX payments from 500 to 5,000,000."}, status=status.HTTP_409_CONFLICT)
            if purchase.payments.filter(status__in=Payment.ACTIVE_STATUSES).exists():
                return Response({"detail": "A payment request is already pending."}, status=status.HTTP_409_CONFLICT)
            payment = Payment.objects.create(
                purchase=purchase,
                user=request.user,
                package=purchase.package,
                amount=purchase.price_snapshot,
                currency=purchase.currency_snapshot,
                payer_msisdn=serializer.validated_data["phone"],
            )
            record_payment_event(payment, "payment.created", source="checkout", message="Payment attempt created.")
            payment.status = Payment.Status.SUBMITTED
            payment.submitted_at = timezone.now()
            payment.reconciliation_next_at = timezone.now() + timedelta(minutes=2)
            payment.save(update_fields=["status", "submitted_at", "reconciliation_next_at", "updated_at"])
            record_payment_event(payment, "payment.submitted", source="checkout", message="Payment request submitted to Relworx.")
        try:
            provider_response = request_mobile_money_payment(payment)
        except RelworxError as error:
            payment.provider_message = str(error)[:240]
            payment.failure_category = Payment.FailureCategory.SYSTEM if error.uncertain else Payment.FailureCategory.PROVIDER
            payment.failure_code = str(error.code or error.status_code or "initiation_error")[:80]
            payment.failure_reason = str(error)
            update_fields = ["provider_message", "failure_category", "failure_code", "failure_reason", "updated_at"]
            if not error.uncertain:
                payment.status = Payment.Status.FAILED
                payment.failed_at = timezone.now()
                payment.reconciliation_next_at = None
                update_fields.extend(["status", "failed_at", "reconciliation_next_at"])
            payment.save(update_fields=update_fields)
            record_payment_event(
                payment,
                "payment.initiation_uncertain" if error.uncertain else "payment.initiation_failed",
                source="checkout",
                message=str(error),
            )
            if not error.uncertain:
                create_payment_alert(
                    payment,
                    "initiation-failure",
                    "Payment initiation failed",
                    f"{payment.reference}: {error}",
                )
            record_activity(
                request,
                category=ActivityLog.Category.SUBSCRIPTION,
                action="payment.initiation_uncertain" if error.uncertain else "payment.initiation_failed",
                description=f"Relworx payment initiation was {'uncertain' if error.uncertain else 'rejected'} for {payment.reference}.",
                target=payment,
            )
            if error.uncertain:
                return Response(PaymentSerializer(payment, context={"request": request}).data, status=status.HTTP_202_ACCEPTED)
            return Response({"detail": str(error)}, status=status.HTTP_502_BAD_GATEWAY)

        payment.internal_reference = str(provider_response["internal_reference"])
        payment.status = Payment.Status.PENDING
        payment.provider_message = str(provider_response.get("message", "Payment request sent."))[:240]
        payment.save(update_fields=["internal_reference", "status", "provider_message", "updated_at"])
        record_payment_event(payment, "payment.pending", source="checkout", message=payment.provider_message, payload=provider_response)
        record_activity(
            request,
            category=ActivityLog.Category.SUBSCRIPTION,
            action="payment.initiated",
            description=f"Relworx payment {payment.reference} was initiated.",
            target=payment,
            metadata={"provider": "relworx"},
        )
        return Response(PaymentSerializer(payment, context={"request": request}).data, status=status.HTTP_202_ACCEPTED)


def verify_relworx_signature(request, payload):
    header = request.headers.get("Relworx-Signature", "")
    parts = {}
    for item in header.split(","):
        key, separator, value = item.strip().partition("=")
        if separator:
            parts[key] = value
    try:
        timestamp = int(parts.get("t", ""))
    except ValueError:
        return False
    if abs(int(time.time()) - timestamp) > settings.RELWORX_WEBHOOK_TOLERANCE_SECONDS:
        return False
    if not settings.RELWORX_WEBHOOK_SIGNING_KEY or not settings.RELWORX_WEBHOOK_URL:
        return False

    signed = f"{settings.RELWORX_WEBHOOK_URL}{timestamp}"
    for key in sorted(["status", "customer_reference", "internal_reference"]):
        signed += f"{key}{payload.get(key, '')}"
    digest = hmac.new(
        settings.RELWORX_WEBHOOK_SIGNING_KEY.encode("utf-8"),
        signed.encode("utf-8"),
        hashlib.sha256,
    ).digest()
    supplied = parts.get("v", "")
    return hmac.compare_digest(supplied, digest.hex()) or hmac.compare_digest(
        supplied, base64.b64encode(digest).decode("ascii")
    )


class PaymentRefreshView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request, pk):
        payment = Payment.objects.select_related("purchase__package", "package", "user").filter(pk=pk, user=request.user).first()
        if not payment:
            return Response({"detail": "Payment not found."}, status=status.HTTP_404_NOT_FOUND)
        if payment.status not in (Payment.Status.SUBMITTED, Payment.Status.PENDING):
            return Response(PaymentSerializer(payment, context={"request": request}).data)
        reconciled, outcome = reconcile_payment(payment.pk, source="customer_refresh")
        reconciled = Payment.objects.select_related("purchase__package", "package", "user").get(pk=reconciled.pk)
        response_status = status.HTTP_400_BAD_REQUEST if outcome == "mismatch" else status.HTTP_200_OK
        return Response(PaymentSerializer(reconciled, context={"request": request}).data, status=response_status)


class PaymentReceiptView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, pk):
        payment = Payment.objects.select_related("user", "package", "purchase").filter(pk=pk).first()
        if not payment or (payment.user_id != request.user.id and not request.user.is_product_owner):
            return Response({"detail": "Receipt not found."}, status=status.HTTP_404_NOT_FOUND)
        if payment.status != Payment.Status.PAID:
            return Response({"detail": "A receipt is available after payment confirmation."}, status=status.HTTP_409_CONFLICT)
        payment.issue_receipt()
        response = render(request, "catalog/payment_receipt.html", {"payment": payment})
        response["Content-Security-Policy"] = "default-src 'none'; style-src 'unsafe-inline'; img-src 'self' data:; base-uri 'none'; frame-ancestors 'none'"
        response["Cache-Control"] = "private, no-store"
        return response


class RelworxWebhookView(APIView):
    authentication_classes = []
    permission_classes = [AllowAny]

    def post(self, request):
        payload = request.data if isinstance(request.data, dict) else {}
        reference = str(payload.get("customer_reference", ""))
        if not verify_relworx_signature(request, payload):
            record_activity(
                request,
                category=ActivityLog.Category.SUBSCRIPTION,
                action="payment.webhook_rejected",
                description="A Relworx webhook signature was rejected.",
                metadata={"reference": reference[:24]},
            )
            return Response({"detail": "Invalid webhook signature."}, status=status.HTTP_401_UNAUTHORIZED)

        with transaction.atomic():
            payment = Payment.objects.select_for_update().select_related("purchase__package").filter(reference=reference).first()
            if not payment:
                record_activity(
                    request,
                    category=ActivityLog.Category.SUBSCRIPTION,
                    action="payment.webhook_unmatched",
                    description="A signed Relworx webhook had no matching payment.",
                    metadata={"reference": reference[:24]},
                )
                return Response({"detail": "Payment not found."}, status=status.HTTP_404_NOT_FOUND)

            try:
                before = payment.status
                apply_relworx_result(payment, payload, source="webhook")
            except PaymentVerificationError as error:
                record_verification_mismatch(payment, error, source="webhook", payload=payload)
                return self._reject_mismatch(request, payment, error.field)

            if payment.status != before:
                record_activity(
                    request,
                    category=ActivityLog.Category.SUBSCRIPTION,
                    action=f"payment.{payment.status}",
                    description=f"Relworx payment {payment.reference} changed from {before} to {payment.status}.",
                    target=payment,
                    metadata={"provider": payment.provider, "source": "webhook"},
                )
            return Response({"status": "ok"})

    @staticmethod
    def _reject_mismatch(request, payment, field):
        record_activity(
            request,
            category=ActivityLog.Category.SUBSCRIPTION,
            action="payment.webhook_mismatch",
            description=f"Relworx webhook {field} mismatch for {payment.reference}.",
            target=payment,
            metadata={"field": field},
        )
        return Response({"detail": f"Payment {field} mismatch."}, status=status.HTTP_400_BAD_REQUEST)


class OwnerDashboardView(APIView):
    permission_classes = [IsProductOwner]

    def get(self, request):
        now = timezone.now()
        return Response({
            "customers": User.objects.filter(is_staff=False).count(),
            "active_packages": Package.objects.filter(
                deleted_at__isnull=True,
                is_active=True,
                closes_at__gt=now,
            ).count(),
            "pending_payments": Payment.objects.filter(status__in=[Payment.Status.SUBMITTED, Payment.Status.PENDING]).count(),
            "payments_needing_review": Payment.objects.filter(status=Payment.Status.NEEDS_REVIEW).count(),
            "open_payment_alerts": PaymentAlert.objects.filter(resolved_at__isnull=True).count(),
            "paid_payments": Payment.objects.filter(status=Payment.Status.PAID).count(),
            "revenue": Payment.objects.filter(status=Payment.Status.PAID).aggregate(total=Sum("amount"))["total"] or 0,
            "package_demand": list(
                Package.objects.filter(deleted_at__isnull=True).annotate(
                    request_count=Count("purchases"),
                    paid_count=Count("purchases", filter=Q(purchases__paid_at__isnull=False)),
                ).values("id", "name", "request_count", "paid_count").order_by("display_order")
            ),
        })


class OwnerActivityViewSet(viewsets.ReadOnlyModelViewSet):
    serializer_class = ActivityLogSerializer
    permission_classes = [IsProductOwner]

    def get_queryset(self):
        queryset = ActivityLog.objects.select_related("actor")
        category = self.request.query_params.get("category")
        if category:
            queryset = queryset.filter(category=category)
        return queryset[:200]


class OwnerPackageViewSet(OwnerAuditMixin, viewsets.ModelViewSet):
    queryset = Package.objects.filter(deleted_at__isnull=True)
    serializer_class = PackageSerializer
    permission_classes = [IsProductOwner]

    def destroy(self, request, *args, **kwargs):
        package = self.get_object()
        package.deleted_at = timezone.now()
        package.save(update_fields=["deleted_at", "updated_at"])
        record_activity(
            request,
            category=ActivityLog.Category.CONTENT,
            action="package.deleted",
            description=f"{request.user.full_name} deleted package {package.name}.",
            metadata={"package_id": package.pk, "package_name": package.name},
        )
        return Response(status=status.HTTP_204_NO_CONTENT)


class OwnerPaymentViewSet(viewsets.ReadOnlyModelViewSet):
    queryset = Payment.objects.select_related("user", "package", "purchase").all()
    serializer_class = PaymentSerializer
    permission_classes = [IsProductOwner]

    def get_serializer_class(self):
        return PaymentDetailSerializer if self.action == "retrieve" else PaymentSerializer

    def get_queryset(self):
        queryset = super().get_queryset()
        if self.action == "retrieve":
            return queryset.prefetch_related("events", "alerts")
        status_filter = self.request.query_params.get("status", "").strip()
        provider = self.request.query_params.get("provider", "").strip()
        failure_category = self.request.query_params.get("failure_category", "").strip()
        search = self.request.query_params.get("search", "").strip()
        _, _, start_at, end_at = _date_bounds(self.request)
        if status_filter:
            queryset = queryset.filter(status=status_filter)
        if provider:
            queryset = queryset.filter(provider__iexact=provider)
        if failure_category:
            queryset = queryset.filter(failure_category=failure_category)
        if search:
            queryset = queryset.filter(
                Q(reference__icontains=search)
                | Q(internal_reference__icontains=search)
                | Q(provider_transaction_id__icontains=search)
                | Q(user__phone__icontains=search)
                | Q(user__first_name__icontains=search)
                | Q(user__last_name__icontains=search)
                | Q(package__name__icontains=search)
            )
        if start_at:
            queryset = queryset.filter(created_at__gte=start_at)
        if end_at:
            queryset = queryset.filter(created_at__lt=end_at)
        return queryset

    @action(detail=False, methods=["get"])
    def report(self, request):
        queryset = self.filter_queryset(self.get_queryset())
        attempts = queryset.count()
        paid = queryset.filter(status=Payment.Status.PAID)
        confirmed = paid.count()
        gross = paid.aggregate(value=Sum("amount"))["value"] or Decimal("0")
        fees = paid.aggregate(value=Sum("provider_charge"))["value"] or Decimal("0")
        by_status = list(queryset.values("status").annotate(count=Count("id"), amount=Sum("amount")).order_by("status"))
        by_provider = list(queryset.values("provider").annotate(count=Count("id"), amount=Sum("amount")).order_by("provider"))
        by_package = list(queryset.values("package_id", "package__name").annotate(count=Count("id"), amount=Sum("amount")).order_by("package__name"))
        failures = list(
            queryset.exclude(failure_category="")
            .values("failure_category")
            .annotate(count=Count("id"))
            .order_by("failure_category")
        )
        start, end, _, _ = _date_bounds(request)
        return Response({
            "range": {"from": start, "to": end},
            "summary": {
                "attempts": attempts,
                "confirmed": confirmed,
                "success_rate": round(confirmed * 100 / attempts, 2) if attempts else 0,
                "gross_revenue": gross,
                "provider_fees": fees,
                "net_revenue": gross - fees,
                "pending": queryset.filter(status__in=[Payment.Status.SUBMITTED, Payment.Status.PENDING]).count(),
                "needs_review": queryset.filter(status=Payment.Status.NEEDS_REVIEW).count(),
            },
            "by_status": by_status,
            "by_provider": by_provider,
            "by_package": by_package,
            "failure_categories": failures,
        })

    @action(detail=False, methods=["get"], url_path="export.csv")
    def export_csv(self, request):
        output = StringIO()
        writer = csv.writer(output)
        writer.writerow([
            "BK reference", "Relworx reference", "Provider transaction", "Customer", "Phone",
            "Package", "Amount", "Currency", "Status", "Provider", "Provider fee",
            "Failure category", "Failure reason", "Created", "Confirmed",
        ])
        for payment in self.filter_queryset(self.get_queryset()).iterator():
            writer.writerow([
                payment.reference, payment.internal_reference, payment.provider_transaction_id,
                payment.user.full_name, payment.user.phone, payment.package.name, payment.amount,
                payment.currency, payment.status, payment.provider, payment.provider_charge or "",
                payment.failure_category, payment.failure_reason, payment.created_at.isoformat(),
                payment.confirmed_at.isoformat() if payment.confirmed_at else "",
            ])
        response = HttpResponse(output.getvalue(), content_type="text/csv; charset=utf-8")
        response["Content-Disposition"] = 'attachment; filename="bk-payments.csv"'
        return response


class OwnerPaymentAlertViewSet(viewsets.ReadOnlyModelViewSet):
    queryset = PaymentAlert.objects.select_related("payment").all()
    serializer_class = PaymentAlertSerializer
    permission_classes = [IsProductOwner]

    def get_queryset(self):
        queryset = super().get_queryset()
        if self.request.query_params.get("open") == "true":
            queryset = queryset.filter(resolved_at__isnull=True)
        return queryset

    @action(detail=True, methods=["post"])
    def resolve(self, request, pk=None):
        alert = self.get_object()
        if not alert.resolved_at:
            alert.resolved_at = timezone.now()
            alert.save(update_fields=["resolved_at"])
        return Response(self.get_serializer(alert).data)


class OwnerPerformanceView(APIView):
    permission_classes = [IsProductOwner]

    def get(self, request):
        queryset = Prediction.objects.filter(is_published=True).select_related("package")
        _, _, start_at, end_at = _date_bounds(request)
        if start_at:
            queryset = queryset.filter(kickoff_at__gte=start_at)
        if end_at:
            queryset = queryset.filter(kickoff_at__lt=end_at)
        payload = prediction_performance_payload(queryset)
        payload["unsettled"] = queryset.filter(result=Prediction.Result.PENDING).count()
        return Response(payload)


class OwnerPredictionViewSet(OwnerAuditMixin, viewsets.ModelViewSet):
    queryset = Prediction.objects.select_related("package").all()
    serializer_class = PredictionSerializer
    permission_classes = [IsProductOwner]

    def perform_create(self, serializer):
        instance = serializer.save(created_by=self.request.user)
        self.record_change(instance, "created")


class OwnerRecentWinViewSet(OwnerAuditMixin, viewsets.ModelViewSet):
    queryset = RecentWin.objects.all()
    serializer_class = RecentWinSerializer
    permission_classes = [IsProductOwner]


class OwnerTestimonialViewSet(OwnerAuditMixin, viewsets.ModelViewSet):
    queryset = Testimonial.objects.all()
    serializer_class = TestimonialSerializer
    permission_classes = [IsProductOwner]


class OwnerCustomerViewSet(viewsets.ReadOnlyModelViewSet):
    queryset = User.objects.filter(is_staff=False).order_by("-created_at")
    serializer_class = UserSerializer
    permission_classes = [IsProductOwner]

    @action(detail=True, methods=["post"])
    def block(self, request, pk=None):
        customer = self.get_object()
        customer.is_blocked = bool(request.data.get("blocked", True))
        customer.is_active = not customer.is_blocked
        customer.save(update_fields=["is_blocked", "is_active", "updated_at"])
        action = "blocked" if customer.is_blocked else "restored"
        record_activity(
            request,
            category=ActivityLog.Category.ADMINISTRATION,
            action=f"account.{action}",
            description=f"{request.user.full_name} {action} {customer.full_name}'s account.",
            target=customer,
        )
        return Response(self.get_serializer(customer).data)
