import logging

from django.core.mail import send_mail
from django.conf import settings
from django.db import transaction
from django.utils import timezone

from .models import EmailNotice, PrivilegedSession
from .payout_security import digest, recipients

logger = logging.getLogger(__name__)


def deliver_notice(pk):
    with transaction.atomic():
        notice = EmailNotice.objects.select_for_update().get(pk=pk)
        if notice.sent_at or notice.attempts >= 10:
            return
        notice.attempts += 1
        try:
            send_mail(notice.subject, notice.body, settings.DEFAULT_FROM_EMAIL, notice.recipients, fail_silently=False)
            notice.sent_at = timezone.now()
        except Exception:
            logger.warning("Security notification delivery failed; queued for retry (notice %s).", pk)
        notice.save(update_fields=["attempts", "sent_at"])


def queue_notice(key, addresses, subject, body):
    notice, created = EmailNotice.objects.get_or_create(dedup_key=key, defaults={"recipients": addresses, "subject": subject, "body": body})
    if created:
        transaction.on_commit(lambda: deliver_notice(notice.pk))


def register_session(request, user):
    if not request.session.session_key:
        request.session.save()
    # REMOTE_ADDR is retained as evidence; untrusted forwarded headers are not used.
    return PrivilegedSession.objects.get_or_create(session_digest=digest(request.session.session_key), defaults={
        "user": user, "device": request.META.get("HTTP_USER_AGENT", "Unknown device")[:300],
        "ip_address": request.META.get("REMOTE_ADDR") or None,
    })[0]


def login_security(sender, request, user, **kwargs):
    if not (user.is_product_owner or user.is_developer):
        return
    row = register_session(request, user)
    portal = "owner" if user.is_product_owner else "developer"
    queue_notice(
        f"login:{row.pk}",
        recipients(user),
        f"BK {portal} portal sign-in",
        (
            f"A user signed in to the BK {portal} portal.\n\n"
            f"Account: {user.full_name} ({user.phone})\n"
            f"Time: {timezone.localtime().isoformat()}\n"
            f"Device: {row.device}\n"
            f"Connection address: {row.ip_address or 'Unavailable'}\n\n"
            "Review Security in the dashboard if this was unexpected."
        ),
    )


def logout_security(sender, request, user, **kwargs):
    if request and request.session.session_key:
        PrivilegedSession.objects.filter(session_digest=digest(request.session.session_key), revoked_at=None).update(revoked_at=timezone.now())
