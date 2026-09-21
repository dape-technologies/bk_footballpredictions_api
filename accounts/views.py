from django.contrib.auth import login, logout
from django.middleware.csrf import get_token
from django.views.decorators.csrf import csrf_protect, ensure_csrf_cookie
from rest_framework import serializers, status
from rest_framework.decorators import api_view, permission_classes, throttle_classes
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response

from .models import ActivityLog, User
from .serializers import LoginSerializer, RegisterSerializer, UserSerializer
from .services import record_activity
from .throttles import LoginRateThrottle, RegistrationRateThrottle


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
