import base64
import csv
import hashlib
import hmac
import mimetypes
import time
from datetime import datetime, time as datetime_time, timedelta
from decimal import Decimal
from io import StringIO
from urllib.parse import urlsplit

from django.conf import settings
from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import transaction
from django.db.models import Avg, Case, Count, IntegerField, Q, Sum, When
from django.db.models.functions import TruncDate
from django.http import FileResponse, Http404, HttpResponse
from django.shortcuts import render
from django.utils import timezone
from django.utils.dateparse import parse_date
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from accounts.models import ActivityLog, User
from accounts.permissions import IsDeveloper, IsProductOwner
from accounts.serializers import ActivityLogSerializer, UserSerializer
from accounts.services import record_activity
from accounts.throttles import PayoutRateThrottle
from .models import (
    FreePick, Package, Payment, PaymentAlert, PayoutRequest, Prediction, Purchase,
    RecentWin, SubscriptionContentGrant, SubscriptionOrder, SubscriptionPlan, Testimonial,
)
from .payment_pricing import payment_quote
from .payment_services import (
    PaymentVerificationError,
    apply_relworx_result,
    create_payment_alert,
    reconcile_payment,
    record_initiation_result,
    record_payment_event,
    record_verification_mismatch,
)
from .payout_services import execute_payout, settle_payout
from .relworx import RelworxError, check_wallet_balance, request_mobile_money_payment
from .reconciliation_monitoring import reconciliation_health_payload
from .wallet_services import cancel_payout, create_payout, serialize_payout, wallet_payload, wallet_summary
from .serializers import (
    PackageSerializer,
    PaymentAttemptSerializer,
    PaymentAlertSerializer,
    PaymentDetailSerializer,
    PaymentSerializer,
    OwnerFreePickSerializer,
    PublicFreePickSerializer,
    PublicPackageSerializer,
    PredictionSerializer,
    PurchaseCreateSerializer,
    PurchaseSerializer,
    RecentWinSerializer,
    SubscriptionOrderSerializer,
    OwnerWeeklySubscriberSerializer,
    SubscriptionPlanSerializer,
    TestimonialSerializer,
    WeeklyContentSerializer,
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
        return Package.objects.filter(
            deleted_at__isnull=True, is_active=True, offer_type=Package.OfferType.ONE_OFF,
        ).annotate(
            open_order=Case(
                When(closes_at__gt=now, then=0),
                default=1,
                output_field=IntegerField(),
            )
        ).order_by("open_order", "display_order", "price", "name")


def active_free_pick():
    return (
        FreePick.objects.filter(
            is_active=True,
            closes_at__gt=timezone.now(),
        )
        .order_by("-updated_at", "-id")
        .first()
    )


class PublicFreePickView(APIView):
    permission_classes = [AllowAny]

    def get(self, request):
        package = active_free_pick()
        if not package:
            return Response(status=status.HTTP_204_NO_CONTENT)
        return Response(PublicFreePickSerializer(package, context={"request": request}).data)


class PublicFreePickImageView(APIView):
    permission_classes = [AllowAny]

    def get(self, request):
        package = active_free_pick()
        if not package:
            raise Http404("No free pick is live right now.")
        response = paid_image_response(package.reveal_image.name, package.reveal_image.storage)
        response["Cache-Control"] = "no-store, private"
        response["Pragma"] = "no-cache"
        return response


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
        from .analytics_services import record_journey
        if not purchase.is_paid:
            record_journey(request, "checkout_open", purchase.package, purchase)
        response_status = status.HTTP_201_CREATED if getattr(serializer, "was_created", False) else status.HTTP_200_OK
        return Response(PurchaseSerializer(purchase, context={"request": request}).data, status=response_status)


class MyPurchaseDetailView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, pk):
        purchase = request.user.purchases.select_related("package", "user").prefetch_related("payments__package", "payments__user").filter(pk=pk).first()
        if not purchase:
            return Response({"detail": "Purchase not found."}, status=status.HTTP_404_NOT_FOUND)
        return Response(PurchaseSerializer(purchase, context={"request": request}).data)


def paid_image_response(storage_name, storage):
    if not storage_name or not storage.exists(storage_name):
        raise Http404("Paid package image not found.")
    content_type = mimetypes.guess_type(storage_name)[0] or "application/octet-stream"
    response = FileResponse(storage.open(storage_name, "rb"), content_type=content_type)
    response["Cache-Control"] = "private, max-age=300"
    response["X-Content-Type-Options"] = "nosniff"
    response["Content-Disposition"] = 'inline; filename="paid-package-image"'
    return response


class MyPurchasePaidImageView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, pk):
        purchase = request.user.purchases.select_related("package").filter(pk=pk, paid_at__isnull=False).first()
        if not purchase:
            raise Http404("Purchase not found.")
        return paid_image_response(purchase.paid_image_snapshot, purchase.package.paid_image.storage)


def active_subscription_order(user, *, lock=False):
    queryset = SubscriptionOrder.objects.select_related("plan").filter(
        user=user,
        paid_at__isnull=False,
        access_starts_at__lte=timezone.now(),
        access_ends_at__gt=timezone.now(),
    ).order_by("-access_ends_at", "-id")
    return queryset.select_for_update().first() if lock else queryset.first()


def payment_conflict(code, detail, payment=None):
    payload = {"detail": detail, "code": code}
    if payment:
        payload["payment"] = {
            "id": payment.pk,
            "reference": payment.reference,
            "status": payment.status,
            "customer_message": PaymentSerializer(payment).get_customer_message(payment),
        }
    return Response(payload, status=status.HTTP_409_CONFLICT)


class WeeklyPlanView(APIView):
    permission_classes = [AllowAny]

    def get(self, request):
        plan = SubscriptionPlan.objects.filter(key="weekly", is_active=True).first()
        if not plan:
            return Response({"detail": "The Weekly Plan is not available right now."}, status=status.HTTP_404_NOT_FOUND)
        return Response(SubscriptionPlanSerializer(plan, context={"request": request}).data)


class MySubscriptionOrdersView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        orders = request.user.subscription_orders.select_related("plan").prefetch_related(
            "payments__package", "payments__subscription_order__plan", "payments__user",
        )
        return Response(SubscriptionOrderSerializer(orders, many=True, context={"request": request}).data)

    def post(self, request):
        with transaction.atomic():
            User.objects.select_for_update().get(pk=request.user.pk)
            if active_subscription_order(request.user, lock=True):
                return Response(
                    {"detail": "Your Weekly Plan is still active. You can renew after it expires."},
                    status=status.HTTP_409_CONFLICT,
                )
            plan = SubscriptionPlan.objects.select_for_update().filter(key="weekly", is_active=True).first()
            if not plan:
                return Response({"detail": "The Weekly Plan is not available right now."}, status=status.HTTP_409_CONFLICT)
            order = request.user.subscription_orders.filter(
                plan=plan,
                paid_at__isnull=True,
                price_snapshot=plan.price,
                currency_snapshot=plan.currency,
                duration_days_snapshot=plan.duration_days,
                initiated_at__gte=timezone.now() - timedelta(hours=24),
            ).order_by("-initiated_at").first()
            created = order is None
            if created:
                order = SubscriptionOrder.objects.create(
                    user=request.user,
                    plan=plan,
                    price_snapshot=plan.price,
                    currency_snapshot=plan.currency,
                    duration_days_snapshot=plan.duration_days,
                )
        record_activity(
            request,
            category=ActivityLog.Category.SUBSCRIPTION,
            action="subscription.checkout_opened",
            description=f"{request.user.full_name} opened checkout for {plan.name}.",
            target=order,
        )
        order = SubscriptionOrder.objects.select_related("plan").prefetch_related(
            "payments__package", "payments__subscription_order__plan", "payments__user",
        ).get(pk=order.pk)
        return Response(
            SubscriptionOrderSerializer(order, context={"request": request}).data,
            status=status.HTTP_201_CREATED if created else status.HTTP_200_OK,
        )


class MySubscriptionOrderDetailView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, pk):
        order = request.user.subscription_orders.select_related("plan").prefetch_related(
            "payments__package", "payments__subscription_order__plan", "payments__user",
        ).filter(pk=pk).first()
        if not order:
            return Response({"detail": "Subscription order not found."}, status=status.HTTP_404_NOT_FOUND)
        return Response(SubscriptionOrderSerializer(order, context={"request": request}).data)


class MyWeeklyContentView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        order = active_subscription_order(request.user)
        if not order:
            return Response({"detail": "An active Weekly Plan is required."}, status=status.HTTP_403_FORBIDDEN)
        packages = Package.objects.filter(
            subscription_grants__order=order,
            offer_type=Package.OfferType.WEEKLY,
            deleted_at__isnull=True,
            is_active=True,
        ).distinct().order_by("-created_at", "-id")
        return Response(WeeklyContentSerializer(packages, many=True, context={"request": request}).data)


class MyWeeklyContentPaidImageView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, pk):
        order = active_subscription_order(request.user)
        package = Package.objects.filter(
            pk=pk, offer_type=Package.OfferType.WEEKLY, deleted_at__isnull=True, is_active=True,
            subscription_grants__order=order,
        ).first() if order else None
        if not package:
            raise Http404("Weekly content not found.")
        image = package.paid_image_optimized or package.paid_image
        return paid_image_response(image.name if image else "", package.paid_image.storage)


class WeeklyContentAccessView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request, pk):
        order = active_subscription_order(request.user)
        package = Package.objects.filter(
            pk=pk, offer_type=Package.OfferType.WEEKLY, deleted_at__isnull=True, is_active=True,
            subscription_grants__order=order,
        ).first() if order else None
        if not package:
            return Response({"detail": "Active Weekly Plan access is required."}, status=status.HTTP_403_FORBIDDEN)
        return Response({"url": package.betslip_link})


class SubscriptionPaymentAttemptView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request, pk):
        serializer = PaymentAttemptSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        with transaction.atomic():
            User.objects.select_for_update().get(pk=request.user.pk)
            order = SubscriptionOrder.objects.select_for_update().select_related("plan").filter(pk=pk, user=request.user).first()
            if not order:
                return Response({"detail": "Subscription order not found."}, status=status.HTTP_404_NOT_FOUND)
            if order.is_paid:
                return payment_conflict("already_paid", "This subscription order has already been paid.")
            if active_subscription_order(request.user, lock=True):
                return payment_conflict("subscription_active", "Your Weekly Plan is still active.")
            if not order.plan.is_active:
                return Response({"detail": "The Weekly Plan is not available right now."}, status=status.HTTP_409_CONFLICT)
            blocking = Payment.objects.filter(
                user=request.user,
                subscription_order__isnull=False,
                status__in=Payment.ACTIVE_STATUSES,
            ).order_by("-created_at", "-id").first()
            if blocking:
                code = "payment_needs_review" if blocking.status == Payment.Status.NEEDS_REVIEW else "payment_in_progress"
                detail = "A previous Weekly Plan payment needs support review." if code == "payment_needs_review" else "A Weekly Plan payment is already in progress. Check its status before trying again."
                return payment_conflict(code, detail, blocking)
            quote = payment_quote(order.price_snapshot)
            if quote["total_charge"] > Decimal("5000000"):
                return Response({"detail": "The plan price plus provider fee exceeds the Relworx limit."}, status=status.HTTP_409_CONFLICT)
            payment = Payment.objects.create(
                subscription_order=order,
                user=request.user,
                amount=quote["total_charge"],
                base_amount=quote["base_amount"],
                provider_fee_estimate=quote["provider_fee_estimate"],
                provider_net_amount=quote["base_amount"],
                currency=order.currency_snapshot,
                payer_msisdn=serializer.validated_data["phone"],
                status=Payment.Status.SUBMITTED,
                submitted_at=timezone.now(),
                reconciliation_next_at=timezone.now() + timedelta(minutes=2),
            )
            record_payment_event(payment, "payment.created", source="subscription_checkout", message="Subscription payment attempt created.")
            record_payment_event(payment, "payment.submitted", source="subscription_checkout", message="Payment request submitted to Relworx.")
        try:
            provider_response = request_mobile_money_payment(payment)
        except RelworxError as error:
            payment = record_initiation_result(payment.pk, source="subscription_checkout", error=error)
            if payment.can_retry:
                data = PaymentSerializer(payment, context={"request": request}).data
                return Response({"detail": str(error), "payment": data}, status=status.HTTP_502_BAD_GATEWAY)
            return Response(PaymentSerializer(payment, context={"request": request}).data, status=status.HTTP_202_ACCEPTED)
        payment = record_initiation_result(payment.pk, source="subscription_checkout", response=provider_response)
        return Response(PaymentSerializer(payment, context={"request": request}).data, status=status.HTTP_202_ACCEPTED)


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
                return payment_conflict("already_paid", "This package has already been purchased.")
            if not purchase.package.is_open:
                return Response({"detail": "This package is closed and cannot be purchased."}, status=status.HTTP_409_CONFLICT)
            if purchase.currency_snapshot.upper() != "UGX":
                return Response({"detail": "Mobile-money checkout currently supports UGX packages only."}, status=status.HTTP_409_CONFLICT)
            if not Decimal("500") <= purchase.price_snapshot <= Decimal("5000000"):
                return Response({"detail": "Relworx accepts UGX payments from 500 to 5,000,000."}, status=status.HTTP_409_CONFLICT)
            blocking = purchase.payments.filter(status__in=Payment.ACTIVE_STATUSES).order_by("-created_at", "-id").first()
            if blocking:
                code = "payment_needs_review" if blocking.status == Payment.Status.NEEDS_REVIEW else "payment_in_progress"
                detail = "This payment needs support review before another attempt." if code == "payment_needs_review" else "A payment request is already in progress. Check its status before trying again."
                return payment_conflict(code, detail, blocking)
            quote = payment_quote(purchase.price_snapshot)
            if quote["total_charge"] > Decimal("5000000"):
                return Response({"detail": "The package price plus provider fee exceeds the Relworx transaction limit."}, status=status.HTTP_409_CONFLICT)
            payment = Payment.objects.create(
                purchase=purchase,
                user=request.user,
                package=purchase.package,
                amount=quote["total_charge"],
                base_amount=quote["base_amount"],
                provider_fee_estimate=quote["provider_fee_estimate"],
                provider_net_amount=quote["base_amount"],
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
            payment = record_initiation_result(payment.pk, source="checkout", error=error)
            if payment.can_retry:
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
            if payment.can_retry:
                data = PaymentSerializer(payment, context={"request": request}).data
                return Response({"detail": str(error), "payment": data}, status=status.HTTP_502_BAD_GATEWAY)
            return Response(PaymentSerializer(payment, context={"request": request}).data, status=status.HTTP_202_ACCEPTED)

        payment = record_initiation_result(payment.pk, source="checkout", response=provider_response)
        from .analytics_services import record_journey
        record_journey(request, "prompt_sent", payment.package, payment.purchase, event_id=f"prompt-{payment.pk}")
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

    supplied = parts.get("v", "")
    configured = urlsplit(settings.RELWORX_WEBHOOK_URL)
    legacy_url = f"{configured.scheme}://{configured.netloc}/api/v1/payments/relworx/webhook/"
    for webhook_url in dict.fromkeys([settings.RELWORX_WEBHOOK_URL, legacy_url]):
        signed = f"{webhook_url}{timestamp}"
        for key in sorted(["status", "customer_reference", "internal_reference"]):
            signed += f"{key}{payload.get(key, '')}"
        digest = hmac.new(
            settings.RELWORX_WEBHOOK_SIGNING_KEY.encode("utf-8"),
            signed.encode("utf-8"),
            hashlib.sha256,
        ).digest()
        if hmac.compare_digest(supplied, digest.hex()) or hmac.compare_digest(
            supplied, base64.b64encode(digest).decode("ascii")
        ):
            return True
    return False


class PaymentRefreshView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request, pk):
        payment = Payment.objects.select_related("purchase__package", "subscription_order__plan", "package", "user").filter(pk=pk, user=request.user).first()
        if not payment:
            return Response({"detail": "Payment not found."}, status=status.HTTP_404_NOT_FOUND)
        if payment.status not in Payment.ACTIVE_STATUSES:
            return Response(PaymentSerializer(payment, context={"request": request}).data)
        reconciled, outcome = reconcile_payment(payment.pk, source="customer_refresh")
        reconciled = Payment.objects.select_related("purchase__package", "subscription_order__plan", "package", "user").get(pk=reconciled.pk)
        response_status = status.HTTP_400_BAD_REQUEST if outcome == "mismatch" else status.HTTP_200_OK
        return Response(PaymentSerializer(reconciled, context={"request": request}).data, status=response_status)


class PaymentReceiptView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, pk):
        payment = Payment.objects.select_related("user", "package", "purchase", "subscription_order__plan").filter(pk=pk).first()
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
            payment = Payment.objects.select_for_update().select_related("purchase__package", "subscription_order__plan").filter(reference=reference).first()
            if not payment:
                payout = PayoutRequest.objects.select_for_update().filter(reference=reference).first()
                if payout:
                    provider_status = str(payload.get("status") or payload.get("request_status") or "").lower()
                    try:
                        amount = Decimal(str(payload.get("amount", payout.amount)))
                    except Exception:
                        return Response({"detail": "Invalid payout amount."}, status=status.HTTP_400_BAD_REQUEST)
                    currency = str(payload.get("currency") or payout.currency).upper()
                    if amount != payout.amount or currency != payout.currency.upper():
                        return Response({"detail": "Payout amount or currency mismatch."}, status=status.HTTP_400_BAD_REQUEST)
                    internal_reference = str(payload.get("internal_reference") or "")
                    if payout.provider_request_id and internal_reference != payout.provider_request_id:
                        return Response({"detail": "Payout internal reference mismatch."}, status=status.HTTP_400_BAD_REQUEST)
                    if provider_status == "success":
                        settle_payout(payout.pk, "success", payload)
                    elif provider_status in {"failed", "failure", "declined", "cancelled", "canceled"}:
                        settle_payout(payout.pk, "failed", payload)
                    return Response({"status": "ok"})
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


class WalletView(APIView):
    beneficiary = "owner"
    permission_classes = [IsProductOwner]

    def get(self, request):
        payload = wallet_payload(self.beneficiary, request.user)
        # The owner dashboard deliberately reports BK's internal ledger balance.
        # Relworx's shared provider balance is an operational concern exposed to
        # the developer only, and querying it on every owner refresh adds latency.
        if self.beneficiary == "developer":
            try:
                payload["provider_balance"] = check_wallet_balance().get("balance")
            except RelworxError:
                payload["provider_balance"] = None
        else:
            payload["provider_balance"] = None
        return Response(payload)


class OwnerWalletView(WalletView):
    pass


class DeveloperWalletView(WalletView):
    beneficiary = "developer"
    permission_classes = [IsDeveloper]


class PayoutRequestView(WalletView):
    throttle_classes = [PayoutRateThrottle]
    def post(self, request):
        token = request.data.get("request_token")
        if token:
            existing = PayoutRequest.objects.filter(client_token=token, requested_by=request.user, beneficiary=self.beneficiary).first()
            if existing:
                try:
                    same_amount = existing.amount == Decimal(str(request.data.get("amount")))
                except Exception:
                    same_amount = False
                if not same_amount:
                    return Response({"detail": "This request token belongs to a different withdrawal amount."}, status=409)
                return Response({"message": "Existing payout request retrieved.", "request": serialize_payout(existing), **wallet_payload(self.beneficiary, request.user)}, status=200)
        from .wallet_services import wallet_account
        if wallet_account(self.beneficiary).payouts_frozen:
            return Response({"detail": "Wallet frozen please contact your developer."}, status=423)
        if not request.user.check_password(str(request.data.get("password", ""))):
            return Response({"password": ["Incorrect password."]}, status=status.HTTP_400_BAD_REQUEST)
        try:
            payout = create_payout(
                self.beneficiary, request.data.get("amount"), request.user,
                request.data.get("request_token"),
            )
        except (DjangoValidationError, TypeError, ValueError) as error:
            details = getattr(error, "message_dict", {"amount": [str(error)]})
            return Response(details, status=status.HTTP_400_BAD_REQUEST)
        if settings.PAYOUT_EXECUTION_MODE == "immediate":
            payout = execute_payout(payout.pk)
        if self.beneficiary == "owner":
            from accounts.payout_security import recipients
            from accounts.security_services import queue_notice
            queue_notice(
                f"owner-payout:{payout.pk}",
                recipients(request.user),
                f"BK owner payout {payout.reference}",
                (
                    "An owner payout was created.\n\n"
                    f"Amount: {payout.currency} {payout.amount:,.2f}\n"
                    f"Destination: {payout.destination_msisdn}\n"
                    f"Reference: {payout.reference}\n"
                    f"Status: {payout.status}\n"
                    f"Requested by: {request.user.full_name} ({request.user.phone})\n"
                    f"Time: {timezone.localtime(payout.created_at).isoformat()}\n"
                    f"Connection address: {request.META.get('REMOTE_ADDR') or 'Unavailable'}\n"
                    f"Device: {request.META.get('HTTP_USER_AGENT', 'Unknown device')[:300]}"
                ),
            )
        return Response({
            "message": "Payout submitted to Relworx." if payout.status == PayoutRequest.Status.PROCESSING else (payout.failure_reason if payout.status == PayoutRequest.Status.FAILED else "Payout request reserved."),
            "request": serialize_payout(payout),
            **wallet_payload(self.beneficiary, request.user),
        }, status=status.HTTP_201_CREATED)


class OwnerPayoutRequestView(PayoutRequestView):
    pass


class DeveloperPayoutRequestView(PayoutRequestView):
    beneficiary = "developer"
    permission_classes = [IsDeveloper]


class PayoutCancelView(WalletView):
    def post(self, request, pk):
        payout = PayoutRequest.objects.filter(pk=pk).first()
        if not payout:
            return Response({"detail": "Payout request not found."}, status=status.HTTP_404_NOT_FOUND)
        try:
            payout = cancel_payout(payout, request.user, self.beneficiary)
        except PermissionError as error:
            return Response({"detail": str(error)}, status=status.HTTP_403_FORBIDDEN)
        except DjangoValidationError as error:
            return Response(error.message_dict, status=status.HTTP_400_BAD_REQUEST)
        return Response({"request": serialize_payout(payout), **wallet_payload(self.beneficiary, request.user)})


class OwnerPayoutCancelView(PayoutCancelView):
    pass


class DeveloperPayoutCancelView(PayoutCancelView):
    beneficiary = "developer"
    permission_classes = [IsDeveloper]


class DeveloperAnalyticsView(APIView):
    """Read-only operational analytics for the isolated developer console."""

    permission_classes = [IsDeveloper]

    def get(self, request):
        now = timezone.now()
        local_now = timezone.localtime(now)
        today_start = timezone.make_aware(datetime.combine(local_now.date(), datetime_time.min))
        week_start = today_start - timedelta(days=local_now.weekday())
        month_start = timezone.make_aware(datetime.combine(local_now.date().replace(day=1), datetime_time.min))
        chart_start = today_start - timedelta(days=29)

        payments = Payment.objects.select_related("package", "subscription_order__plan", "user")
        paid = payments.filter(status=Payment.Status.PAID)
        today_attempts = payments.filter(created_at__gte=today_start)
        today_paid = paid.filter(confirmed_at__gte=today_start)
        pending_statuses = [Payment.Status.CREATED, Payment.Status.SUBMITTED, Payment.Status.PENDING]
        failed_statuses = [Payment.Status.FAILED, Payment.Status.EXPIRED, Payment.Status.CANCELLED]

        by_status = {
            row["status"]: {"count": row["count"], "amount": row["amount"] or Decimal("0")}
            for row in payments.values("status").annotate(count=Count("id"), amount=Sum("amount"))
        }
        by_package = {
            row["package__name"]: row["amount"] or Decimal("0")
            for row in paid.filter(package__isnull=False).values("package__name").annotate(amount=Sum("amount")).order_by("package__name")
        }
        for row in paid.filter(subscription_order__isnull=False).values(
            "subscription_order__plan__name",
        ).annotate(amount=Sum("amount")):
            by_package[row["subscription_order__plan__name"]] = row["amount"] or Decimal("0")
        by_provider = {
            (row["provider"] or "relworx"): {"count": row["count"], "amount": row["amount"] or Decimal("0")}
            for row in paid.values("provider").annotate(count=Count("id"), amount=Sum("amount"))
        }
        failures = {
            (row["failure_category"] or "unclassified"): {
                "count": row["count"], "amount": row["amount"] or Decimal("0"),
            }
            for row in payments.filter(status__in=failed_statuses)
            .values("failure_category").annotate(count=Count("id"), amount=Sum("amount"))
        }

        recent_payments = []
        for payment in payments.order_by("-created_at")[:50]:
            digits = "".join(character for character in payment.payer_msisdn if character.isdigit())
            masked_phone = f"{'*' * max(0, len(digits) - 4)}{digits[-4:]}" if digits else ""
            recent_payments.append({
                "id": payment.pk,
                "amount": payment.amount,
                "package": payment.product_name,
                "status": payment.status,
                "provider": payment.provider,
                "phone": masked_phone,
                "reference": payment.reference,
                "provider_reference": payment.internal_reference,
                "failure_category": payment.failure_category,
                "failure_reason": payment.failure_reason,
                "created_at": payment.created_at,
                "confirmed_at": payment.confirmed_at,
                "failed_at": payment.failed_at,
            })

        commission_total = paid.aggregate(value=Sum("developer_commission"))["value"] or Decimal("0")
        developer_wallet = wallet_summary("developer")
        completed_payouts = PayoutRequest.objects.filter(
            beneficiary="developer", status=PayoutRequest.Status.COMPLETED,
        )
        completed_payout_total = completed_payouts.aggregate(value=Sum("amount"))["value"] or Decimal("0")
        payout_status = {
            row["status"]: {"count": row["count"], "amount": row["amount"] or Decimal("0")}
            for row in PayoutRequest.objects.filter(beneficiary="developer")
            .values("status").annotate(count=Count("id"), amount=Sum("amount"))
        }

        customers = User.objects.filter(is_staff=False, is_superuser=False).exclude(
            groups__name__in=["Developer", "Product Owner"],
        ).distinct()
        purchases = Purchase.objects.select_related("package")
        active_packages = Package.objects.filter(deleted_at__isnull=True, is_active=True, closes_at__gt=now)

        revenue_chart = list(
            paid.filter(confirmed_at__gte=chart_start).annotate(date=TruncDate("confirmed_at"))
            .values("date").annotate(amount=Sum("amount")).order_by("date")
        )
        signup_chart = list(
            customers.filter(created_at__gte=chart_start).annotate(date=TruncDate("created_at"))
            .values("date").annotate(count=Count("id")).order_by("date")
        )

        total_attempts = payments.count()
        total_paid = paid.count()
        attempted_amount = payments.aggregate(value=Sum("amount"))["value"] or Decimal("0")
        total_revenue = paid.aggregate(value=Sum("amount"))["value"] or Decimal("0")
        pending_queryset = payments.filter(status__in=pending_statuses)
        failed_queryset = payments.filter(status__in=failed_statuses)
        missing_transactions = paid.filter(Q(provider_transaction_id="") | Q(provider_transaction_id__isnull=True)).count()

        return Response({
            "generated_at": now,
            "timezone": settings.TIME_ZONE,
            "finance": {
                "attempted_count": total_attempts,
                "attempted_amount": attempted_amount,
                "total_revenue": total_revenue,
                "revenue_today": today_paid.aggregate(value=Sum("amount"))["value"] or Decimal("0"),
                "revenue_this_week": paid.filter(confirmed_at__gte=week_start).aggregate(value=Sum("amount"))["value"] or Decimal("0"),
                "revenue_this_month": paid.filter(confirmed_at__gte=month_start).aggregate(value=Sum("amount"))["value"] or Decimal("0"),
                "confirmation_rate": round(total_paid * 100 / total_attempts, 2) if total_attempts else 0,
                "pending_count": pending_queryset.count(),
                "pending_amount": pending_queryset.aggregate(value=Sum("amount"))["value"] or Decimal("0"),
                "failed_count": failed_queryset.count(),
                "failed_amount": failed_queryset.aggregate(value=Sum("amount"))["value"] or Decimal("0"),
                "today": {
                    "confirmed_count": today_paid.count(),
                    "confirmed_amount": today_paid.aggregate(value=Sum("amount"))["value"] or Decimal("0"),
                    "attempted_count": today_attempts.count(),
                    "attempted_amount": today_attempts.aggregate(value=Sum("amount"))["value"] or Decimal("0"),
                    "failed_count": today_attempts.filter(status__in=failed_statuses).count(),
                    "failed_amount": today_attempts.filter(status__in=failed_statuses).aggregate(value=Sum("amount"))["value"] or Decimal("0"),
                },
                "by_status": by_status,
                "by_package": by_package,
                "by_provider": by_provider,
                "failures_by_category": failures,
                "recent_payments": recent_payments,
                "audit": {
                    "ledger_count": total_attempts,
                    "missing_confirmation_count": paid.filter(confirmed_at__isnull=True).count(),
                    "missing_transaction_count": missing_transactions,
                    "healthy": not paid.filter(confirmed_at__isnull=True).exists() and not missing_transactions,
                },
            },
            "commission": {
                "enabled": settings.DEVELOPER_COMMISSION_RATE > 0,
                "ratio": settings.DEVELOPER_COMMISSION_RATE,
                "today_earned": developer_wallet["today_earned"],
                "total_earned": commission_total,
                "wallet_credited": developer_wallet["credited"],
                "available": developer_wallet["available"],
                "reserved": developer_wallet["reserved"],
                "total_withdrawn": completed_payout_total,
                "by_payout_status": payout_status,
                "recent": [
                    {
                        "id": payment.pk, "payment_reference": payment.reference,
                        "package": payment.product_name, "amount": payment.amount,
                        "commission": payment.developer_commission,
                        "confirmed_at": payment.confirmed_at,
                    }
                    for payment in paid.filter(developer_commission__gt=0).order_by("-confirmed_at")[:25]
                ],
            },
            "users": {
                "total": customers.count(),
                "new_today": customers.filter(created_at__gte=today_start).count(),
                "new_this_week": customers.filter(created_at__gte=week_start).count(),
                "new_this_month": customers.filter(created_at__gte=month_start).count(),
            },
            "packages": {
                "total": Package.objects.filter(deleted_at__isnull=True).count(),
                "active_total": active_packages.count(),
                "purchase_count": purchases.count(),
                "paid_purchase_count": purchases.filter(paid_at__isnull=False).count(),
                "items": list(
                    Package.objects.filter(deleted_at__isnull=True).annotate(
                        purchase_count=Count("purchases"),
                        paid_count=Count("purchases", filter=Q(purchases__paid_at__isnull=False)),
                    ).values("id", "name", "package_type", "price", "currency", "is_active", "closes_at", "purchase_count", "paid_count")
                ),
            },
            "charts": {"revenue": revenue_chart, "signups": signup_chart},
        })


class DeveloperPaymentGatewayView(APIView):
    permission_classes = [IsDeveloper]

    def patch(self, request):
        from .wallet_services import wallet_account
        if not isinstance(request.data.get("owner_payouts_frozen"), bool):
            return Response({"detail": "A boolean owner_payouts_frozen is required."}, status=400)
        account = wallet_account("owner")
        account.payouts_frozen = request.data["owner_payouts_frozen"]
        account.save(update_fields=["payouts_frozen"])
        record_activity(request, category=ActivityLog.Category.ADMINISTRATION,
                        action="wallet.freeze_changed", description=f"Owner payout freeze set to {account.payouts_frozen}.")
        return self.get(request)

    def get(self, request):
        from .wallet_services import wallet_account
        configured = bool(settings.RELWORX_API_KEY and settings.RELWORX_ACCOUNT_NO)
        return Response({
            "owner_payouts_frozen": wallet_account("owner").payouts_frozen,
            "active_provider": "relworx",
            "provider": {
                "name": "Relworx",
                "configured": configured,
                "ready": configured,
                "message": "Relworx is configured for BK payments." if configured else "Relworx credentials are incomplete.",
                "capabilities": {
                    "collections": configured,
                    "payouts": settings.RELWORX_PAYOUTS_ENABLED,
                    "signed_webhooks": bool(settings.RELWORX_WEBHOOK_SIGNING_KEY),
                    "scheduled_reconciliation": True,
                },
            },
        })


class DeveloperPaymentGatewayCheckView(APIView):
    permission_classes = [IsDeveloper]

    def post(self, request):
        if not settings.RELWORX_API_KEY or not settings.RELWORX_ACCOUNT_NO:
            return Response({"ready": False, "message": "Relworx credentials are incomplete."}, status=status.HTTP_422_UNPROCESSABLE_ENTITY)
        try:
            result = check_wallet_balance("UGX")
        except RelworxError as error:
            return Response({"ready": False, "message": str(error)}, status=status.HTTP_422_UNPROCESSABLE_ENTITY)
        return Response({
            "ready": bool(result.get("success")),
            "checked_at": timezone.now(),
            "message": "Relworx authentication and wallet connection are working.",
        })


class DeveloperReconciliationHealthView(APIView):
    permission_classes = [IsDeveloper]

    def get(self, request):
        return Response(reconciliation_health_payload(include_history=True))


class OwnerDashboardView(APIView):
    permission_classes = [IsProductOwner]

    def get(self, request):
        now = timezone.now()
        return Response({
            "customers": User.objects.filter(is_staff=False, is_superuser=False).exclude(groups__name__in=["Developer", "Product Owner"]).count(),
            "active_packages": Package.objects.filter(
                deleted_at__isnull=True,
                is_active=True,
                offer_type=Package.OfferType.ONE_OFF,
                closes_at__gt=now,
            ).count(),
            "active_subscribers": SubscriptionOrder.objects.filter(
                paid_at__isnull=False, access_starts_at__lte=now, access_ends_at__gt=now,
            ).values("user_id").distinct().count(),
            "pending_payments": Payment.objects.filter(status__in=[Payment.Status.SUBMITTED, Payment.Status.PENDING]).count(),
            "payments_needing_review": Payment.objects.filter(status=Payment.Status.NEEDS_REVIEW).count(),
            "open_payment_alerts": PaymentAlert.objects.filter(resolved_at__isnull=True).count(),
            "paid_payments": Payment.objects.filter(status=Payment.Status.PAID).count(),
            "revenue": Payment.objects.filter(status=Payment.Status.PAID).aggregate(total=Sum("amount"))["total"] or 0,
            "wallet": wallet_summary("owner"),
            "reconciliation": reconciliation_health_payload(),
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

    def get_queryset(self):
        queryset = super().get_queryset()
        offer_type = self.request.query_params.get("offer_type")
        if offer_type in Package.OfferType.values:
            queryset = queryset.filter(offer_type=offer_type)
        return queryset

    @staticmethod
    def grant_to_active_subscribers(package):
        if (
            package.offer_type != Package.OfferType.WEEKLY
            or not package.is_active
            or package.deleted_at
            or package.closes_at <= timezone.now()
        ):
            return
        now = timezone.now()
        orders = SubscriptionOrder.objects.filter(
            paid_at__isnull=False, access_starts_at__lte=now, access_ends_at__gt=now,
        )
        SubscriptionContentGrant.objects.bulk_create(
            [SubscriptionContentGrant(order=order, package=package) for order in orders],
            ignore_conflicts=True,
        )

    def perform_create(self, serializer):
        super().perform_create(serializer)
        self.grant_to_active_subscribers(serializer.instance)

    def perform_update(self, serializer):
        super().perform_update(serializer)
        self.grant_to_active_subscribers(serializer.instance)

    @action(detail=True, methods=["get"], url_path="paid-image")
    def paid_image(self, request, pk=None):
        package = self.get_object()
        image = package.paid_image_optimized or package.paid_image
        return paid_image_response(image.name if image else "", package.paid_image.storage)

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


class OwnerSubscriptionPlanView(APIView):
    permission_classes = [IsProductOwner]

    @staticmethod
    def get_plan():
        return SubscriptionPlan.objects.get_or_create(
            key="weekly",
            defaults={"name": "Weekly Plan", "price": 1000, "currency": "UGX", "duration_days": 7, "is_active": False},
        )[0]

    def get(self, request):
        return Response(SubscriptionPlanSerializer(self.get_plan(), context={"request": request}).data)

    def patch(self, request):
        plan = self.get_plan()
        serializer = SubscriptionPlanSerializer(plan, data=request.data, partial=True, context={"request": request})
        serializer.is_valid(raise_exception=True)
        serializer.save(duration_days=7)
        record_activity(
            request,
            category=ActivityLog.Category.SUBSCRIPTION,
            action="subscription_plan.updated",
            description=f"{request.user.full_name} updated the Weekly Plan.",
            target=plan,
        )
        return Response(serializer.data)


class OwnerWeeklySubscriberView(APIView):
    permission_classes = [IsProductOwner]

    def get(self, request):
        now = timezone.now()
        active_filter = Q(access_starts_at__lte=now, access_ends_at__gt=now)
        queryset = SubscriptionOrder.objects.filter(paid_at__isnull=False).select_related(
            "user", "plan",
        ).prefetch_related(
            "payments__duplicate_of",
        ).annotate(
            available_content_count=Count(
                "content_grants",
                filter=Q(
                    content_grants__package__offer_type=Package.OfferType.WEEKLY,
                    content_grants__package__deleted_at__isnull=True,
                    content_grants__package__is_active=True,
                    content_grants__package__closes_at__gt=now,
                ),
                distinct=True,
            ),
        )
        summary = {
            "active": queryset.filter(active_filter).values("user_id").distinct().count(),
            "waiting": queryset.filter(active_filter, user__is_blocked=False, available_content_count=0).values("user_id").distinct().count(),
            "available": queryset.filter(active_filter, user__is_blocked=False, available_content_count__gt=0).values("user_id").distinct().count(),
            "expiring": queryset.filter(
                active_filter, user__is_blocked=False, access_ends_at__lte=now + timedelta(days=1),
            ).values("user_id").distinct().count(),
        }
        selected_status = request.query_params.get("status", "active")
        filters = {
            "active": active_filter,
            "waiting": active_filter & Q(user__is_blocked=False, available_content_count=0),
            "available": active_filter & Q(user__is_blocked=False, available_content_count__gt=0),
            "expiring": active_filter & Q(user__is_blocked=False, access_ends_at__lte=now + timedelta(days=1)),
            "expired": Q(access_ends_at__lte=now),
        }
        if selected_status not in filters:
            return Response({"detail": "Choose a supported subscriber status."}, status=status.HTTP_400_BAD_REQUEST)
        queryset = queryset.filter(filters[selected_status])
        search = request.query_params.get("search", "").strip()
        if search:
            queryset = queryset.filter(
                Q(user__phone__icontains=search)
                | Q(user__first_name__icontains=search)
                | Q(user__last_name__icontains=search)
            )
        queryset = queryset.annotate(
            waiting_rank=Case(
                When(user__is_blocked=False, available_content_count=0, then=0),
                default=1,
                output_field=IntegerField(),
            )
        ).order_by("waiting_rank", "access_ends_at", "id")
        return Response({
            "generated_at": now,
            "status": selected_status,
            "summary": summary,
            "results": OwnerWeeklySubscriberSerializer(queryset, many=True, context={"request": request}).data,
        })


class OwnerFreePickViewSet(OwnerAuditMixin, viewsets.ModelViewSet):
    queryset = FreePick.objects.all()
    serializer_class = OwnerFreePickSerializer
    permission_classes = [IsProductOwner]

    def deactivate_other_free_picks(self, free_pick):
        if free_pick.is_active:
            FreePick.objects.filter(is_active=True).exclude(pk=free_pick.pk).update(
                is_active=False,
                updated_at=timezone.now(),
            )

    @transaction.atomic
    def perform_create(self, serializer):
        free_pick = serializer.save(created_by=self.request.user)
        self.deactivate_other_free_picks(free_pick)
        self.record_change(free_pick, "created")

    @transaction.atomic
    def perform_update(self, serializer):
        free_pick = serializer.save()
        self.deactivate_other_free_picks(free_pick)
        self.record_change(free_pick, "updated")

    @action(detail=True, methods=["get"], url_path="reveal-image")
    def reveal_image(self, request, pk=None):
        free_pick = self.get_object()
        return paid_image_response(free_pick.reveal_image.name, free_pick.reveal_image.storage)


class OwnerPaymentViewSet(viewsets.ReadOnlyModelViewSet):
    queryset = Payment.objects.select_related("user", "package", "purchase", "subscription_order__plan", "duplicate_of").all()
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
        settlement = self.request.query_params.get("settlement", "").strip()
        search = self.request.query_params.get("search", "").strip()
        _, _, start_at, end_at = _date_bounds(self.request)
        if status_filter:
            queryset = queryset.filter(status=status_filter)
        if provider:
            queryset = queryset.filter(provider__iexact=provider)
        if failure_category:
            queryset = queryset.filter(failure_category=failure_category)
        if settlement in Payment.SettlementClassification.values:
            queryset = queryset.filter(settlement_classification=settlement)
        if search:
            queryset = queryset.filter(
                Q(reference__icontains=search)
                | Q(internal_reference__icontains=search)
                | Q(provider_transaction_id__icontains=search)
                | Q(user__phone__icontains=search)
                | Q(user__first_name__icontains=search)
                | Q(user__last_name__icontains=search)
                | Q(package__name__icontains=search)
                | Q(subscription_order__plan__name__icontains=search)
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
        package_revenue = paid.aggregate(value=Sum("base_amount"))["value"] or Decimal("0")
        fee_estimates = paid.aggregate(value=Sum("provider_fee_estimate"))["value"] or Decimal("0")
        fees = paid.aggregate(value=Sum("provider_charge"))["value"] or Decimal("0")
        commissions = paid.aggregate(value=Sum("developer_commission"))["value"] or Decimal("0")
        by_status = list(queryset.values("status").annotate(count=Count("id"), amount=Sum("amount")).order_by("status"))
        by_provider = list(queryset.values("provider").annotate(count=Count("id"), amount=Sum("amount")).order_by("provider"))
        by_package = list(queryset.filter(package__isnull=False).values("package_id", "package__name").annotate(count=Count("id"), amount=Sum("amount")).order_by("package__name"))
        subscription_rows = queryset.filter(subscription_order__isnull=False).values(
            "subscription_order__plan_id", "subscription_order__plan__name",
        ).annotate(count=Count("id"), amount=Sum("amount"))
        by_package.extend({
            "package_id": None,
            "package__name": row["subscription_order__plan__name"],
            "count": row["count"],
            "amount": row["amount"],
        } for row in subscription_rows)
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
                "package_revenue": package_revenue,
                "provider_fee_estimates": fee_estimates,
                "provider_fees": fees,
                "net_revenue": gross - fees,
                "developer_commissions": commissions,
                "owner_net": gross - fees - commissions,
                "pending": queryset.filter(status__in=[Payment.Status.SUBMITTED, Payment.Status.PENDING]).count(),
                "needs_review": queryset.filter(status=Payment.Status.NEEDS_REVIEW).count(),
                "duplicate_settlements": paid.filter(settlement_classification=Payment.SettlementClassification.DUPLICATE).count(),
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
            "Package", "Package price", "Customer charge", "Currency", "Status", "Provider",
            "Estimated fee", "Actual provider fee", "Provider net", "Developer commission",
            "Failure category", "Failure reason", "Created", "Confirmed",
            "Settlement classification", "Original payment",
        ])
        for payment in self.filter_queryset(self.get_queryset()).iterator():
            writer.writerow([
                payment.reference, payment.internal_reference, payment.provider_transaction_id,
                payment.user.full_name, payment.user.phone, payment.product_name,
                payment.base_amount or payment.amount, payment.amount, payment.currency,
                payment.status, payment.provider, payment.provider_fee_estimate or "",
                payment.provider_charge or "", payment.provider_net_amount or "",
                payment.developer_commission,
                payment.failure_category, payment.failure_reason, payment.created_at.isoformat(),
                payment.confirmed_at.isoformat() if payment.confirmed_at else "",
                payment.settlement_classification,
                payment.duplicate_of.reference if payment.duplicate_of_id else "",
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
    queryset = User.objects.filter(is_staff=False, is_superuser=False).exclude(groups__name__in=["Developer", "Product Owner"]).order_by("-created_at")
    serializer_class = UserSerializer
    permission_classes = [IsProductOwner]

    @action(detail=True, methods=["post"])
    def block(self, request, pk=None):
        customer = self.get_object()
        if customer.is_product_owner or customer.is_developer or customer.pk == request.user.pk:
            return Response({"detail": "Privileged accounts cannot be blocked here."}, status=403)
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


class DeveloperCustomerViewSet(OwnerCustomerViewSet):
    queryset = User.objects.all().order_by("-created_at")
    permission_classes = [IsDeveloper]

    @action(detail=True, methods=["post"])
    def block(self, request, pk=None):
        customer = self.get_object()
        if customer.pk == request.user.pk or customer.is_superuser or customer.is_developer or customer.is_product_owner:
            return Response({"detail": "Privileged accounts cannot be blocked here."}, status=403)
        return super().block(request, pk)
