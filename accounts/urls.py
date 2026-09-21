from django.urls import path

from .views import admin_login_view, csrf_token, login_view, logout_view, me_view, register_view

urlpatterns = [
    path("auth/csrf/", csrf_token, name="csrf-token"),
    path("auth/register/", register_view, name="register"),
    path("auth/login/", login_view, name="login"),
    path("auth/admin-login/", admin_login_view, name="admin-login"),
    path("auth/logout/", logout_view, name="logout"),
    path("auth/me/", me_view, name="me"),
]
