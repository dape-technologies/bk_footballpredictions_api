from django.urls import include, path
from rest_framework.routers import DefaultRouter
from .operations_views import TelemetryView, OperationsReportView, BetslipAccessView, CustomerPreferencesView

from .views import (
    MyPurchaseDetailView,
    MyPurchasePaidImageView,
    MyPurchasesView,
    OwnerActivityViewSet,
    OwnerCustomerViewSet,
    OwnerDashboardView,
    OwnerFreePickViewSet,
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
    PublicFreePickView,
    PublicFreePickImageView,
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
    DeveloperAnalyticsView,
    DeveloperCustomerViewSet,
    DeveloperPaymentGatewayView,
    DeveloperPaymentGatewayCheckView,
)

public_router = DefaultRouter()
public_router.register("packages", PublicPackageViewSet, basename="package")
public_router.register("predictions", PublicPredictionViewSet, basename="prediction")
public_router.register("recent-wins", PublicRecentWinViewSet, basename="recent-win")
public_router.register("testimonials", PublicTestimonialViewSet, basename="testimonial")

owner_router = DefaultRouter()
owner_router.register("packages", OwnerPackageViewSet, basename="owner-package")
owner_router.register("free-picks", OwnerFreePickViewSet, basename="owner-free-pick")
owner_router.register("payments", OwnerPaymentViewSet, basename="owner-payment")
owner_router.register("payment-alerts", OwnerPaymentAlertViewSet, basename="owner-payment-alert")
owner_router.register("predictions", OwnerPredictionViewSet, basename="owner-prediction")
owner_router.register("recent-wins", OwnerRecentWinViewSet, basename="owner-recent-win")
owner_router.register("testimonials", OwnerTestimonialViewSet, basename="owner-testimonial")
owner_router.register("customers", OwnerCustomerViewSet, basename="owner-customer")
owner_router.register("activities", OwnerActivityViewSet, basename="owner-activity")
developer_router = DefaultRouter()
developer_router.register("users", DeveloperCustomerViewSet, basename="developer-user")

urlpatterns = [
    path("telemetry/", TelemetryView.as_view()),
    path("operations/report/", OperationsReportView.as_view()),
    path("me/preferences/", CustomerPreferencesView.as_view()),
    path("me/purchases/<int:pk>/access/", BetslipAccessView.as_view()),
    path("", include(public_router.urls)),
    path("me/purchases/", MyPurchasesView.as_view(), name="my-purchases"),
    path("me/purchases/<int:pk>/", MyPurchaseDetailView.as_view(), name="my-purchase-detail"),
    path("me/purchases/<int:pk>/paid-image/", MyPurchasePaidImageView.as_view(), name="my-purchase-paid-image"),
    path("me/purchases/<int:pk>/payment-attempts/", PaymentAttemptView.as_view(), name="payment-attempt"),
    path("me/payments/<int:pk>/refresh/", PaymentRefreshView.as_view(), name="payment-refresh"),
    path("me/payments/<int:pk>/receipt/", PaymentReceiptView.as_view(), name="payment-receipt"),
    path("payments/relworx/webhook/", RelworxWebhookView.as_view(), name="relworx-webhook"),
    path("performance/", PublicPerformanceView.as_view(), name="public-performance"),
    path("free-pick/", PublicFreePickView.as_view(), name="public-free-pick"),
    path("free-pick/image/", PublicFreePickImageView.as_view(), name="public-free-pick-image"),
    path("owner/dashboard/", OwnerDashboardView.as_view(), name="owner-dashboard"),
    path("owner/performance/", OwnerPerformanceView.as_view(), name="owner-performance"),
    path("owner/wallet/", OwnerWalletView.as_view(), name="owner-wallet"),
    path("owner/payout-requests/", OwnerPayoutRequestView.as_view(), name="owner-payout-request"),
    path("owner/payout-requests/<int:pk>/cancel/", OwnerPayoutCancelView.as_view(), name="owner-payout-cancel"),
    path("developer/wallet/", DeveloperWalletView.as_view(), name="developer-wallet"),
    path("developer/payout-requests/", DeveloperPayoutRequestView.as_view(), name="developer-payout-request"),
    path("developer/payout-requests/<int:pk>/cancel/", DeveloperPayoutCancelView.as_view(), name="developer-payout-cancel"),
    path("developer/analytics/", DeveloperAnalyticsView.as_view(), name="developer-analytics"),
    path("developer/payment-gateway/", DeveloperPaymentGatewayView.as_view(), name="developer-payment-gateway"),
    path("developer/payment-gateway/relworx/check/", DeveloperPaymentGatewayCheckView.as_view(), name="developer-payment-gateway-check"),
    path("owner/", include(owner_router.urls)),
    path("developer/", include(developer_router.urls)),
]
