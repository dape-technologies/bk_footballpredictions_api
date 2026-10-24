from django.contrib.auth import login, logout
from django.middleware.csrf import get_token
from django.views.decorators.csrf import csrf_protect, ensure_csrf_cookie
from rest_framework import serializers, status
from rest_framework.decorators import api_view, permission_classes, throttle_classes
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response

from .models import ActivityLog, User
from .login_security import issue_login_code, verify_login_code
from .serializers import LoginSerializer, RegisterSerializer, UserSerializer
from .services import record_activity
from .throttles import LoginRateThrottle, RegistrationRateThrottle


def challenge_response(payload):
    response = Response(payload, status=status.HTTP_202_ACCEPTED)
    response["Cache-Control"] = "no-store, private"
    return response


@ensure_csrf_cookie
@api_view(["GET"])
@permission_classes([AllowAny])
def csrf_token(request):
    return Response({"csrfToken": get_token(request)})


@api_view(["POST"])
@permission_classes([AllowAny])
@throttle_classes([RegistrationRateThrottle])
@csrf_protect
def register_view(request):
    serializer = RegisterSerializer(data=request.data)
    serializer.is_valid(raise_exception=True)
    user = serializer.save()
    login(request, user)
    record_activity(
        request,
        category=ActivityLog.Category.ACCOUNT,
        action="account.registered",
        description=f"{user.full_name} created an account.",
        actor=user,
        target=user,
    )
    return Response(UserSerializer(user).data, status=status.HTTP_201_CREATED)


@api_view(["POST"])
@permission_classes([AllowAny])
@throttle_classes([LoginRateThrottle])
@csrf_protect
def login_view(request):
    if request.data.get("challenge_token") or request.data.get("otp"):
        user = verify_login_code(request)
        login(request, user)
        record_activity(request, category=ActivityLog.Category.AUTHENTICATION,
            action="auth.login_succeeded", description=f"{user.full_name} completed verified sign-in.", actor=user, target=user)
        return Response(UserSerializer(user).data)
    serializer = LoginSerializer(data=request.data)
    if not serializer.is_valid():
        phone = User.normalize_phone(request.data.get("phone"))
        record_activity(
            request,
            category=ActivityLog.Category.AUTHENTICATION,
            action="auth.login_failed",
            description="A sign-in attempt failed.",
            actor=User.objects.filter(phone=phone).first(),
        )
        raise serializers.ValidationError(serializer.errors)
    user = serializer.validated_data["user"]
    if user.is_product_owner or user.is_developer:
        challenge = issue_login_code(request, user)
        record_activity(request, category=ActivityLog.Category.AUTHENTICATION,
            action="auth.login_otp_sent", description="A privileged login verification code was issued.", actor=user, target=user)
        return challenge_response(challenge)
    login(request, user)
    record_activity(
        request,
        category=ActivityLog.Category.AUTHENTICATION,
        action="auth.login_succeeded",
        description=f"{user.full_name} signed in.",
        actor=user,
        target=user,
    )
    return Response(UserSerializer(user).data)


@api_view(["POST"])
@permission_classes([AllowAny])
@throttle_classes([LoginRateThrottle])
@csrf_protect
def admin_login_view(request):
    if request.data.get("challenge_token") or request.data.get("otp"):
        user = verify_login_code(request)
        login(request, user)
        record_activity(request, category=ActivityLog.Category.AUTHENTICATION,
            action="auth.admin_login_succeeded", description=f"{user.full_name} completed verified administrator sign-in.", actor=user, target=user)
        return Response(UserSerializer(user).data)
    serializer = LoginSerializer(data=request.data)
    if not serializer.is_valid():
        phone = User.normalize_phone(request.data.get("phone"))
        record_activity(
            request,
            category=ActivityLog.Category.AUTHENTICATION,
            action="auth.admin_login_failed",
            description="An administrator sign-in attempt failed.",
            actor=User.objects.filter(phone=phone).first(),
        )
        raise serializers.ValidationError(serializer.errors)

    user = serializer.validated_data["user"]
    if not (user.is_product_owner or user.is_developer):
        record_activity(
            request,
            category=ActivityLog.Category.AUTHENTICATION,
            action="auth.admin_login_denied",
            description="A non-administrator account attempted to access the admin dashboard.",
            actor=user,
            target=user,
        )
        raise serializers.ValidationError("Owner or developer access is required.")

    challenge = issue_login_code(request, user)
    record_activity(
        request,
        category=ActivityLog.Category.AUTHENTICATION,
        action="auth.admin_login_otp_sent",
        description="An administrator login verification code was issued.",
        actor=user,
        target=user,
    )
    return challenge_response(challenge)


@api_view(["POST"])
@permission_classes([IsAuthenticated])
def logout_view(request):
    user = request.user
    record_activity(
        request,
        category=ActivityLog.Category.AUTHENTICATION,
        action="auth.logged_out",
        description=f"{user.full_name} signed out.",
        actor=user,
        target=user,
    )
    logout(request)
    return Response(status=status.HTTP_204_NO_CONTENT)


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def me_view(request):
    return Response(UserSerializer(request.user).data)
