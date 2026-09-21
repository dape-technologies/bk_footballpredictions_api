from rest_framework.authentication import SessionAuthentication as DRFSessionAuthentication


class SessionAuthentication(DRFSessionAuthentication):
    """Session auth that correctly advertises unauthenticated responses as 401."""

    def authenticate_header(self, request):
        return "Session"
