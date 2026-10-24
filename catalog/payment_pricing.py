from decimal import Decimal, ROUND_CEILING

from django.conf import settings


def payment_quote(base_amount, provider="relworx"):
    base = max(Decimal("0"), Decimal(str(base_amount)))
    rate = settings.RELWORX_COLLECTION_FEE_RATE if provider == "relworx" else Decimal("0")
    rate = min(Decimal("0.99"), max(Decimal("0"), rate))
    total = (base / (Decimal("1") - rate)).quantize(Decimal("1"), rounding=ROUND_CEILING) if rate else base
    return {
        "base_amount": base,
        "provider_fee_estimate": total - base,
        "total_charge": total,
        "currency": "UGX",
        "provider": provider,
        "fee_rate": rate,
    }
