from django.urls import include, path
from rest_framework.routers import DefaultRouter

from .views import (
    MyPurchaseDetailView,
    MyPurchasesView,
    OwnerActivityViewSet,
    OwnerCustomerViewSet,
    OwnerDashboardView,
    OwnerPackageViewSet,
    OwnerPaymentViewSet,
    OwnerPredictionViewSet,
    OwnerRecentWinViewSet,
    OwnerTestimonialViewSet,
    PaymentAttemptView,
    PublicPackageViewSet,
    PublicPredictionViewSet,
    PublicRecentWinViewSet,
    PublicTestimonialViewSet,
    RelworxWebhookView,
)

public_router = DefaultRouter()
public_router.register("packages", PublicPackageViewSet, basename="package")
public_router.register("predictions", PublicPredictionViewSet, basename="prediction")
public_router.register("recent-wins", PublicRecentWinViewSet, basename="recent-win")
public_router.register("testimonials", PublicTestimonialViewSet, basename="testimonial")

owner_router = DefaultRouter()
owner_router.register("packages", OwnerPackageViewSet, basename="owner-package")
owner_router.register("payments", OwnerPaymentViewSet, basename="owner-payment")
owner_router.register("predictions", OwnerPredictionViewSet, basename="owner-prediction")
owner_router.register("recent-wins", OwnerRecentWinViewSet, basename="owner-recent-win")
owner_router.register("testimonials", OwnerTestimonialViewSet, basename="owner-testimonial")
owner_router.register("customers", OwnerCustomerViewSet, basename="owner-customer")
owner_router.register("activities", OwnerActivityViewSet, basename="owner-activity")

urlpatterns = [
    path("", include(public_router.urls)),
    path("me/purchases/", MyPurchasesView.as_view(), name="my-purchases"),
    path("me/purchases/<int:pk>/", MyPurchaseDetailView.as_view(), name="my-purchase-detail"),
    path("me/purchases/<int:pk>/payment-attempts/", PaymentAttemptView.as_view(), name="payment-attempt"),
    path("payments/relworx/webhook/", RelworxWebhookView.as_view(), name="relworx-webhook"),
    path("owner/dashboard/", OwnerDashboardView.as_view(), name="owner-dashboard"),
    path("owner/", include(owner_router.urls)),
]
