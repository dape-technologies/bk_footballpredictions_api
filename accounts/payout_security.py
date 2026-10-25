import secrets
from datetime import timedelta
from decimal import Decimal, InvalidOperation

from django.conf import settings
from django.contrib.auth.hashers import make_password, check_password
from django.core.mail import send_mail
from django.db import transaction
from django.utils import timezone
from django.utils.crypto import salted_hmac
from rest_framework.exceptions import ValidationError

from .models import SecurityChallenge, User
from .otp_cipher import encrypt_otp


def recipients(user):
    return list(dict.fromkeys([settings.OWNER_SECURITY_EMAIL, settings.DEVELOPER_SECURITY_EMAIL]
                if user.is_product_owner else [settings.DEVELOPER_SECURITY_EMAIL]))


def digest(value):
    return salted_hmac("bk-security", str(value), algorithm="sha256").hexdigest()


def binding_for(request, purpose):
    if purpose == "payout":
        try:
            value = Decimal(str(request.data.get("amount"))).quantize(Decimal("0.01"))
            if not value.is_finite() or value <= 0:
                raise ValueError()
        except (InvalidOperation, ValueError, TypeError):
            raise ValidationError({"amount": "Enter a valid withdrawal amount."})
        return str(value)
    if purpose in {"destination", "owner_destination"}:
        from catalog.serializers import normalize_ugandan_msisdn
        return normalize_ugandan_msisdn(request.data.get("destination", ""))
    return purpose


def issue_code(request, purpose="payout"):
    if not request.user.check_password(str(request.data.get("password", ""))):
        raise ValidationError({"password": "Incorrect password."})
    binding = binding_for(request, purpose)
    code = str(secrets.randbelow(1000000)).zfill(6)
    with transaction.atomic():
        # Serialize issuance across all sessions of this account.
        User.objects.select_for_update().get(pk=request.user.pk)
        if SecurityChallenge.objects.filter(user=request.user, created_at__gt=timezone.now() - timedelta(seconds=60)).exists():
            raise ValidationError({"detail": "Wait one minute before requesting another code."})
        SecurityChallenge.objects.filter(user=request.user, purpose=purpose, consumed_at=None).update(consumed_at=timezone.now())
        challenge = SecurityChallenge.objects.create(user=request.user, purpose=purpose,
            session_digest=digest(request.session.session_key), binding=binding,
            code_hash=make_password(code), code_ciphertext=encrypt_otp(code),
            expires_at=timezone.now() + timedelta(minutes=5))
    try:
        send_mail("BK security verification", f"Your verification code is {code}. Action: {purpose}. Details: {binding}. Expires in 5 minutes. Do not share this code.",
                  settings.DEFAULT_FROM_EMAIL, recipients(request.user), fail_silently=False)
    except Exception:
        SecurityChallenge.objects.filter(pk=challenge.pk).update(consumed_at=timezone.now())
        raise ValidationError({"detail": "Verification email could not be sent. No financial action was taken."})


def verify_code(request, purpose="payout"):
    binding = binding_for(request, purpose)
    with transaction.atomic():
        challenge = SecurityChallenge.objects.select_for_update().filter(user=request.user, purpose=purpose,
            session_digest=digest(request.session.session_key), consumed_at=None).order_by("-created_at").first()
        valid = False
        if challenge and challenge.expires_at > timezone.now() and challenge.attempts < 5:
            challenge.attempts += 1
            valid = challenge.binding == binding and check_password(str(request.data.get("otp", "")), challenge.code_hash)
            challenge.save(update_fields=["attempts"])
            if valid:
                challenge.consumed_at = timezone.now()
                challenge.save(update_fields=["consumed_at"])
    if not valid:
        raise ValidationError({"otp": "Invalid or expired verification code. Request a new code."})
