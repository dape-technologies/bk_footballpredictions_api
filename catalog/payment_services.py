from datetime import timedelta
from decimal import Decimal, InvalidOperation

from django.db import transaction
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from .models import Payment, PaymentAlert, PaymentEvent
from .relworx import RelworxError, check_mobile_money_payment
from .wallet_services import accrue_payment


SAFE_PROVIDER_FIELDS = (
    "status",
    "request_status",
    "customer_reference",
    "internal_reference",
    "currency",
    "amount",
    "provider",
    "charge",
    "provider_transaction_id",
    "completed_at",
    "message",
    "code",
)


class PaymentVerificationError(Exception):
    def __init__(self, field, message=None):
        self.field = field
        super().__init__(message or f"Payment {field} mismatch.")


def safe_provider_payload(payload):
    safe = {}
    for key in SAFE_PROVIDER_FIELDS:
        if key not in payload:
            continue
        value = payload.get(key)
        safe[key] = str(value) if isinstance(value, Decimal) else value
    return safe


def record_payment_event(payment, event_type, *, source="system", message="", payload=None, status_value=None):
    return PaymentEvent.objects.create(
        payment=payment,
        event_type=event_type,
        source=source,
        status=status_value if status_value is not None else payment.status,
        message=str(message or "")[:240],
        payload=safe_provider_payload(payload or {}),
    )


def create_payment_alert(payment, key, title, message, *, severity=PaymentAlert.Severity.WARNING):
    alert, _ = PaymentAlert.objects.get_or_create(
        alert_key=f"payment-{payment.pk}-{key}",
        defaults={
            "payment": payment,
            "severity": severity,
            "title": title,
            "message": str(message)[:500],
        },
    )
    return alert


def classify_failure(payload):
    text = " ".join(
        str(payload.get(key, "")) for key in ("status", "request_status", "message", "code")
    ).lower()
    customer_signals = ("declin", "reject", "cancel", "insufficient", "pin", "customer", "user timeout")
    if any(signal in text for signal in customer_signals):
        return Payment.FailureCategory.CUSTOMER
    if any(signal in text for signal in ("invalid", "mismatch", "validation")):
        return Payment.FailureCategory.VALIDATION
    if any(signal in text for signal in ("error", "exception", "unavailable", "timeout")):
        return Payment.FailureCategory.SYSTEM
    return Payment.FailureCategory.PROVIDER


def _decimal(value, field):
    try:
        return Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as error:
        raise PaymentVerificationError(field) from error


def _validate_identifiers(payment, payload):
    customer_reference = str(payload.get("customer_reference", "")).strip()
    if customer_reference and customer_reference != payment.reference:
        raise PaymentVerificationError("customer reference")

    internal_reference = str(payload.get("internal_reference", "")).strip()
    if payment.internal_reference and payment.internal_reference != internal_reference:
        raise PaymentVerificationError("internal reference")
    return internal_reference


def apply_relworx_result(payment, payload, *, source):
    """Apply a provider result to a locked Payment instance."""
    provider_status = str(payload.get("status") or payload.get("request_status") or "").lower()
    internal_reference = _validate_identifiers(payment, payload)
    now = timezone.now()
    updates = []

    if internal_reference and not payment.internal_reference:
        payment.internal_reference = internal_reference
        updates.append("internal_reference")
    provider = str(payload.get("provider") or "").strip()
    if provider:
        payment.provider = provider[:40]
        updates.append("provider")
    provider_transaction_id = str(payload.get("provider_transaction_id") or "").strip()
    if provider_transaction_id:
        payment.provider_transaction_id = provider_transaction_id[:120]
        updates.append("provider_transaction_id")
    if payload.get("charge") not in (None, ""):
        payment.provider_charge = _decimal(payload.get("charge"), "provider charge")
        payment.provider_net_amount = max(Decimal("0"), payment.amount - payment.provider_charge)
        updates.extend(["provider_charge", "provider_net_amount"])

    payment.last_checked_at = now
    payment.check_attempts += 1
    payment.provider_message = str(payload.get("message") or payment.provider_message)[:240]
    payment.provider_payload = safe_provider_payload(payload)
    updates.extend(["last_checked_at", "check_attempts", "provider_message", "provider_payload"])

    if provider_status == "success":
        if str(payload.get("customer_reference", "")) != payment.reference:
            raise PaymentVerificationError("customer reference")
        if _decimal(payload.get("amount"), "amount") != payment.amount:
            raise PaymentVerificationError("amount")
        if str(payload.get("currency", "")).upper() != payment.currency.upper():
            raise PaymentVerificationError("currency")
        if payment.status == Payment.Status.PAID:
            record_payment_event(payment, "provider.success_duplicate", source=source, payload=payload)
            return payment

        completed_at = parse_datetime(str(payload.get("completed_at", ""))) or now
        payment.status = Payment.Status.PAID
        payment.paid_at = completed_at
        payment.confirmed_at = completed_at
        payment.reconciliation_next_at = None
        payment.failure_category = ""
        payment.failure_code = ""
        payment.failure_reason = ""
        payment.failed_at = None
        updates.extend([
            "status", "paid_at", "confirmed_at", "reconciliation_next_at",
            "failure_category", "failure_code", "failure_reason", "failed_at",
        ])
        payment.save(update_fields=list(dict.fromkeys(updates + ["updated_at"])))
        payment.purchase.complete(completed_at)
        payment.issue_receipt()
        accrue_payment(payment)
        payment.alerts.filter(resolved_at__isnull=True).update(resolved_at=now)
        record_payment_event(payment, "payment.confirmed", source=source, payload=payload)
        return payment

    if provider_status in {"failed", "failure", "declined", "cancelled", "canceled"}:
        if payment.status == Payment.Status.PAID:
            record_payment_event(payment, "provider.failure_after_success", source=source, payload=payload)
            return payment
        category = classify_failure(payload)
        payment.status = Payment.Status.CANCELLED if provider_status in {"cancelled", "canceled"} else Payment.Status.FAILED
        payment.failure_category = category
        payment.failure_code = str(payload.get("code") or provider_status)[:80]
        payment.failure_reason = str(payload.get("message") or "Payment provider reported a failed transaction.")
        payment.failed_at = now
        payment.reconciliation_next_at = None
        updates.extend(["status", "failure_category", "failure_code", "failure_reason", "failed_at", "reconciliation_next_at"])
        payment.save(update_fields=list(dict.fromkeys(updates + ["updated_at"])))
        record_payment_event(payment, "payment.failed", source=source, payload=payload)
        if category != Payment.FailureCategory.CUSTOMER:
            create_payment_alert(
                payment,
                "provider-failure",
                "Payment provider failure",
                f"{payment.reference}: {payment.failure_reason}",
            )
        return payment

    if provider_status in {"pending", "processing", "in_progress", "submitted"}:
        if payment.status != Payment.Status.PAID:
            payment.status = Payment.Status.PENDING
            payment.reconciliation_next_at = now + timedelta(minutes=2)
            updates.extend(["status", "reconciliation_next_at"])
            payment.save(update_fields=list(dict.fromkeys(updates + ["updated_at"])))
            record_payment_event(payment, "payment.pending", source=source, payload=payload)
        return payment

    raise PaymentVerificationError("status", "Unsupported payment status.")


def record_verification_mismatch(payment, error, *, source, payload=None):
    payment.failure_category = Payment.FailureCategory.VALIDATION
    payment.failure_code = str(error.field).replace(" ", "_")[:80]
    payment.failure_reason = str(error)
    payment.last_checked_at = timezone.now()
    payment.check_attempts += 1
    payment.save(update_fields=[
        "failure_category", "failure_code", "failure_reason", "last_checked_at", "check_attempts", "updated_at",
    ])
    record_payment_event(payment, "provider.verification_failed", source=source, message=str(error), payload=payload)
    create_payment_alert(
        payment,
        f"verification-{payment.failure_code}",
        "Payment verification mismatch",
        f"{payment.reference}: {error}",
        severity=PaymentAlert.Severity.CRITICAL,
    )


def reconcile_payment(payment_id, *, max_attempts=12, max_age_hours=24, source="scheduled_reconciliation"):
    with transaction.atomic():
        payment = Payment.objects.select_for_update().select_related("purchase__package").get(pk=payment_id)
        if payment.status not in (Payment.Status.SUBMITTED, Payment.Status.PENDING):
            return payment, "skipped"
        now = timezone.now()
        if source == "scheduled_reconciliation" and payment.reconciliation_next_at and payment.reconciliation_next_at > now:
            return payment, "skipped"
        # A short lease prevents overlapping cron runs from querying the same payment.
        payment.reconciliation_next_at = now + timedelta(minutes=5)
        payment.save(update_fields=["reconciliation_next_at", "updated_at"])

    try:
        result = check_mobile_money_payment(payment)
    except RelworxError as error:
        with transaction.atomic():
            payment = Payment.objects.select_for_update().get(pk=payment_id)
            payment.last_checked_at = timezone.now()
            payment.check_attempts += 1
            payment.provider_message = str(error)[:240]
            payment.failure_category = Payment.FailureCategory.SYSTEM if error.uncertain else Payment.FailureCategory.PROVIDER
            payment.failure_code = str(error.code or error.status_code or "provider_error")[:80]
            payment.failure_reason = str(error)
            too_old = payment.created_at <= timezone.now() - timedelta(hours=max_age_hours)
            exhausted = payment.check_attempts >= max_attempts
            if too_old or exhausted:
                payment.status = Payment.Status.NEEDS_REVIEW
                payment.reconciliation_next_at = None
            else:
                delay_minutes = min(30, 2 ** min(payment.check_attempts, 4))
                payment.reconciliation_next_at = timezone.now() + timedelta(minutes=delay_minutes)
            payment.save(update_fields=[
                "last_checked_at", "check_attempts", "provider_message", "failure_category",
                "failure_code", "failure_reason", "status", "reconciliation_next_at", "updated_at",
            ])
            record_payment_event(payment, "reconciliation.error", source=source, message=str(error))
            if payment.status == Payment.Status.NEEDS_REVIEW:
                create_payment_alert(
                    payment,
                    "needs-review",
                    "Payment needs review",
                    f"{payment.reference} could not be reconciled after {payment.check_attempts} checks.",
                    severity=PaymentAlert.Severity.CRITICAL,
                )
            return payment, "error"

    with transaction.atomic():
        payment = Payment.objects.select_for_update().select_related("purchase__package").get(pk=payment_id)
        try:
            apply_relworx_result(payment, result, source=source)
        except PaymentVerificationError as error:
            record_verification_mismatch(payment, error, source=source, payload=result)
            return payment, "mismatch"
        return payment, "updated"
