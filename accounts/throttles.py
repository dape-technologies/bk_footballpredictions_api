from rest_framework.throttling import SimpleRateThrottle


class ClientIpRateThrottle(SimpleRateThrottle):
    def get_cache_key(self, request, view):
        return self.cache_format % {"scope": self.scope, "ident": self.get_ident(request)}


class RegistrationRateThrottle(ClientIpRateThrottle):
    scope = "registration"
    rate = "5/hour"


class LoginRateThrottle(ClientIpRateThrottle):
    scope = "login"
    rate = "10/minute"


class PayoutRateThrottle(SimpleRateThrottle):
    scope = "payout-security"
    rate = "10/minute"

    def get_cache_key(self, request, view):
        return self.cache_format % {"scope": self.scope, "ident": request.user.pk if request.user.is_authenticated else self.get_ident(request)}
