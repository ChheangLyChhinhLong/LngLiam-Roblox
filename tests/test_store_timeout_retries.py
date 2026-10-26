import asyncio
from types import SimpleNamespace

import httpx

from app.store import Store


class RetryableBuilder:
    def __init__(self) -> None:
        self.calls = 0

    def select(self, *_args, **_kwargs):
        return self

    def eq(self, *_args, **_kwargs):
        return self

    def maybe_single(self, *_args, **_kwargs):
        return self

    async def execute(self):
        self.calls += 1
        if self.calls == 1:
            raise httpx.ReadTimeout("simulated timeout")
        return SimpleNamespace(data={"language": "en"})


class RetryableClient:
    def table(self, _name):
        return RetryableBuilder()


def test_store_retries_transient_supabase_timeouts():
    async def run_test():
        store = Store(RetryableClient())
        assert await store.user_language(42) == "en"

    asyncio.run(run_test())
