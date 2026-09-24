from django.contrib import admin

from .models import Package, Payment, Prediction, Purchase, RecentWin, Testimonial


@admin.register(Package)
class PackageAdmin(admin.ModelAdmin):
    list_display = ["name", "package_type", "price", "closes_at", "is_open", "win_probability", "code"]
    list_filter = ["package_type", "closes_at"]
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
    list_display = ["reference", "user", "package", "amount", "currency", "status", "paid_at", "created_at"]
    list_filter = ["status", "currency", "created_at"]
    search_fields = ["reference", "internal_reference", "user__phone", "package__name"]
    readonly_fields = [
        "purchase", "user", "package", "amount", "currency", "status", "reference",
        "internal_reference", "provider", "payer_msisdn", "provider_message",
        "provider_payload", "paid_at", "created_at", "updated_at",
    ]
