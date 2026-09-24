import base64
import hashlib
import hmac
import time
from decimal import Decimal, InvalidOperation

from django.conf import settings
from django.db import transaction
from django.db.models import Case, Count, IntegerField, Q, Sum, When
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from accounts.models import ActivityLog, User
from accounts.permissions import IsProductOwner
from accounts.serializers import ActivityLogSerializer, UserSerializer
from accounts.services import record_activity
from .models import Package, Payment, Prediction, Purchase, RecentWin, Testimonial
from .relworx import RelworxError, request_mobile_money_payment
from .serializers import (
    PackageSerializer,
    PaymentAttemptSerializer,
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
        return Package.objects.filter(deleted_at__isnull=True).annotate(
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
            if purchase.payments.filter(status=Payment.Status.PENDING).exists():
                return Response({"detail": "A payment request is already pending."}, status=status.HTTP_409_CONFLICT)
            payment = Payment.objects.create(
                purchase=purchase,
                user=request.user,
                package=purchase.package,
                amount=purchase.price_snapshot,
                currency=purchase.currency_snapshot,
                payer_msisdn=serializer.validated_data["phone"],
            )
        try:
            provider_response = request_mobile_money_payment(payment)
        except RelworxError as error:
            payment.provider_message = str(error)[:240]
            update_fields = ["provider_message", "updated_at"]
            if not error.uncertain:
                payment.status = Payment.Status.FAILED
                update_fields.insert(0, "status")
            payment.save(update_fields=update_fields)
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
        payment.provider_message = str(provider_response.get("message", "Payment request sent."))[:240]
        payment.save(update_fields=["internal_reference", "provider_message", "updated_at"])
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

            internal_reference = str(payload.get("internal_reference", ""))
            if payment.internal_reference and not hmac.compare_digest(payment.internal_reference, internal_reference):
                return self._reject_mismatch(request, payment, "internal reference")

            provider_status = str(payload.get("status", "")).lower()
            safe_payload = {
                key: payload.get(key)
                for key in ["status", "customer_reference", "internal_reference", "currency", "amount", "provider", "charge", "completed_at"]
                if key in payload
            }
            if provider_status == "success":
                try:
                    amount = Decimal(str(payload.get("amount")))
                except (InvalidOperation, TypeError):
                    return self._reject_mismatch(request, payment, "amount")
                if amount != payment.amount:
                    return self._reject_mismatch(request, payment, "amount")
                if str(payload.get("currency", "")).upper() != payment.currency.upper():
                    return self._reject_mismatch(request, payment, "currency")
                if payment.status == Payment.Status.PAID:
                    record_activity(
                        request,
                        category=ActivityLog.Category.SUBSCRIPTION,
                        action="payment.webhook_duplicate",
                        description=f"Duplicate success webhook received for {payment.reference}.",
                        target=payment,
                    )
                    return Response({"status": "ok"})

                completed_at = parse_datetime(str(payload.get("completed_at", ""))) or timezone.now()
                payment.status = Payment.Status.PAID
                payment.paid_at = completed_at
                payment.provider_message = str(payload.get("message", "Payment completed successfully."))[:240]
                payment.provider_payload = safe_payload
                payment.save(update_fields=["status", "paid_at", "provider_message", "provider_payload", "updated_at"])
                payment.purchase.complete(completed_at)
                record_activity(
                    request,
                    category=ActivityLog.Category.SUBSCRIPTION,
                    action="payment.completed",
                    description=f"Relworx payment {payment.reference} completed.",
                    target=payment,
                    metadata={"provider": "relworx"},
                )
            elif provider_status == "failed":
                if payment.status == Payment.Status.PAID:
                    return Response({"status": "ok"})
                payment.status = Payment.Status.FAILED
                payment.provider_message = str(payload.get("message", "Payment failed."))[:240]
                payment.provider_payload = safe_payload
                payment.save(update_fields=["status", "provider_message", "provider_payload", "updated_at"])
                record_activity(
                    request,
                    category=ActivityLog.Category.SUBSCRIPTION,
                    action="payment.failed",
                    description=f"Relworx payment {payment.reference} failed.",
                    target=payment,
                )
            else:
                return Response({"detail": "Unsupported payment status."}, status=status.HTTP_400_BAD_REQUEST)
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
            "active_packages": Package.objects.filter(deleted_at__isnull=True, closes_at__gt=now).count(),
            "pending_payments": Payment.objects.filter(status=Payment.Status.PENDING).count(),
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
