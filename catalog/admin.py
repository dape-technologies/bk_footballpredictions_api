from django.contrib import admin

from .models import Package, Payment, Prediction, RecentWin, Subscription, Testimonial


@admin.register(Package)
class PackageAdmin(admin.ModelAdmin):
    list_display = [
        "name", "package_type", "price", "is_active", "commences_at",
        "win_probability", "code",
    ]
    list_editable = ["is_active"]
    list_filter = ["is_active", "package_type"]
    search_fields = ["name", "code", "betslip_link"]


admin.site.register([Prediction, Subscription, Testimonial])


@admin.register(RecentWin)
class RecentWinAdmin(admin.ModelAdmin):
    list_display = ["caption", "settled_at", "is_published", "created_at"]
    list_editable = ["is_published"]
    search_fields = ["caption"]


@admin.register(Payment)
class PaymentAdmin(admin.ModelAdmin):
    list_display = ["reference", "user", "package", "amount", "currency", "status", "paid_at", "created_at"]
    list_filter = ["status", "currency", "created_at"]
    search_fields = ["reference", "user__phone", "user__first_name", "user__last_name", "package__name"]
    readonly_fields = ["reference", "created_at", "updated_at"]
