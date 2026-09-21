from django.contrib import admin
from django.contrib.auth.admin import UserAdmin

from .models import ActivityLog, User


@admin.register(User)
class CustomUserAdmin(UserAdmin):
    ordering = ["-created_at"]
    list_display = ["phone", "first_name", "last_name", "date_of_birth", "is_staff", "is_blocked", "created_at"]
    search_fields = ["phone", "first_name", "last_name"]
    fieldsets = (
        (None, {"fields": ("phone", "password")}),
        ("Profile", {"fields": ("first_name", "last_name", "date_of_birth", "is_blocked")}),
        ("Permissions", {"fields": ("is_active", "is_staff", "is_superuser", "groups", "user_permissions")}),
        ("Dates", {"fields": ("last_login", "date_joined")}),
    )
    add_fieldsets = (
        (None, {"classes": ("wide",), "fields": ("phone", "first_name", "last_name", "date_of_birth", "password1", "password2", "is_staff")}),
    )


@admin.register(ActivityLog)
class ActivityLogAdmin(admin.ModelAdmin):
    list_display = ["created_at", "category", "action", "actor", "description", "ip_address"]
    list_filter = ["category", "action", "created_at"]
    search_fields = ["description", "actor__first_name", "actor__last_name", "actor__phone", "target_id"]
    readonly_fields = [
        "actor", "category", "action", "description", "target_type", "target_id",
        "metadata", "ip_address", "created_at",
    ]

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
