from rest_framework.permissions import BasePermission


class IsProductOwner(BasePermission):
    message = "Product owner access is required."

    def has_permission(self, request, view):
        return bool(
            request.user
            and request.user.is_authenticated
            and request.user.is_active
            and request.user.is_product_owner
        )
