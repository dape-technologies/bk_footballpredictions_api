from django.urls import include, path
from rest_framework.routers import DefaultRouter

from .views import (
    MyPurchaseDetailView,
    MyPurchasesView,
    OwnerActivityViewSet,
    OwnerCustomerViewSet,
    OwnerDashboardView,
    OwnerPackageViewSet,
    OwnerPaymentAlertViewSet,
    OwnerPaymentViewSet,
    OwnerPerformanceView,
    OwnerPredictionViewSet,
    OwnerRecentWinViewSet,
    OwnerTestimonialViewSet,
    PaymentAttemptView,
    PaymentReceiptView,
    PaymentRefreshView,
    PublicPerformanceView,
    PublicPackageViewSet,
    PublicPredictionViewSet,
    PublicRecentWinViewSet,
    PublicTestimonialViewSet,
    RelworxWebhookView,
    OwnerWalletView,
    OwnerPayoutRequestView,
    OwnerPayoutCancelView,
    DeveloperWalletView,
    DeveloperPayoutRequestView,
    DeveloperPayoutCancelView,
)

public_router = DefaultRouter()
public_router.register("packages", PublicPackageViewSet, basename="package")
public_router.register("predictions", PublicPredictionViewSet, basename="prediction")
public_router.register("recent-wins", PublicRecentWinViewSet, basename="recent-win")
public_router.register("testimonials", PublicTestimonialViewSet, basename="testimonial")

owner_router = DefaultRouter()
owner_router.register("packages", OwnerPackageViewSet, basename="owner-package")
owner_router.register("payments", OwnerPaymentViewSet, basename="owner-payment")
owner_router.register("payment-alerts", OwnerPaymentAlertViewSet, basename="owner-payment-alert")
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
    path("me/payments/<int:pk>/refresh/", PaymentRefreshView.as_view(), name="payment-refresh"),
    path("me/payments/<int:pk>/receipt/", PaymentReceiptView.as_view(), name="payment-receipt"),
    path("payments/relworx/webhook/", RelworxWebhookView.as_view(), name="relworx-webhook"),
    path("performance/", PublicPerformanceView.as_view(), name="public-performance"),
    path("owner/dashboard/", OwnerDashboardView.as_view(), name="owner-dashboard"),
    path("owner/performance/", OwnerPerformanceView.as_view(), name="owner-performance"),
    path("owner/wallet/", OwnerWalletView.as_view(), name="owner-wallet"),
    path("owner/payout-requests/", OwnerPayoutRequestView.as_view(), name="owner-payout-request"),
    path("owner/payout-requests/<int:pk>/cancel/", OwnerPayoutCancelView.as_view(), name="owner-payout-cancel"),
    path("developer/wallet/", DeveloperWalletView.as_view(), name="developer-wallet"),
    path("developer/payout-requests/", DeveloperPayoutRequestView.as_view(), name="developer-payout-request"),
    path("developer/payout-requests/<int:pk>/cancel/", DeveloperPayoutCancelView.as_view(), name="developer-payout-cancel"),
    path("owner/", include(owner_router.urls)),
]
