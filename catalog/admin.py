from django.contrib import admin

from .models import Package, Payment, PaymentAlert, PaymentEvent, Prediction, Purchase, RecentWin, Testimonial


@admin.register(Package)
class PackageAdmin(admin.ModelAdmin):
    list_display = ["name", "package_type", "price", "closes_at", "is_active", "is_open", "win_probability", "code"]
    list_filter = ["is_active", "package_type", "closes_at"]
    search_fields = ["name", "code", "betslip_link"]


@admin.register(Purchase)
class PurchaseAdmin(admin.ModelAdmin):
    list_display = ["user", "package", "price_snapshot", "currency_snapshot", "paid_at", "initiated_at"]
    list_filter = ["currency_snapshot", "paid_at", "initiated_at"]
    search_fields = ["user__phone", "package__name", "code_snapshot"]
    readonly_fields = ["initiated_at", "updated_at"]


admin.site.register([Prediction, Testimonial])


@admin.register(RecentWin)
class RecentWinAdmin(admin.ModelAdmin):
    list_display = ["caption", "settled_at", "is_published", "created_at"]
    list_editable = ["is_published"]
    search_fields = ["caption"]


@admin.register(Payment)
class PaymentAdmin(admin.ModelAdmin):
    list_display = ["reference", "user", "package", "amount", "currency", "status", "failure_category", "confirmed_at", "created_at"]
    list_filter = ["status", "failure_category", "provider", "currency", "created_at"]
    search_fields = ["reference", "internal_reference", "provider_transaction_id", "receipt_number", "user__phone", "package__name"]
    readonly_fields = [
        "purchase", "user", "package", "amount", "currency", "status", "reference",
        "internal_reference", "provider", "provider_transaction_id", "provider_charge",
        "payer_msisdn", "provider_message", "provider_payload", "failure_category",
        "failure_code", "failure_reason", "failed_at", "submitted_at", "confirmed_at",
        "last_checked_at", "check_attempts", "reconciliation_next_at", "receipt_number",
        "receipt_issued_at", "paid_at", "created_at", "updated_at",
    ]


@admin.register(PaymentEvent)
class PaymentEventAdmin(admin.ModelAdmin):
    list_display = ["payment", "event_type", "source", "status", "created_at"]
    list_filter = ["event_type", "source", "status"]
    search_fields = ["payment__reference", "message"]
    readonly_fields = ["payment", "event_type", "source", "status", "message", "payload", "created_at"]


@admin.register(PaymentAlert)
class PaymentAlertAdmin(admin.ModelAdmin):
    list_display = ["title", "payment", "severity", "resolved_at", "created_at"]
    list_filter = ["severity", "resolved_at"]
    search_fields = ["alert_key", "title", "message", "payment__reference"]
    readonly_fields = ["payment", "alert_key", "severity", "title", "message", "created_at"]
