import json
from urllib.parse import urlencode
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from django.conf import settings


class RelworxError(Exception):
    def __init__(self, message, *, uncertain=False, status_code=None, code="", payload=None):
        super().__init__(message)
        self.uncertain = uncertain
        self.status_code = status_code
        self.code = code
        self.payload = payload or {}


def _authenticated_request(url, *, data=None, method="GET"):
    request = Request(
        url,
        data=json.dumps(data).encode("utf-8") if data is not None else None,
        headers={
            "Authorization": f"Bearer {settings.RELWORX_API_KEY}",
            "Accept": "application/vnd.relworx.v2",
            **({"Content-Type": "application/json"} if data is not None else {}),
        },
        method=method,
    )
    try:
        with urlopen(request, timeout=settings.RELWORX_REQUEST_TIMEOUT_SECONDS) as response:
            return json.loads(response.read().decode("utf-8"))
    except HTTPError as error:
        error_payload = {}
        try:
            error_payload = json.loads(error.read().decode("utf-8"))
            detail = error_payload.get("message") if isinstance(error_payload, dict) else None
        except (ValueError, AttributeError):
            detail = None
        raise RelworxError(
            detail or "The payment provider rejected this request.",
            status_code=error.code,
            code=str(error_payload.get("code", "")) if isinstance(error_payload, dict) else "",
            payload=error_payload if isinstance(error_payload, dict) else {},
        ) from error
    except (URLError, TimeoutError, ValueError) as error:
        raise RelworxError(
            "Relworx has not confirmed whether the request was received. Please check again shortly.",
            uncertain=True,
        ) from error


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
    result = _authenticated_request(url, data=payload, method="POST")

    if not result.get("success") or not result.get("internal_reference"):
        raise RelworxError(result.get("message") or "The payment request could not be started.")
    return result


def check_mobile_money_payment(payment):
    if not settings.RELWORX_API_KEY or not settings.RELWORX_ACCOUNT_NO:
        raise RelworxError("Payments are temporarily unavailable.")
    query = urlencode({
        # Relworx accepts the provider reference or the original customer reference.
        # The latter recovers requests whose initiation response timed out locally.
        "internal_reference": payment.internal_reference or payment.reference,
        "account_no": settings.RELWORX_ACCOUNT_NO,
    })
    url = f"{settings.RELWORX_API_BASE_URL.rstrip('/')}/api/mobile-money/check-request-status?{query}"
    result = _authenticated_request(url)
    if not result.get("success"):
        raise RelworxError(result.get("message") or "The payment status could not be checked.")
    return result
