import hashlib
import hmac
from typing import Any
from urllib.parse import quote

import httpx


def aba_mobile_deeplink(qr_string: str) -> str:
    if not qr_string.strip():
        raise ValueError("QR string must not be empty")
    return f"abamobilebank://ababank.com?type=payway&qrcode={quote(qr_string, safe='')}"


def aba_mobile_redirect_url(base_url: str, order_id: str) -> str:
    parsed = httpx.URL(base_url)
    if parsed.scheme != "https" or not parsed.host:
        raise ValueError("A public HTTPS app URL is required for the ABA button")
    return f"{str(parsed.copy_with(path='').copy_with(query=None, fragment=None)).rstrip('/')}/aba/{quote(order_id, safe='')}"


def verify_webhook_signature(raw_body: bytes, signature: str, secret: str) -> bool:
    if not signature or not secret:
        return False
    digest = hmac.new(secret.encode("utf-8"), raw_body, hashlib.sha256).hexdigest()
    expected = f"sha256={digest}" if signature.startswith("sha256=") else digest
    return hmac.compare_digest(expected, signature)


class KHQRPayClient:
    def __init__(self, api_key: str, base_url: str, webhook_url: str = "") -> None:
        self._webhook_url = webhook_url
        self._client = httpx.AsyncClient(
            base_url=base_url.rstrip("/"),
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            timeout=20.0,
        )

    async def create_payment(
        self,
        amount: str,
        order_id: str,
    ) -> dict[str, Any]:
        body: dict[str, Any] = {"amount": float(amount)}
        if self._webhook_url:
            body["webhook"] = self._webhook_url
        response = await self._client.post(
            "/aba/create",
            headers={"Idempotency-Key": order_id},
            json=body,
        )
        try:
            payload = response.json()
        except ValueError:
            raise RuntimeError(
                f"KHQRPay returned HTTP {response.status_code} with an invalid JSON response"
            ) from None
        if not isinstance(payload, dict):
            raise RuntimeError(
                f"KHQRPay returned HTTP {response.status_code} with an invalid response"
            )
        if response.is_error or payload.get("ok") is not True:
            error = (
                payload.get("error_text")
                or payload.get("error")
                or payload.get("msg")
                or f"HTTP {response.status_code}"
            )
            raise RuntimeError(f"KHQRPay error: {error}")
        payment_id = payload.get("id")
        qr_string = payload.get("qr")
        if (
            isinstance(payment_id, bool)
            or not isinstance(payment_id, (int, str))
            or not str(payment_id).isdigit()
        ):
            raise RuntimeError("KHQRPay response is missing payment id")
        if not isinstance(qr_string, str) or not qr_string.strip():
            raise RuntimeError("KHQRPay response is missing QR data")
        return {
            "transaction_id": str(payment_id),
            "qr_string": qr_string,
            "qr_image": payload.get("qr_image"),
            "checkout_url": payload.get("checkout_url"),
            "aba_deeplink": payload.get("aba_deeplink"),
            "aba_deeplink_android": payload.get("aba_deeplink_android"),
            "amount": payload.get("amount"),
            "status": self._normalized_status(payload.get("status")),
        }

    async def check_payment(self, transaction_id: str) -> dict[str, Any]:
        response = await self._client.get("/payment", params={"id": transaction_id})
        try:
            payload = response.json()
        except ValueError:
            raise RuntimeError(
                f"KHQRPay returned HTTP {response.status_code} with an invalid JSON response"
            ) from None
        if not isinstance(payload, dict):
            raise RuntimeError(
                f"KHQRPay returned HTTP {response.status_code} with an invalid response"
            )
        if response.is_error or payload.get("ok") is not True:
            error = (
                payload.get("error_text")
                or payload.get("error")
                or payload.get("msg")
                or f"HTTP {response.status_code}"
            )
            raise RuntimeError(f"KHQRPay error: {error}")
        payment = payload.get("payment")
        if not isinstance(payment, dict):
            raise RuntimeError("KHQRPay response is missing payment data")
        return {
            **payment,
            "transaction_id": str(payment.get("id", transaction_id)),
            "status": self._normalized_status(payment.get("status")),
        }

    @staticmethod
    def _normalized_status(status: Any) -> str:
        return status.casefold() if isinstance(status, str) else ""

    async def close(self) -> None:
        await self._client.aclose()
