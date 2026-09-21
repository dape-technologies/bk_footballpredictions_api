from django.db import transaction
from django.db.models.deletion import ProtectedError
from django.db.models import Count, Q, Sum
from django.utils import timezone
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from accounts.models import ActivityLog, User
from accounts.permissions import IsProductOwner
from accounts.serializers import ActivityLogSerializer, UserSerializer
from accounts.services import record_activity
from .models import Package, Prediction, RecentWin, Subscription, Testimonial
from .serializers import (
    PackageSerializer,
    PredictionSerializer,
    RecentWinSerializer,
    SubscriptionRequestSerializer,
    SubscriptionSerializer,
    TestimonialSerializer,
)


def expire_subscriptions(user=None):
    query = Subscription.objects.filter(
        status=Subscription.Status.ACTIVE,
        expires_at__lte=timezone.now(),
    )
    if user:
        query = query.filter(user=user)
    query.update(status=Subscription.Status.EXPIRED)


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
    serializer_class = PackageSerializer
    permission_classes = [AllowAny]
    lookup_field = "slug"

    def get_queryset(self):
        return Package.objects.filter(is_active=True)


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


class MySubscriptionsView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        expire_subscriptions(request.user)
        subscriptions = request.user.subscriptions.select_related("package", "approved_by")
        return Response(SubscriptionSerializer(subscriptions, many=True, context={"request": request}).data)

    def post(self, request):
        serializer = SubscriptionRequestSerializer(data=request.data, context={"request": request})
        serializer.is_valid(raise_exception=True)
        subscription = serializer.save()
        record_activity(
            request,
            category=ActivityLog.Category.SUBSCRIPTION,
            action="subscription.requested",
            description=f"{request.user.full_name} requested {subscription.package.name} access.",
            target=subscription,
        )
        return Response(
            SubscriptionSerializer(subscription, context={"request": request}).data,
            status=status.HTTP_201_CREATED,
        )


class CancelSubscriptionView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request, pk):
        subscription = request.user.subscriptions.filter(pk=pk).first()
        if not subscription:
            return Response({"detail": "Subscription not found."}, status=status.HTTP_404_NOT_FOUND)
        if subscription.status not in [Subscription.Status.PENDING, Subscription.Status.ACTIVE]:
            return Response({"detail": "This subscription cannot be cancelled."}, status=status.HTTP_409_CONFLICT)
        subscription.status = Subscription.Status.CANCELLED
        subscription.customer_message = "Cancelled by customer."
        subscription.save(update_fields=["status", "customer_message", "updated_at"])
        record_activity(
            request,
            category=ActivityLog.Category.SUBSCRIPTION,
            action="subscription.cancelled_by_customer",
            description=f"{request.user.full_name} cancelled {subscription.package.name} access.",
            target=subscription,
        )
        return Response(SubscriptionSerializer(subscription, context={"request": request}).data)


class OwnerDashboardView(APIView):
    permission_classes = [IsProductOwner]

    def get(self, request):
        expire_subscriptions()
        today = timezone.localdate()
        return Response({
            "customers": User.objects.filter(is_staff=False).count(),
            "pending_requests": Subscription.objects.filter(status=Subscription.Status.PENDING).count(),
            "active_subscriptions": Subscription.objects.filter(status=Subscription.Status.ACTIVE).count(),
            "published_predictions": Prediction.objects.filter(is_published=True).count(),
            "wins": Prediction.objects.filter(result=Prediction.Result.WON).count(),
            "today_predictions": Prediction.objects.filter(kickoff_at__date=today).count(),
            "package_demand": list(
                Package.objects.annotate(
                    request_count=Count("subscriptions"),
                    active_count=Count("subscriptions", filter=Q(subscriptions__status=Subscription.Status.ACTIVE)),
                ).values("id", "name", "request_count", "active_count").order_by("display_order")
            ),
            "informational_value": Subscription.objects.filter(status=Subscription.Status.ACTIVE).aggregate(total=Sum("price_snapshot"))["total"] or 0,
            "recent_activity": ActivityLogSerializer(
                ActivityLog.objects.select_related("actor")[:8], many=True
            ).data,
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
    queryset = Package.objects.all()
    serializer_class = PackageSerializer
    permission_classes = [IsProductOwner]

    def destroy(self, request, *args, **kwargs):
        package = self.get_object()
        package_id = package.pk
        package_name = package.name
        try:
            package.delete()
        except ProtectedError:
            return Response(
                {"detail": "This package has subscription or prediction history. Close it instead of deleting it."},
                status=status.HTTP_409_CONFLICT,
            )
        record_activity(
            request,
            category=ActivityLog.Category.CONTENT,
            action="package.deleted",
            description=f"{request.user.full_name} deleted package {package_name}.",
            metadata={"package_id": package_id, "package_name": package_name},
        )
        return Response(status=status.HTTP_204_NO_CONTENT)


class OwnerPredictionViewSet(OwnerAuditMixin, viewsets.ModelViewSet):
    queryset = Prediction.objects.select_related("package").all()
    serializer_class = PredictionSerializer
    permission_classes = [IsProductOwner]

    def perform_create(self, serializer):
        instance = serializer.save(created_by=self.request.user)
        self.record_change(instance, "created")


class OwnerSubscriptionViewSet(viewsets.ReadOnlyModelViewSet):
    queryset = Subscription.objects.select_related("user", "package", "approved_by").all()
    serializer_class = SubscriptionSerializer
    permission_classes = [IsProductOwner]

    @action(detail=True, methods=["post"])
    def decide(self, request, pk=None):
        subscription = self.get_object()
        decision = request.data.get("decision")
        message = str(request.data.get("customer_message", ""))[:240]
        owner_note = str(request.data.get("owner_note", ""))
        with transaction.atomic():
            subscription = Subscription.objects.select_for_update().get(pk=subscription.pk)
            if decision == "approve":
                subscription.activate(request.user)
                subscription.customer_message = message or "Access approved. Your premium predictions are ready."
            elif decision == "reject":
                subscription.status = Subscription.Status.REJECTED
                subscription.customer_message = message or "This access request was not approved."
            elif decision == "cancel":
                subscription.status = Subscription.Status.CANCELLED
                subscription.customer_message = message or "This access was cancelled by the owner."
            else:
                return Response({"detail": "Choose approve, reject, or cancel."}, status=status.HTTP_400_BAD_REQUEST)
            subscription.owner_note = owner_note
            subscription.approved_by = request.user
            subscription.save()
        action_past_tense = {"approve": "approved", "reject": "rejected", "cancel": "cancelled"}[decision]
        record_activity(
            request,
            category=ActivityLog.Category.SUBSCRIPTION,
            action=f"subscription.{action_past_tense}",
            description=f"{request.user.full_name} {action_past_tense} {subscription.user.full_name}'s {subscription.package.name} request.",
            target=subscription,
        )
        return Response(SubscriptionSerializer(subscription, context={"request": request}).data)


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
