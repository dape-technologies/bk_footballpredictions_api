from django.contrib.auth import logout
from django.utils import timezone


class PrivilegedSessionTimeoutMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if request.user.is_authenticated and (request.user.is_product_owner or request.user.is_developer):
            from .security_services import register_session
            row = register_session(request, request.user)
            now = timezone.now().timestamp()
            started = row.started_at.timestamp()
            seen = row.last_seen_at.timestamp()
            if row.revoked_at or now - started > 8 * 3600 or now - seen > 20 * 60:
                logout(request)
            else:
                if now - seen >= 30:
                    row.last_seen_at = timezone.now()
                    row.save(update_fields=["last_seen_at"])
        return self.get_response(request)
