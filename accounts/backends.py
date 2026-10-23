from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.auth.backends import ModelBackend


class PhoneOrAdminAliasBackend(ModelBackend):
    """Authenticate customers by phone and the seeded administrator by alias."""

    def authenticate(self, request, username=None, password=None, **kwargs):
        user_model = get_user_model()
        identifier = username
        if identifier is None:
            identifier = kwargs.get(user_model.USERNAME_FIELD)
        if identifier is None or password is None:
            return None

        identifier = str(identifier).strip()
        alias = settings.ADMIN_LOGIN_USERNAME
        if alias and identifier.casefold() == alias.casefold():
            identifier = settings.ADMIN_SEED_PHONE

        phone = user_model.normalize_phone(identifier)
        if not phone:
            return None
        return super().authenticate(request, username=phone, password=password)
