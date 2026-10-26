import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
from fastapi.testclient import TestClient

from app import main

app = main.app


def test_health_endpoint_accepts_get_and_head():
    client = TestClient(app)

    assert client.get("/health").status_code == 200
    assert client.get("/health").json() == {"status": "ok"}
    assert client.head("/health").status_code == 200
    assert client.get("/").status_code == 200
    assert client.head("/").status_code == 200


def test_lifespan_uses_http1_for_supabase_and_closes_transport(monkeypatch):
    clients = []
    original_async_client = httpx.AsyncClient

    def create_http_client(**kwargs):
        clients.append((kwargs, original_async_client(**kwargs)))
        return clients[-1][1]

    async def wait_forever(*_args, **_kwargs):
        await asyncio.Future()

    monkeypatch.setattr(main.httpx, "AsyncClient", create_http_client)
    create_supabase = AsyncMock(return_value=SimpleNamespace())
    monkeypatch.setattr(main, "acreate_client", create_supabase)
    monkeypatch.setattr(main, "register_handlers", lambda *_args: None)
    monkeypatch.setattr(main.dispatcher, "start_polling", wait_forever)
    monkeypatch.setattr(main, "reconcile_payments", wait_forever)
    monkeypatch.setattr(main, "reconcile_broadcasts", wait_forever)
    monkeypatch.setattr(
        main,
        "schedule_welcome_media_preload",
        lambda _store: asyncio.create_task(wait_forever()),
    )
    monkeypatch.setattr(main.bot.session, "close", AsyncMock())
    monkeypatch.setattr(main.payments, "close", AsyncMock())

    async def run_test():
        async with main.lifespan(app):
            options = create_supabase.await_args.kwargs["options"]
            assert options.httpx_client is clients[0][1]
            assert clients[0][0] == {"http2": False, "timeout": 30}

        assert clients[0][1].is_closed

    asyncio.run(run_test())