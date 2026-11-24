import hashlib
from datetime import timedelta

from django.contrib.auth.hashers import check_password, make_password
from django.core.cache import cache
from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import transaction
from django.db.models import Sum
from django.utils import timezone
from django.views.decorators.csrf import csrf_protect
from rest_framework import serializers, status
from rest_framework.decorators import api_view, permission_classes, throttle_classes
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response

from .models import ActivityLog, PasswordRecoveryChallenge, RecoveryProfile, User
from .recovery import RECOVERY_PROMPTS, normalize_recovery_phrase, recovery_hashes
from .recovery_serializers import RecoveryCompleteSerializer, RecoveryProfileSerializer, RecoveryStartSerializer
from .serializers import UserSerializer
from .services import client_ip, record_activity
from .throttles import PasswordRecoveryCompleteRateThrottle, PasswordRecoveryStartRateThrottle


DUMMY_RECOVERY_HASH = make_password("bk-dummy-recovery-phrase")
GENERIC_FAILURE = "We could not verify those recovery details."


def _phone_start_allowed(phone):
    digest = hashlib.sha256(phone.encode("utf-8")).hexdigest()
    key = f"password-recovery-phone:{digest}"
    if cache.add(key, 1, timeout=3600):
        return True
    try:
        return cache.incr(key) <= 3
    except ValueError:
        cache.set(key, 1, timeout=3600)
        return True


def _is_recovery_locked(user):
    since = timezone.now() - timedelta(hours=24)
    failures = PasswordRecoveryChallenge.objects.filter(user=user, created_at__gte=since).aggregate(total=Sum("attempts"))["total"] or 0
    return failures >= 10


def _dummy_verify():
    check_password("not-the-private-phrase", DUMMY_RECOVERY_HASH)
    check_password("not-the-life-phrase", DUMMY_RECOVERY_HASH)


@api_view(["GET"])
@permission_classes([AllowAny])
def recovery_questions_view(request):
    return Response({"version": 1, "prompts": RECOVERY_PROMPTS})


@api_view(["PUT"])
@permission_classes([IsAuthenticated])
@csrf_protect
def recovery_profile_view(request):
    user = request.user
    if user.is_product_owner or user.is_developer:
        return Response({"detail": "Privileged accounts use the administrator recovery process."}, status=status.HTTP_403_FORBIDDEN)

    serializer = RecoveryProfileSerializer(data=request.data, context={"request": request})
    serializer.is_valid(raise_exception=True)
    private_hash, life_hash = recovery_hashes(
        serializer.validated_data["recovery_private_phrase"],
        serializer.validated_data["recovery_life_phrase"],
    )
    RecoveryProfile.objects.update_or_create(
        user=user,
        defaults={
            "private_phrase_hash": private_hash,
            "life_phrase_hash": life_hash,
            "prompt_version": 1,
        },
    )
    PasswordRecoveryChallenge.objects.filter(user=user, consumed_at=None).update(consumed_at=timezone.now())
    record_activity(
        request,
        category=ActivityLog.Category.ACCOUNT,
        action="account.recovery_configured",
        description="Customer recovery answers were configured.",
        actor=user,
        target=user,
    )
    return Response(UserSerializer(user).data)


@api_view(["POST"])
@permission_classes([AllowAny])
@throttle_classes([PasswordRecoveryStartRateThrottle])
@csrf_protect
def password_recovery_start_view(request):
    serializer = RecoveryStartSerializer(data=request.data)
    serializer.is_valid(raise_exception=True)
    phone = User.normalize_phone(serializer.validated_data["phone"])
    if not _phone_start_allowed(phone):
        return Response({"detail": "Too many recovery requests. Please try again later."}, status=status.HTTP_429_TOO_MANY_REQUESTS)

    user = User.objects.filter(phone=phone).first()
    eligible = bool(
        user
        and user.is_active
        and not user.is_blocked
        and not user.is_product_owner
        and not user.is_developer
        and RecoveryProfile.objects.filter(user=user).exists()
        and not _is_recovery_locked(user)
    )
    _dummy_verify()
    challenge = PasswordRecoveryChallenge.objects.create(
        user=user if eligible else None,
        is_decoy=not eligible,
        ip_address=client_ip(request),
        device=request.META.get("HTTP_USER_AGENT", "")[:300],
        expires_at=timezone.now() + timedelta(minutes=10),
    )
    record_activity(
        request,
        category=ActivityLog.Category.AUTHENTICATION,
        action="auth.password_recovery_started",
        description="A password recovery attempt was started.",
        actor=user if eligible else None,
        target=user if eligible else None,
    )
    return Response(
        {"recovery_id": str(challenge.public_id), "expires_in": 600, "prompts": RECOVERY_PROMPTS},
        status=status.HTTP_202_ACCEPTED,
    )


@api_view(["POST"])
@permission_classes([AllowAny])
@throttle_classes([PasswordRecoveryCompleteRateThrottle])
@csrf_protect
def password_recovery_complete_view(request):
    serializer = RecoveryCompleteSerializer(data=request.data)
    serializer.is_valid(raise_exception=True)
    now = timezone.now()

    with transaction.atomic():
        try:
            challenge = PasswordRecoveryChallenge.objects.select_for_update().select_related("user").get(
                public_id=serializer.validated_data["recovery_id"]
            )
        except (PasswordRecoveryChallenge.DoesNotExist, DjangoValidationError, ValueError):
            _dummy_verify()
            return Response({"detail": GENERIC_FAILURE}, status=status.HTTP_400_BAD_REQUEST)

        user = challenge.user
        valid_state = bool(
            not challenge.is_decoy
            and user
            and challenge.consumed_at is None
            and challenge.expires_at > now
            and challenge.attempts < 5
            and user.is_active
            and not user.is_blocked
            and not user.is_product_owner
            and not user.is_developer
            and not _is_recovery_locked(user)
        )
        profile = RecoveryProfile.objects.select_for_update().filter(user=user).first() if valid_state else None
        private_phrase = normalize_recovery_phrase(serializer.validated_data["private_phrase"])
        life_phrase = normalize_recovery_phrase(serializer.validated_data["life_phrase"])
        private_matches = check_password(private_phrase, profile.private_phrase_hash) if profile else False
        life_matches = check_password(life_phrase, profile.life_phrase_hash) if profile else False
        answers_match = private_matches and life_matches
        if not profile:
            _dummy_verify()

        if not valid_state or not answers_match:
            if challenge.consumed_at is None:
                challenge.attempts += 1
                if challenge.attempts >= 5:
                    challenge.consumed_at = now
                challenge.save(update_fields=["attempts", "consumed_at"])
            record_activity(
                request,
                category=ActivityLog.Category.AUTHENTICATION,
                action="auth.password_recovery_failed",
                description="A password recovery verification failed.",
                actor=user if valid_state else None,
                target=user if valid_state else None,
            )
            return Response({"detail": GENERIC_FAILURE}, status=status.HTTP_400_BAD_REQUEST)

        user.set_password(serializer.validated_data["new_password"])
        user.save(update_fields=["password", "updated_at"])
        profile.delete()
        PasswordRecoveryChallenge.objects.filter(user=user, consumed_at=None).update(consumed_at=now)
        record_activity(
            request,
            category=ActivityLog.Category.AUTHENTICATION,
            action="auth.password_recovery_succeeded",
            description="A customer password was reset through account recovery.",
            actor=user,
            target=user,
        )

    return Response({"detail": "Your password was reset. Sign in and set new recovery answers."})
