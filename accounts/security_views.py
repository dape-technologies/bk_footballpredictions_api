import secrets

from django.contrib.auth import update_session_auth_hash
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import transaction
from django.utils import timezone
from rest_framework.decorators import api_view, permission_classes, throttle_classes
from rest_framework.exceptions import ValidationError
from rest_framework.permissions import BasePermission
from rest_framework.response import Response

from .models import ActivityLog, PrivilegedSession, RecoveryCode, User
from .payout_security import digest, issue_code, verify_code, recipients
from .services import record_activity
from .throttles import PayoutRateThrottle


class IsPrivileged(BasePermission):
    def has_permission(self, request, view):
        return request.user.is_authenticated and request.user.is_active and (request.user.is_product_owner or request.user.is_developer)


@api_view(["GET", "POST"])
@permission_classes([IsPrivileged])
@throttle_classes([PayoutRateThrottle])
def security_view(request):
    if request.method == "GET":
        sessions = PrivilegedSession.objects.filter(user=request.user).order_by("-started_at")[:30]
        return Response({
            "sessions": [{"id": row.pk, "device": row.device, "ip_address": row.ip_address,
                "started_at": row.started_at, "last_seen_at": row.last_seen_at, "revoked_at": row.revoked_at,
                "current": row.session_digest == digest(request.session.session_key),
                "active": row.revoked_at is None and (timezone.now() - row.last_seen_at).total_seconds() < 1200
                    and (timezone.now() - row.started_at).total_seconds() < 28800} for row in sessions],
            "recovery_codes_remaining": RecoveryCode.objects.filter(user=request.user, used_at=None).count(),
            "notification_emails": recipients(request.user),
        })
    action = request.data.get("action")
    if action == "revoke_session":
        row = PrivilegedSession.objects.filter(pk=request.data.get("session_id"), user=request.user).first()
        if not row:
            return Response({"detail": "Session not found."}, status=404)
        row.revoked_at = timezone.now()
        row.save(update_fields=["revoked_at"])
        record_activity(request, category=ActivityLog.Category.AUTHENTICATION, action="security.session_revoked", description="A privileged session was revoked.")
        return Response({"message": "Session revoked."})
    if action not in ["recovery_codes", "password", "destination"]:
        raise ValidationError({"action": "Choose recovery_codes, password, destination or revoke_session."})
    if request.data.get("request_otp") is True:
        issue_code(request, action)
        return Response({"message": "Verification code emailed to your security contacts."})
    if action == "password":
        try:
            validate_password(str(request.data.get("new_password", "")), request.user)
        except DjangoValidationError as error:
            raise ValidationError({"new_password": error.messages})
        if len(str(request.data.get("new_password", ""))) < 12:
            raise ValidationError({"new_password": "Privileged passwords must have at least 12 characters."})
    verify_code(request, action)
    if action == "recovery_codes":
        codes = [secrets.token_hex(12) for _ in range(8)]
        with transaction.atomic():
            User.objects.select_for_update().get(pk=request.user.pk)
            RecoveryCode.objects.filter(user=request.user).delete()
            RecoveryCode.objects.bulk_create([RecoveryCode(user=request.user, digest=digest(code)) for code in codes])
        result = {"message": "Save these codes securely. Each can be used once with your password.", "codes": codes}
    elif action == "password":
        request.user.set_password(request.data["new_password"])
        request.user.save(update_fields=["password"])
        PrivilegedSession.objects.filter(user=request.user).update(revoked_at=timezone.now())
        update_session_auth_hash(request, request.user)
        from .security_services import register_session
        register_session(request, request.user)
        result = {"message": "Password changed. Other sessions were revoked."}
    else:
        from catalog.wallet_services import wallet_account
        from catalog.serializers import normalize_ugandan_msisdn
        account = wallet_account("owner" if request.user.is_product_owner else "developer")
        account.destination_msisdn = normalize_ugandan_msisdn(request.data.get("destination"))
        account.save(update_fields=["destination_msisdn"])
        result = {"message": "Payout destination updated after verification."}
    record_activity(request, category=ActivityLog.Category.AUTHENTICATION, action=f"security.{action}", description=f"Verified security change: {action}.")
    from .security_services import queue_notice
    queue_notice(f"security-change:{action}:{secrets.token_hex(12)}", recipients(request.user), "BK security settings changed", f"Your BK {action.replace('_', ' ')} settings were changed after verification. Contact your developer if this was unexpected.")
    return Response(result)
