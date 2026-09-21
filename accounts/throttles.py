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
