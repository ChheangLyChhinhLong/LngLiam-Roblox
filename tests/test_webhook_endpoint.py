import hashlib
import hmac
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

from fastapi.testclient import TestClient

from app import main


def test_webhook_hides_missing_secret_configuration(monkeypatch):
    monkeypatch.setattr(main.settings, "khqrpay_webhook_secret", "")

    response = TestClient(main.app).post("/webhook/khqrpay", json={})

    assert response.status_code == 503
    assert response.json() == {"detail": "Webhook service is unavailable"}
    assert "secret" not in response.text.casefold()
    assert "khqrpay" not in response.text.casefold()


def test_webhook_hides_payment_provider_errors(monkeypatch):
    secret = "webhook-secret"
    body = json.dumps(
        {"event": "payment.update", "id": 123, "status": "Paid", "amount": 5.0}
    ).encode()
    signature = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    store = SimpleNamespace(
        order_by_transaction=AsyncMock(
            return_value={
                "id": "order-123",
                "chat_id": 456,
                "total": "5.00",
                "status": "pending",
            }
        )
    )
    monkeypatch.setattr(main.settings, "khqrpay_webhook_secret", secret)
    monkeypatch.setattr(main.app.state, "store", store, raising=False)
    monkeypatch.setattr(
        main.payments,
        "check_payment",
        AsyncMock(side_effect=RuntimeError("provider-secret database details")),
    )

    response = TestClient(main.app).post(
        "/webhook/khqrpay",
        content=body,
        headers={"x-webhook-signature": signature},
    )

    assert response.status_code == 503
    assert response.json() == {"detail": "Could not verify payment"}
    assert "provider-secret" not in response.text
    assert "database details" not in response.text


def test_paid_status_webhook_confirms_and_delivers_order(monkeypatch):
    secret = "webhook-secret"
    body = json.dumps(
        {
            "event": "payment.update",
            "id": 123,
            "status": "Paid",
            "amount": 5.0,
        }
    ).encode()
    signature = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    store = SimpleNamespace(
        order_by_transaction=AsyncMock(
            return_value={
                "id": "order-123",
                "chat_id": 456,
                "total": "5.00",
                "status": "pending",
            }
        )
    )
    monkeypatch.setattr(main.settings, "khqrpay_webhook_secret", secret)
    monkeypatch.setattr(main.app.state, "store", store, raising=False)
    monkeypatch.setattr(
        main.payments,
        "check_payment",
        AsyncMock(return_value={"status": "paid", "amount": "5.00"}),
    )
    deliver_order = AsyncMock()
    monkeypatch.setattr(main, "deliver_order", deliver_order)

    response = TestClient(main.app).post(
        "/webhook/khqrpay",
        content=body,
        headers={"x-webhook-signature": signature},
    )

    assert response.status_code == 200
    assert response.json() == {"received": True, "status": "paid"}
    deliver_order.assert_awaited_once_with(main.bot, store, main.settings, "order-123", 456)


def test_paid_status_webhook_credits_a_wallet_deposit(monkeypatch):
    secret = "webhook-secret"
    body = json.dumps(
        {
            "event": "payment.update",
            "id": 456,
            "status": "Paid",
            "amount": 10.0,
        }
    ).encode()
    signature = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    store = SimpleNamespace(
        order_by_transaction=AsyncMock(return_value=None),
        wallet_deposit_by_transaction=AsyncMock(
            return_value={
                "id": "deposit-123",
                "chat_id": 456,
                "amount": "10.00",
                "status": "pending",
                "transaction_id": "456",
            }
        ),
    )
    monkeypatch.setattr(main.settings, "khqrpay_webhook_secret", secret)
    monkeypatch.setattr(main.app.state, "store", store, raising=False)
    monkeypatch.setattr(
        main.payments,
        "check_payment",
        AsyncMock(return_value={"status": "paid", "amount": "10.00"}),
    )
    credit_deposit = AsyncMock(return_value=True)
    monkeypatch.setattr(main, "complete_wallet_deposit", credit_deposit)

    response = TestClient(main.app).post(
        "/webhook/khqrpay",
        content=body,
        headers={"x-webhook-signature": signature},
    )

    assert response.status_code == 200
    assert response.json() == {"received": True, "status": "paid"}
    credit_deposit.assert_awaited_once_with(main.bot, store, store.wallet_deposit_by_transaction.return_value)


def test_wallet_deposit_webhook_rejects_a_mismatched_payment_amount(monkeypatch):
    secret = "webhook-secret"
    body = json.dumps(
        {
            "event": "payment.update",
            "id": 456,
            "status": "Paid",
            "amount": 9.0,
        }
    ).encode()
    signature = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    store = SimpleNamespace(
        order_by_transaction=AsyncMock(return_value=None),
        wallet_deposit_by_transaction=AsyncMock(
            return_value={
                "id": "deposit-123",
                "chat_id": 456,
                "amount": "10.00",
                "status": "pending",
                "transaction_id": "456",
            }
        ),
    )
    monkeypatch.setattr(main.settings, "khqrpay_webhook_secret", secret)
    monkeypatch.setattr(main.app.state, "store", store, raising=False)
    monkeypatch.setattr(
        main.payments,
        "check_payment",
        AsyncMock(return_value={"status": "paid", "amount": "10.00"}),
    )
    credit_deposit = AsyncMock()
    monkeypatch.setattr(main, "complete_wallet_deposit", credit_deposit)

    response = TestClient(main.app).post(
        "/webhook/khqrpay",
        content=body,
        headers={"x-webhook-signature": signature},
    )

    assert response.status_code == 400
    assert response.json()["detail"] == "Payment amount does not match the deposit"
    credit_deposit.assert_not_awaited()