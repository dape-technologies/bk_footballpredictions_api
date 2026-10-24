import secrets
from datetime import timedelta

from django.conf import settings
from django.contrib.auth.hashers import check_password, make_password
from django.core.mail import send_mail
from django.db import transaction
from django.utils import timezone
from rest_framework.exceptions import ValidationError

from .models import ActivityLog, SecurityChallenge, User
from .otp_cipher import encrypt_otp
from .payout_security import digest, recipients


def issue_login_code(request, user):
    if not request.session.session_key:
        request.session.save()
    code = str(secrets.randbelow(1000000)).zfill(6)
    challenge_token = secrets.token_urlsafe(32)
    expires_at = timezone.now() + timedelta(minutes=5)
    with transaction.atomic():
        User.objects.select_for_update().get(pk=user.pk)
        if SecurityChallenge.objects.filter(
            user=user, purpose="login", created_at__gt=timezone.now() - timedelta(seconds=60)
        ).exists():
            raise ValidationError({"detail": "Wait one minute before requesting another login code."})
        SecurityChallenge.objects.filter(user=user, purpose="login", consumed_at=None).update(consumed_at=timezone.now())
        challenge = SecurityChallenge.objects.create(
            user=user,
            purpose="login",
            session_digest=digest(request.session.session_key),
            binding=digest(challenge_token),
            code_hash=make_password(code),
            code_ciphertext=encrypt_otp(code),
            expires_at=expires_at,
        )
    try:
        send_mail(
            "BK login verification",
            f"Your BK login verification code is {code}. It expires in 5 minutes. Do not share this code.",
            settings.DEFAULT_FROM_EMAIL,
            recipients(user),
            fail_silently=False,
        )
    except Exception:
        SecurityChallenge.objects.filter(pk=challenge.pk).update(consumed_at=timezone.now())
        raise ValidationError({"detail": "Verification email could not be sent. No login session was created."})
    return {
        "otp_required": True,
        "challenge_token": challenge_token,
        "expires_at": expires_at,
        "message": "A six-digit verification code was sent to the security email account.",
    }


def verify_login_code(request):
    token = str(request.data.get("challenge_token", "")).strip()
    code = str(request.data.get("otp", "")).strip()
    if not token or not code:
        raise ValidationError({"otp": "Enter the six-digit login verification code."})
    if not request.session.session_key:
        raise ValidationError({"otp": "This login challenge is no longer valid. Start again."})

    valid = False
    user = None
    with transaction.atomic():
        challenge = SecurityChallenge.objects.select_for_update().select_related("user").filter(
            purpose="login",
            binding=digest(token),
            session_digest=digest(request.session.session_key),
            consumed_at=None,
        ).order_by("-created_at").first()
        if challenge and challenge.expires_at > timezone.now() and challenge.attempts < 5:
            challenge.attempts += 1
            valid = check_password(code, challenge.code_hash)
            challenge.save(update_fields=["attempts"])
            if valid:
                challenge.consumed_at = timezone.now()
                challenge.save(update_fields=["consumed_at"])
                user = challenge.user
    if not valid or not user or not user.is_active or user.is_blocked or not (user.is_product_owner or user.is_developer):
        if challenge:
            from .services import record_activity
            record_activity(request, category=ActivityLog.Category.AUTHENTICATION,
                action="auth.login_otp_failed", description="A privileged login verification attempt failed.",
                actor=challenge.user, target=challenge.user)
        raise ValidationError({"otp": "Invalid or expired verification code. Start again if the code has expired."})
    return user
