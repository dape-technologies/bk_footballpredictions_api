from django.db import transaction
from django.db.models.deletion import ProtectedError
from django.db.models import Count, Q, Sum
from django.utils import timezone
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.permissions import AllowAny, IsAdminUser, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from accounts.models import User
from accounts.serializers import UserSerializer
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
        return Response(SubscriptionSerializer(subscription, context={"request": request}).data)


class OwnerDashboardView(APIView):
    permission_classes = [IsAdminUser]

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
        })


class OwnerPackageViewSet(viewsets.ModelViewSet):
    queryset = Package.objects.all()
    serializer_class = PackageSerializer
    permission_classes = [IsAdminUser]

    def destroy(self, request, *args, **kwargs):
        package = self.get_object()
        try:
            package.delete()
        except ProtectedError:
            return Response(
                {"detail": "This package has subscription or prediction history. Close it instead of deleting it."},
                status=status.HTTP_409_CONFLICT,
            )
        return Response(status=status.HTTP_204_NO_CONTENT)


class OwnerPredictionViewSet(viewsets.ModelViewSet):
    queryset = Prediction.objects.select_related("package").all()
    serializer_class = PredictionSerializer
    permission_classes = [IsAdminUser]

    def perform_create(self, serializer):
        serializer.save(created_by=self.request.user)


class OwnerSubscriptionViewSet(viewsets.ReadOnlyModelViewSet):
    queryset = Subscription.objects.select_related("user", "package", "approved_by").all()
    serializer_class = SubscriptionSerializer
    permission_classes = [IsAdminUser]

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
        return Response(SubscriptionSerializer(subscription, context={"request": request}).data)


class OwnerRecentWinViewSet(viewsets.ModelViewSet):
    queryset = RecentWin.objects.all()
    serializer_class = RecentWinSerializer
    permission_classes = [IsAdminUser]


class OwnerTestimonialViewSet(viewsets.ModelViewSet):
    queryset = Testimonial.objects.all()
    serializer_class = TestimonialSerializer
    permission_classes = [IsAdminUser]


class OwnerCustomerViewSet(viewsets.ReadOnlyModelViewSet):
    queryset = User.objects.filter(is_staff=False).order_by("-created_at")
    serializer_class = UserSerializer
    permission_classes = [IsAdminUser]

    @action(detail=True, methods=["post"])
    def block(self, request, pk=None):
        customer = self.get_object()
        customer.is_blocked = bool(request.data.get("blocked", True))
        customer.is_active = not customer.is_blocked
        customer.save(update_fields=["is_blocked", "is_active", "updated_at"])
        return Response(self.get_serializer(customer).data)
