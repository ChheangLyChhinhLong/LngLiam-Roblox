import hashlib
import hmac
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

from fastapi.testclient import TestClient

from app import main


def test_paid_status_webhook_confirms_and_delivers_order(monkeypatch):
    secret = "webhook-secret"
    body = json.dumps(
        {"status": "paid", "transaction_id": "txn-123", "amount": "5.00"}
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
    monkeypatch.setattr(main.settings, "khpay_webhook_secret", secret)
    monkeypatch.setattr(main.app.state, "store", store, raising=False)
    monkeypatch.setattr(
        main.payments,
        "check_payment",
        AsyncMock(return_value={"status": "paid", "amount": "5.00"}),
    )
    deliver_order = AsyncMock()
    monkeypatch.setattr(main, "deliver_order", deliver_order)

    response = TestClient(main.app).post(
        "/webhook/khpay",
        content=body,
        headers={"x-webhook-signature": signature},
    )

    assert response.status_code == 200
    assert response.json() == {"received": True, "status": "paid"}
    deliver_order.assert_awaited_once_with(main.bot, store, main.settings, "order-123", 456)


def test_paid_status_webhook_credits_a_wallet_deposit(monkeypatch):
    secret = "webhook-secret"
    body = json.dumps(
        {"status": "paid", "transaction_id": "txn-deposit", "amount": "10.00"}
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
                "transaction_id": "txn-deposit",
            }
        ),
    )
    monkeypatch.setattr(main.settings, "khpay_webhook_secret", secret)
    monkeypatch.setattr(main.app.state, "store", store, raising=False)
    monkeypatch.setattr(
        main.payments,
        "check_payment",
        AsyncMock(return_value={"status": "paid", "amount": "10.00"}),
    )
    credit_deposit = AsyncMock(return_value=True)
    monkeypatch.setattr(main, "complete_wallet_deposit", credit_deposit)

    response = TestClient(main.app).post(
        "/webhook/khpay",
        content=body,
        headers={"x-webhook-signature": signature},
    )

    assert response.status_code == 200
    assert response.json() == {"received": True, "status": "paid"}
    credit_deposit.assert_awaited_once_with(main.bot, store, store.wallet_deposit_by_transaction.return_value)


def test_wallet_deposit_webhook_rejects_a_mismatched_payment_amount(monkeypatch):
    secret = "webhook-secret"
    body = json.dumps(
        {"status": "paid", "transaction_id": "txn-deposit", "amount": "9.00"}
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
                "transaction_id": "txn-deposit",
            }
        ),
    )
    monkeypatch.setattr(main.settings, "khpay_webhook_secret", secret)
    monkeypatch.setattr(main.app.state, "store", store, raising=False)
    monkeypatch.setattr(
        main.payments,
        "check_payment",
        AsyncMock(return_value={"status": "paid", "amount": "10.00"}),
    )
    credit_deposit = AsyncMock()
    monkeypatch.setattr(main, "complete_wallet_deposit", credit_deposit)

    response = TestClient(main.app).post(
        "/webhook/khpay",
        content=body,
        headers={"x-webhook-signature": signature},
    )

    assert response.status_code == 400
    assert response.json()["detail"] == "Payment amount does not match the deposit"
    credit_deposit.assert_not_awaited()