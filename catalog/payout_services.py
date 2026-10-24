from datetime import timedelta
from decimal import Decimal, InvalidOperation

from django.db import transaction
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from .models import PayoutRequest, WalletLedgerEntry
from .relworx import RelworxError, check_mobile_money_payout, request_mobile_money_payout


def _decimal(value):
    try:
        return Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None


def settle_payout(payout_id, provider_status, payload=None):
    payload = payload or {}
    with transaction.atomic():
        payout = PayoutRequest.objects.select_for_update().select_related("wallet_account").get(pk=payout_id)
        if payout.status in [PayoutRequest.Status.COMPLETED, PayoutRequest.Status.FAILED, PayoutRequest.Status.CANCELLED]:
            return payout
        internal = str(payload.get("internal_reference") or payout.provider_request_id or "")[:200]
        transaction_id = str(payload.get("provider_transaction_id") or payout.transaction_id or "")[:200]
        fee = _decimal(payload.get("charge"))
        completed = parse_datetime(str(payload.get("completed_at") or "")) or timezone.now()
        if provider_status == "success":
            reservation = WalletLedgerEntry.objects.filter(idempotency_key=f"payout:{payout.pk}:reserve").first()
            if reservation and reservation.entry_type != WalletLedgerEntry.Type.PAYOUT_DEBIT:
                reservation.entry_type = WalletLedgerEntry.Type.PAYOUT_DEBIT
                reservation.metadata = {**reservation.metadata, "provider_request_id": internal, "transaction_id": transaction_id}
                reservation.save(update_fields=["entry_type", "metadata"])
            if fee and fee > 0:
                WalletLedgerEntry.objects.get_or_create(
                    idempotency_key=f"payout:{payout.pk}:fee",
                    defaults={"wallet_account": payout.wallet_account, "payout_request": payout,
                              "entry_type": WalletLedgerEntry.Type.PAYOUT_FEE, "amount": -fee,
                              "metadata": {"reference": payout.reference}},
                )
            payout.status = PayoutRequest.Status.COMPLETED
            payout.completed_at = completed
            payout.provider_fee_actual = fee
            payout.failure_reason = ""
        else:
            WalletLedgerEntry.objects.get_or_create(
                idempotency_key=f"payout:{payout.pk}:release",
                defaults={"wallet_account": payout.wallet_account, "payout_request": payout,
                          "entry_type": WalletLedgerEntry.Type.PAYOUT_RELEASE, "amount": payout.amount,
                          "metadata": {"reference": payout.reference}},
            )
            payout.status = PayoutRequest.Status.FAILED
            payout.completed_at = completed
            payout.failure_reason = str(payload.get("message") or "Relworx payout failed.")[:1000]
        payout.provider_request_id = internal
        payout.transaction_id = transaction_id
        payout.reconciliation_next_at = None
        payout.save()
        return payout


def execute_payout(payout_id):
    claimed = PayoutRequest.objects.filter(pk=payout_id, status=PayoutRequest.Status.PENDING).update(
        status=PayoutRequest.Status.PROCESSING, failure_reason="", reconciliation_next_at=timezone.now() + timedelta(minutes=2),
    )
    payout = PayoutRequest.objects.get(pk=payout_id)
    if not claimed:
        return payout
    try:
        result = request_mobile_money_payout(payout)
    except RelworxError as error:
        if error.uncertain:
            payout.failure_reason = str(error)
            payout.reconciliation_next_at = timezone.now() + timedelta(minutes=2)
            payout.save(update_fields=["failure_reason", "reconciliation_next_at", "updated_at"])
            return payout
        return settle_payout(payout.pk, "failed", {"message": str(error)})
    payout.provider_request_id = str(result.get("internal_reference") or "")[:200]
    payout.failure_reason = ""
    payout.save(update_fields=["provider_request_id", "failure_reason", "updated_at"])
    return payout


def reconcile_payout(payout_id):
    payout = PayoutRequest.objects.get(pk=payout_id)
    if payout.status != PayoutRequest.Status.PROCESSING:
        return payout, "skipped"
    try:
        result = check_mobile_money_payout(payout)
    except RelworxError as error:
        payout.check_attempts += 1
        payout.failure_reason = str(error)
        payout.reconciliation_next_at = timezone.now() + timedelta(minutes=min(30, 2 ** min(payout.check_attempts, 4)))
        payout.save(update_fields=["check_attempts", "failure_reason", "reconciliation_next_at", "updated_at"])
        return payout, "error"
    payout.check_attempts += 1
    provider_status = str(result.get("status") or result.get("request_status") or "").lower()
    reference = str(result.get("customer_reference") or "")
    amount = _decimal(result.get("amount"))
    currency = str(result.get("currency") or "").upper()
    if reference and reference != payout.reference:
        return payout, "mismatch"
    if amount is not None and amount != payout.amount:
        return payout, "mismatch"
    if currency and currency != payout.currency.upper():
        return payout, "mismatch"
    if provider_status == "success":
        return settle_payout(payout.pk, "success", result), "updated"
    if provider_status in ["failed", "failure", "cancelled", "canceled", "declined"]:
        return settle_payout(payout.pk, "failed", result), "updated"
    payout.reconciliation_next_at = timezone.now() + timedelta(minutes=2)
    payout.save(update_fields=["check_attempts", "reconciliation_next_at", "updated_at"])
    return payout, "pending"
