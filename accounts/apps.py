from django.apps import AppConfig


class AccountsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "accounts"

    def ready(self):
        from django.contrib.auth.signals import user_logged_in, user_logged_out
        from .security_services import login_security, logout_security
        user_logged_in.connect(login_security, dispatch_uid="bk-privileged-login-security")
        user_logged_out.connect(logout_security, dispatch_uid="bk-privileged-logout-security")

