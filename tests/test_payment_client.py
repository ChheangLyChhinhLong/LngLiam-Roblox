import asyncio
from unittest.mock import AsyncMock

import httpx

from app.payments import KHQRPayClient, aba_mobile_deeplink, aba_mobile_redirect_url


def test_client_sends_bearer_api_key():
    async def run_test():
        client = KHQRPayClient("sentinel-api-key", "https://khqrpay.site/api/v1")
        try:
            assert client._client.headers["Authorization"] == "Bearer sentinel-api-key"
        finally:
            await client.close()

    asyncio.run(run_test())


def test_aba_payment_uses_documented_endpoint_and_normalizes_response():
    async def run_test():
        client = KHQRPayClient(
            "api-key",
            "https://khqrpay.site/api/v1",
            "https://shop.example.com/webhook/khqrpay",
        )
        response = httpx.Response(
            200,
            json={
                "ok": True,
                "code": 0,
                "id": 847291036184,
                "type": "aba",
                "amount": 5,
                "status": "Pending",
                "qr": "KHQR+payload/123",
                "qr_image": "data:image/svg+xml;base64,c3Zn",
                "checkout_url": "https://khqrpay.site/pay/847291036184",
                "aba_deeplink": "https://dpayment-epreprod.ababank.com/deep-link",
            },
            request=httpx.Request(
                "POST", "https://khqrpay.site/api/v1/aba/create"
            ),
        )
        post = AsyncMock(return_value=response)
        client._client.post = post
        try:
            payment = await client.create_payment("5.00", "order-456")
            assert post.await_args.args[0] == "/aba/create"
            assert post.await_args.kwargs["json"] == {
                "amount": 5.0,
                "webhook": "https://shop.example.com/webhook/khqrpay",
            }
            assert post.await_args.kwargs["headers"] == {
                "Idempotency-Key": "order-456"
            }
            assert payment["transaction_id"] == "847291036184"
            assert payment["qr_string"] == "KHQR+payload/123"
            assert payment["status"] == "pending"
            assert payment["checkout_url"] == "https://khqrpay.site/pay/847291036184"
        finally:
            await client.close()

    asyncio.run(run_test())


def test_payment_status_uses_documented_id_query_and_normalizes_status():
    async def run_test():
        client = KHQRPayClient("api-key", "https://khqrpay.site/api/v1")
        response = httpx.Response(
            200,
            json={
                "ok": True,
                "payment": {
                    "id": 847291036184,
                    "amount": 5,
                    "status": "Paid",
                    "type": "aba",
                },
            },
            request=httpx.Request(
                "GET",
                "https://khqrpay.site/api/v1/payment?id=847291036184",
            ),
        )
        get = AsyncMock(return_value=response)
        client._client.get = get
        try:
            payment = await client.check_payment("847291036184")
            assert get.await_args.args[0] == "/payment"
            assert get.await_args.kwargs["params"] == {"id": "847291036184"}
            assert payment["transaction_id"] == "847291036184"
            assert payment["status"] == "paid"
            assert payment["amount"] == 5
        finally:
            await client.close()

    asyncio.run(run_test())


def test_api_failure_surfaces_documented_error_text():
    async def run_test():
        client = KHQRPayClient("api-key", "https://khqrpay.site/api/v1")
        client._client.get = AsyncMock(
            return_value=httpx.Response(
                404,
                json={
                    "ok": False,
                    "error": "payment_not_found",
                    "error_text": "No payment matches that id.",
                },
                request=httpx.Request(
                    "GET", "https://khqrpay.site/api/v1/payment?id=missing"
                ),
            )
        )
        try:
            try:
                await client.check_payment("missing")
            except RuntimeError as exc:
                assert "No payment matches that id." in str(exc)
            else:
                raise AssertionError("Expected KHQRPay error to be raised")
        finally:
            await client.close()

    asyncio.run(run_test())


def test_aba_mobile_deeplink_encodes_raw_qr_string():
    assert aba_mobile_deeplink("KHQR+payload/123=") == (
        "abamobilebank://ababank.com?type=payway&qrcode=KHQR%2Bpayload%2F123%3D"
    )


def test_aba_mobile_redirect_uses_our_https_host():
    assert aba_mobile_redirect_url(
        "https://shop.example.com/webhook/khqrpay", "order-123"
    ) == "https://shop.example.com/aba/order-123"
