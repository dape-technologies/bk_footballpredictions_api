from django.urls import path

from .views import admin_login_view, csrf_token, login_view, logout_view, me_view, register_view
from .security_views import security_view
from .recovery_views import (
    password_recovery_complete_view,
    password_recovery_start_view,
    recovery_profile_view,
    recovery_questions_view,
)

urlpatterns = [
    path("auth/security/", security_view, name="privileged-security"),
    path("auth/csrf/", csrf_token, name="csrf-token"),
    path("auth/register/", register_view, name="register"),
    path("auth/recovery/questions/", recovery_questions_view, name="recovery-questions"),
    path("auth/recovery-profile/", recovery_profile_view, name="recovery-profile"),
    path("auth/password-recovery/start/", password_recovery_start_view, name="password-recovery-start"),
    path("auth/password-recovery/complete/", password_recovery_complete_view, name="password-recovery-complete"),
    path("auth/login/", login_view, name="login"),
    path("auth/admin-login/", admin_login_view, name="admin-login"),
    path("auth/logout/", logout_view, name="logout"),
    path("auth/me/", me_view, name="me"),
]
