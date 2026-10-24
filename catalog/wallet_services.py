import re
from decimal import Decimal, InvalidOperation
from uuid import uuid4

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Sum
from django.utils import timezone

from .models import Payment, PayoutRequest, WalletAccount, WalletLedgerEntry
from .relworx import check_wallet_balance, RelworxError


def wallet_account(beneficiary, provider="relworx", currency="UGX"):
    return WalletAccount.objects.get_or_create(
        beneficiary=beneficiary, provider=provider, currency=currency,
    )[0]


def accrue_payment(payment):
    payment.refresh_from_db()
    if payment.status != Payment.Status.PAID:
        return
    base = payment.base_amount if payment.base_amount is not None else payment.amount
    net = payment.provider_net_amount if payment.provider_net_amount is not None else payment.amount
    commission = min(net, (base * settings.DEVELOPER_COMMISSION_RATE).quantize(Decimal("0.01")))
    owner_amount = max(Decimal("0"), net - commission)
    with transaction.atomic():
        if owner_amount:
            WalletLedgerEntry.objects.get_or_create(
                idempotency_key=f"payment:{payment.pk}:owner",
                defaults={
                    "wallet_account": wallet_account("owner", "relworx", payment.currency),
                    "payment": payment, "entry_type": WalletLedgerEntry.Type.PAYMENT_CREDIT,
                    "amount": owner_amount, "metadata": {"payment_reference": payment.reference},
                    "created_at": payment.confirmed_at or timezone.now(),
                },
            )
        if commission:
            WalletLedgerEntry.objects.get_or_create(
                idempotency_key=f"payment:{payment.pk}:developer",
                defaults={
                    "wallet_account": wallet_account("developer", "relworx", payment.currency),
                    "payment": payment, "entry_type": WalletLedgerEntry.Type.COMMISSION_CREDIT,
                    "amount": commission, "metadata": {"payment_reference": payment.reference},
                    "created_at": payment.confirmed_at or timezone.now(),
                },
            )
        if payment.developer_commission != commission:
            payment.developer_commission = commission
            payment.save(update_fields=["developer_commission", "updated_at"])


def wallet_summary(beneficiary):
    account = wallet_account(beneficiary)
    entries = account.entries.all()
    credited = entries.filter(entry_type__in=[WalletLedgerEntry.Type.PAYMENT_CREDIT, WalletLedgerEntry.Type.COMMISSION_CREDIT]).aggregate(total=Sum("amount"))["total"] or 0
    available = entries.aggregate(total=Sum("amount"))["total"] or 0
    available -= account.payout_requests.filter(status__in=["pending", "processing"]).count() * Decimal("1500")
    reserved = account.payout_requests.filter(status__in=[PayoutRequest.Status.PENDING, PayoutRequest.Status.PROCESSING]).aggregate(total=Sum("amount"))["total"] or 0
    today = entries.filter(
        entry_type__in=[WalletLedgerEntry.Type.PAYMENT_CREDIT, WalletLedgerEntry.Type.COMMISSION_CREDIT],
        created_at__date=timezone.localdate(),
    ).aggregate(total=Sum("amount"))["total"] or 0
    return {
        "provider": account.provider, "currency": account.currency,
        "today_credited": today, "credited": credited, "reserved": reserved,
        "available": max(Decimal("0"), available),
    }


def _destination(beneficiary):
    value = wallet_account(beneficiary).destination_msisdn or (settings.PAYMENT_OWNER_PAYOUT_MSISDN if beneficiary == "owner" else settings.PAYMENT_DEVELOPER_PAYOUT_MSISDN)
    digits = re.sub(r"\D", "", value or "")
    if digits.startswith("0") and len(digits) == 10:
        digits = f"256{digits[1:]}"
    return f"+{digits}" if digits else ""


def create_payout(beneficiary, amount, actor, client_token=None):
    try:
        amount = Decimal(str(amount)).quantize(Decimal("0.01"))
    except (InvalidOperation, TypeError, ValueError) as error:
        raise ValidationError({"amount": "Enter a valid payout amount."}) from error
    destination = _destination(beneficiary)
    if not amount.is_finite() or amount <= 0:
        raise ValidationError({"amount": "Enter a positive finite amount."})
    if not destination:
        raise ValidationError({"amount": f"{beneficiary.title()} payout destination is not configured on the server."})
    token = str(client_token or uuid4())
    with transaction.atomic():
        existing = PayoutRequest.objects.filter(client_token=token).first()
        if existing:
            if existing.beneficiary != beneficiary or existing.requested_by_id != actor.pk:
                raise ValidationError({"request_token": "This payout request token is already in use."})
            return existing
        wallet_account("owner")
        wallet_account("developer")
        locked_accounts = list(WalletAccount.objects.select_for_update().filter(provider="relworx", currency="UGX").order_by("pk"))
        account = next(item for item in locked_accounts if item.beneficiary == beneficiary)
        if account.payouts_frozen:
            raise ValidationError({"amount": "Wallet frozen please contact your developer."})
        available = account.entries.aggregate(total=Sum("amount"))["total"] or Decimal("0")
        available -= account.payout_requests.filter(status__in=["pending", "processing"]).count() * Decimal("1500")
        if amount < settings.RELWORX_PAYOUT_MIN_AMOUNT:
            raise ValidationError({"amount": f"The minimum Relworx payout is {settings.RELWORX_PAYOUT_MIN_AMOUNT:,.0f} UGX."})
        if amount > settings.RELWORX_PAYOUT_MAX_AMOUNT:
            raise ValidationError({"amount": f"The maximum Relworx payout is {settings.RELWORX_PAYOUT_MAX_AMOUNT:,.0f} UGX."})
        fee_buffer = Decimal("1500")
        if amount + fee_buffer > available:
            raise ValidationError({"amount": "Leave UGX 1,500 available for the withdrawal fee."})
        if settings.PAYOUT_EXECUTION_MODE == "immediate":
            try:
                provider_balance = Decimal(str(check_wallet_balance().get("balance", 0)))
                outstanding = PayoutRequest.objects.filter(status__in=["pending", "processing"])
                provider_balance -= (outstanding.aggregate(total=Sum("amount"))["total"] or Decimal("0")) + outstanding.count() * fee_buffer
            except (RelworxError, InvalidOperation, TypeError, ValueError) as error:
                raise ValidationError({"amount": "Provider balance could not be verified. Please try again."}) from error
            if amount + fee_buffer > provider_balance:
                raise ValidationError({"amount": "Relworx balance is insufficient for this payout plus the UGX 1,500 withdrawal fee."})
        if amount > available:
            raise ValidationError({"amount": f"Payout exceeds the available balance of {max(Decimal('0'), available):,.2f} UGX."})
        payout = PayoutRequest.objects.create(
            client_token=token, wallet_account=account, beneficiary=beneficiary,
            amount=amount, currency=account.currency, destination_msisdn=destination,
            requested_by=actor,
        )
        WalletLedgerEntry.objects.create(
            wallet_account=account, payout_request=payout,
            entry_type=WalletLedgerEntry.Type.PAYOUT_RESERVATION, amount=-amount,
            idempotency_key=f"payout:{payout.pk}:reserve", metadata={"reference": payout.reference},
        )
        return payout


def cancel_payout(payout, actor, beneficiary):
    with transaction.atomic():
        payout = PayoutRequest.objects.select_for_update().get(pk=payout.pk)
        if payout.beneficiary != beneficiary or payout.requested_by_id != actor.pk:
            raise PermissionError("You can only cancel your own payout requests.")
        if payout.status != PayoutRequest.Status.PENDING:
            raise ValidationError({"request": "Only pending payout requests can be cancelled."})
        WalletLedgerEntry.objects.get_or_create(
            idempotency_key=f"payout:{payout.pk}:release",
            defaults={"wallet_account": payout.wallet_account, "payout_request": payout,
                      "entry_type": WalletLedgerEntry.Type.PAYOUT_RELEASE, "amount": payout.amount,
                      "metadata": {"reference": payout.reference}},
        )
        payout.status = PayoutRequest.Status.CANCELLED
        payout.cancelled_at = timezone.now()
        payout.save(update_fields=["status", "cancelled_at", "updated_at"])
        return payout


def mask_msisdn(value):
    return value if len(value) <= 8 else f"{value[:4]}{'*' * (len(value) - 8)}{value[-4:]}"


def serialize_payout(payout):
    return {
        "id": payout.pk, "reference": payout.reference, "beneficiary": payout.beneficiary,
        "provider": payout.provider, "amount": payout.amount, "currency": payout.currency,
        "destination": mask_msisdn(payout.destination_msisdn), "status": payout.status,
        "requested_by": payout.requested_by.full_name if payout.requested_by else "System",
        "provider_request_id": payout.provider_request_id, "transaction_id": payout.transaction_id,
        "provider_fee": payout.provider_fee_actual, "failure_reason": payout.failure_reason,
        "created_at": payout.created_at, "cancelled_at": payout.cancelled_at, "completed_at": payout.completed_at,
    }


def wallet_payload(beneficiary, actor=None):
    payouts = PayoutRequest.objects.select_related("requested_by").filter(beneficiary=beneficiary, provider="relworx")
    if beneficiary == "developer" and actor:
        payouts = payouts.filter(requested_by=actor)
    account = wallet_account(beneficiary)
    return {
        "wallet": wallet_summary(beneficiary),
        "payouts_frozen": account.payouts_frozen,
        "withdrawal_fee_buffer": Decimal("1500"),
        "minimum_operating_balance": Decimal("1500"),
        "destination_configured": bool(_destination(beneficiary)),
        "destination": mask_msisdn(_destination(beneficiary)),
        "execution_mode": settings.PAYOUT_EXECUTION_MODE,
        "payouts_enabled": settings.RELWORX_PAYOUTS_ENABLED,
        "minimum_payout": settings.RELWORX_PAYOUT_MIN_AMOUNT,
        "maximum_payout": settings.RELWORX_PAYOUT_MAX_AMOUNT,
        "requests": [serialize_payout(item) for item in payouts[:50]],
        "ledger": [{"id": item.pk, "entry_type": item.entry_type, "amount": item.amount,
                    "reference": item.metadata.get("payment_reference") or item.metadata.get("reference", ""),
                    "created_at": item.created_at} for item in account.entries.all()[:100]],
    }
