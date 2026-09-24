import json
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from django.conf import settings


class RelworxError(Exception):
    def __init__(self, message, *, uncertain=False):
        super().__init__(message)
        self.uncertain = uncertain


def request_mobile_money_payment(payment):
    if not settings.RELWORX_API_KEY or not settings.RELWORX_ACCOUNT_NO:
        raise RelworxError("Payments are temporarily unavailable.")

    url = f"{settings.RELWORX_API_BASE_URL.rstrip('/')}/api/mobile-money/request-payment"
    payload = {
        "account_no": settings.RELWORX_ACCOUNT_NO,
        "reference": payment.reference,
        "msisdn": payment.payer_msisdn,
        "currency": payment.currency,
        "amount": float(payment.amount),
        "description": f"{payment.package.name} betting package",
    }
    request = Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {settings.RELWORX_API_KEY}",
            "Accept": "application/vnd.relworx.v2",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urlopen(request, timeout=settings.RELWORX_REQUEST_TIMEOUT_SECONDS) as response:
            result = json.loads(response.read().decode("utf-8"))
    except HTTPError as error:
        try:
            detail = json.loads(error.read().decode("utf-8")).get("message")
        except (ValueError, AttributeError):
            detail = None
        raise RelworxError(detail or "The payment provider rejected this request.") from error
    except (URLError, TimeoutError, ValueError) as error:
        raise RelworxError(
            "Relworx has not confirmed whether the request was received. We will keep checking before allowing another attempt.",
            uncertain=True,
        ) from error

    if not result.get("success") or not result.get("internal_reference"):
        raise RelworxError(result.get("message") or "The payment request could not be started.")
    return result
