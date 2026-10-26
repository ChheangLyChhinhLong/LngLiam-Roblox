import asyncio
from datetime import datetime, timezone
from typing import Any

import httpx
from supabase import AsyncClient

from app.security import encrypt_stock, stock_fingerprint


class _RetryingRequestBuilder:
    def __init__(self, builder: Any) -> None:
        self._builder = builder

    def __getattr__(self, name: str) -> Any:
        attr = getattr(self._builder, name)
        if not callable(attr):
            return attr

        def wrapper(*args: Any, **kwargs: Any) -> "_RetryingRequestBuilder":
            return _RetryingRequestBuilder(attr(*args, **kwargs))

        return wrapper

    async def execute(self, *args: Any, **kwargs: Any) -> Any:
        last_error: Exception | None = None
        for attempt in range(3):
            try:
                return await self._builder.execute(*args, **kwargs)
            except (
                asyncio.TimeoutError,
                TimeoutError,
                OSError,
                httpx.ReadTimeout,
                httpx.ConnectTimeout,
                httpx.TimeoutException,
                httpx.TransportError,
            ) as exc:
                last_error = exc
                if attempt == 2:
                    raise
                await asyncio.sleep(0.5 * (attempt + 1))
        if last_error is not None:
            raise last_error
        raise RuntimeError("Supabase request execution failed without an exception")


class _RetryingSupabaseClient:
    def __init__(self, client: AsyncClient) -> None:
        self._client = client

    def __getattr__(self, name: str) -> Any:
        return getattr(self._client, name)

    def table(self, *args: Any, **kwargs: Any) -> _RetryingRequestBuilder:
        return _RetryingRequestBuilder(self._client.table(*args, **kwargs))

    def rpc(self, *args: Any, **kwargs: Any) -> _RetryingRequestBuilder:
        return _RetryingRequestBuilder(self._client.rpc(*args, **kwargs))


class Store:
    def __init__(self, client: AsyncClient) -> None:
        self.client = _RetryingSupabaseClient(client)

    async def upsert_user(self, telegram_id: int, username: str | None, first_name: str, language: str) -> None:
        await self.client.table("users").upsert(
            {
                "telegram_id": telegram_id,
                "username": username,
                "first_name": first_name,
                "language": language,
            },
            on_conflict="telegram_id",
        ).execute()

    async def user_language(self, telegram_id: int) -> str | None:
        result = await (
            self.client.table("users")
            .select("language")
            .eq("telegram_id", telegram_id)
            .maybe_single()
            .execute()
        )
        return (result.data or {}).get("language")

    async def stock_notification_users(self) -> list[dict[str, Any]]:
        page_size = 1000
        offset = 0
        users: list[dict[str, Any]] = []
        while True:
            result = await (
                self.client.table("users")
                .select("telegram_id,language")
                .order("telegram_id")
                .range(offset, offset + page_size - 1)
                .execute()
            )
            page = result.data or []
            users.extend(page)
            if len(page) < page_size:
                return users
            offset += page_size

    async def create_scheduled_broadcast(
        self,
        admin_id: int,
        scheduled_at: str,
        content: list[dict[str, Any]],
        buttons: list[dict[str, str]],
    ) -> str:
        result = await (
            self.client.table("broadcast_jobs")
            .insert(
                {
                    "admin_id": admin_id,
                    "scheduled_at": scheduled_at,
                    "content": content,
                    "buttons": buttons,
                    "status": "scheduled",
                }
            )
            .select("id")
            .execute()
        )
        rows = result.data or []
        if not rows or not rows[0].get("id"):
            raise RuntimeError("Supabase did not return the scheduled broadcast ID")
        return str(rows[0]["id"])

    async def due_broadcast_jobs(self, now: str) -> list[dict[str, Any]]:
        result = await (
            self.client.table("broadcast_jobs")
            .select("id,admin_id,scheduled_at,content,buttons")
            .eq("status", "scheduled")
            .lte("scheduled_at", now)
            .order("scheduled_at")
            .limit(10)
            .execute()
        )
        return result.data or []

    async def claim_broadcast_job(self, job_id: str) -> bool:
        result = await (
            self.client.table("broadcast_jobs")
            .update({"status": "sending"})
            .eq("id", job_id)
            .eq("status", "scheduled")
            .select("id")
            .execute()
        )
        return bool(result.data)

    async def finish_broadcast_job(
        self,
        job_id: str,
        status: str,
        sent: int = 0,
        failed: int = 0,
    ) -> None:
        if status not in {"sent", "cancelled", "failed"}:
            raise ValueError("Invalid scheduled broadcast status")
        await (
            self.client.table("broadcast_jobs")
            .update(
                {
                    "status": status,
                    "sent_count": sent,
                    "failed_count": failed,
                    "finished_at": datetime.now(timezone.utc).isoformat(),
                }
            )
            .eq("id", job_id)
            .execute()
        )

    async def scheduled_broadcasts(self, admin_id: int) -> list[dict[str, Any]]:
        result = await (
            self.client.table("broadcast_jobs")
            .select("id,scheduled_at,status")
            .eq("admin_id", admin_id)
            .eq("status", "scheduled")
            .order("scheduled_at")
            .limit(20)
            .execute()
        )
        return result.data or []

    async def cancel_scheduled_broadcast(self, job_id: str, admin_id: int) -> dict[str, Any] | None:
        result = await (
            self.client.table("broadcast_jobs")
            .update({"status": "cancelled"})
            .eq("id", job_id)
            .eq("admin_id", admin_id)
            .eq("status", "scheduled")
            .select("id,content")
            .maybe_single()
            .execute()
        )
        return result.data if result else None

    async def set_user_language(self, telegram_id: int, language: str) -> None:
        if language not in {"km", "en"}:
            raise ValueError("Unsupported language")
        await self.client.table("users").update({"language": language}).eq("telegram_id", telegram_id).execute()

    async def is_reseller(self, telegram_id: int) -> bool:
        result = await (
            self.client.table("resellers")
            .select("telegram_id")
            .eq("telegram_id", telegram_id)
            .maybe_single()
            .execute()
        )
        return bool(result and result.data)

    async def resellers(self) -> list[dict[str, Any]]:
        result = await (
            self.client.table("resellers")
            .select("telegram_id")
            .order("telegram_id")
            .execute()
        )
        return result.data or []

    async def add_reseller(self, telegram_id: int) -> None:
        if telegram_id <= 0:
            raise ValueError("Telegram ID must be a positive integer")
        await self.client.table("resellers").upsert(
            {"telegram_id": telegram_id},
            on_conflict="telegram_id",
            ignore_duplicates=True,
        ).execute()

    async def remove_reseller(self, telegram_id: int) -> bool:
        result = await (
            self.client.table("resellers")
            .delete()
            .eq("telegram_id", telegram_id)
            .select("telegram_id")
            .execute()
        )
        return bool(result.data)

    async def bot_settings(self, keys: tuple[str, ...]) -> dict[str, str]:
        result = await (
            self.client.table("bot_settings")
            .select("key,value")
            .in_("key", list(keys))
            .execute()
        )
        return {item["key"]: item["value"] for item in result.data or []}

    async def set_bot_setting(self, key: str, value: str) -> None:
        await self.client.table("bot_settings").upsert(
            {"key": key, "value": value},
            on_conflict="key",
        ).execute()

    async def wallet_balance(self, telegram_id: int) -> str:
        result = await (
            self.client.table("user_wallets")
            .select("balance")
            .eq("telegram_id", telegram_id)
            .maybe_single()
            .execute()
        )
        data = getattr(result, "data", None)
        return str((data or {}).get("balance", "0.00"))

    async def create_wallet_deposit(self, telegram_id: int, amount: str) -> dict[str, Any]:
        result = await self.client.rpc(
            "create_wallet_deposit",
            {"p_chat_id": telegram_id, "p_amount": amount},
        ).execute()
        return result.data

    async def set_wallet_deposit_payment(
        self,
        deposit_id: str,
        transaction_id: str,
        payment_url: str,
    ) -> None:
        await self.client.rpc(
            "set_wallet_deposit_payment",
            {
                "p_deposit_id": deposit_id,
                "p_transaction_id": transaction_id,
                "p_payment_url": payment_url,
            },
        ).execute()

    async def pending_wallet_payment_deeplink(self, deposit_id: str) -> str | None:
        result = await (
            self.client.table("wallet_deposits")
            .select("payment_url")
            .eq("id", deposit_id)
            .eq("status", "pending")
            .maybe_single()
            .execute()
        )
        return (result.data or {}).get("payment_url")

    async def wallet_deposit_by_transaction(self, transaction_id: str) -> dict[str, Any] | None:
        result = await (
            self.client.table("wallet_deposits")
            .select("id,chat_id,amount,status,transaction_id")
            .eq("transaction_id", transaction_id)
            .maybe_single()
            .execute()
        )
        return result.data

    async def wallet_deposit_for_user(
        self,
        deposit_id: str,
        telegram_id: int,
    ) -> dict[str, Any] | None:
        result = await (
            self.client.table("wallet_deposits")
            .select("id,chat_id,amount,status,transaction_id")
            .eq("id", deposit_id)
            .eq("chat_id", telegram_id)
            .maybe_single()
            .execute()
        )
        return result.data

    async def pending_wallet_deposits(self) -> list[dict[str, Any]]:
        result = await (
            self.client.table("wallet_deposits")
            .select("id,chat_id,amount,transaction_id")
            .eq("status", "pending")
            .not_.is_("transaction_id", "null")
            .execute()
        )
        return result.data or []

    async def complete_wallet_deposit(self, deposit_id: str) -> bool:
        result = await self.client.rpc(
            "complete_wallet_deposit",
            {"p_deposit_id": deposit_id},
        ).execute()
        return bool(result.data)

    async def close_wallet_deposit(self, deposit_id: str, status: str) -> None:
        await self.client.rpc(
            "close_wallet_deposit",
            {"p_deposit_id": deposit_id, "p_status": status},
        ).execute()

    async def set_wallet_deposit_message(self, deposit_id: str, message_id: int) -> None:
        await (
            self.client.table("wallet_deposits")
            .update({"message_id": message_id})
            .eq("id", deposit_id)
            .execute()
        )

    async def wallet_deposit_message(self, deposit_id: str) -> dict[str, Any] | None:
        result = await (
            self.client.table("wallet_deposits")
            .select("chat_id,message_id")
            .eq("id", deposit_id)
            .maybe_single()
            .execute()
        )
        return result.data

    async def category_by_name(self, name: str) -> dict[str, Any] | None:
        result = await (
            self.client.table("categories")
            .select("id,name,description")
            .ilike("name", name)
            .eq("active", True)
            .maybe_single()
            .execute()
        )
        return result.data

    async def categories(self) -> list[dict[str, Any]]:
        result = await (
            self.client.table("categories")
            .select("id,name,description")
            .eq("active", True)
            .order("sort_order")
            .execute()
        )
        return result.data

    async def category(self, category_id: str) -> dict[str, Any] | None:
        result = await (
            self.client.table("categories")
            .select("id,name,description")
            .eq("id", category_id)
            .eq("active", True)
            .maybe_single()
            .execute()
        )
        return result.data

    async def products(self, category_id: str) -> list[dict[str, Any]]:
        result = await (
            self.client.table("products")
            .select("id,name,description,price,reseller_price")
            .eq("category_id", category_id)
            .eq("active", True)
            .order("sort_order")
            .execute()
        )
        return result.data

    async def product(self, product_id: str) -> dict[str, Any] | None:
        result = await (
            self.client.table("products")
            .select(
                "id,category_id,name,description,price,reseller_price,image_url,video_url,"
                "delivery_message,delivery_image_url,delivery_video_url"
            )
            .eq("id", product_id)
            .eq("active", True)
            .maybe_single()
            .execute()
        )
        return result.data

    async def create_product(
        self,
        category_id: str,
        name: str,
        price: str,
        description: str,
        image_url: str | None = None,
        video_url: str | None = None,
        reseller_price: str | None = None,
        delivery_message: str | None = None,
        delivery_image_url: str | None = None,
        delivery_video_url: str | None = None,
    ) -> str:
        if image_url and video_url:
            raise ValueError("A product cannot have both an image and a video")
        product_data = {
            "category_id": category_id,
            "name": name,
            "price": price,
            "reseller_price": reseller_price if reseller_price is not None else price,
            "description": description,
        }
        if image_url:
            product_data["image_url"] = image_url
        if video_url:
            product_data["video_url"] = video_url
        if any(
            value is not None
            for value in (delivery_message, delivery_image_url, delivery_video_url)
        ):
            product_data.update(
                {
                    "delivery_message": delivery_message or "",
                    "delivery_image_url": delivery_image_url,
                    "delivery_video_url": delivery_video_url,
                }
            )
        result = await (
            self.client.table("products")
            .insert(product_data)
            .select("id")
            .execute()
        )
        data = result.data
        product = data[0] if isinstance(data, list) and data else data
        if not isinstance(product, dict) or not product.get("id"):
            raise RuntimeError("Supabase did not return the new product ID")
        return str(product["id"])

    async def update_product(
        self,
        product_id: str,
        name: str,
        price: str,
        reseller_price: str,
        description: str,
        image_url: str | None = None,
        video_url: str | None = None,
        update_media: bool = False,
        delivery_message: str | None = None,
        delivery_image_url: str | None = None,
        delivery_video_url: str | None = None,
        update_delivery: bool = False,
    ) -> None:
        values: dict[str, Any] = {
            "name": name,
            "price": price,
            "reseller_price": reseller_price,
            "description": description,
        }
        if update_media:
            values["image_url"] = image_url
            values["video_url"] = video_url
        if update_delivery:
            values.update(
                {
                    "delivery_message": delivery_message or "",
                    "delivery_image_url": delivery_image_url,
                    "delivery_video_url": delivery_video_url,
                }
            )
        await self.client.table("products").update(values).eq("id", product_id).execute()

    async def update_product_image(self, product_id: str, image_url: str) -> bool:
        result = await (
            self.client.table("products")
            .update({"image_url": image_url, "video_url": None})
            .eq("id", product_id)
            .select("id")
            .maybe_single()
            .execute()
        )
        return bool(result.data)

    async def update_product_video(self, product_id: str, video_url: str) -> bool:
        result = await (
            self.client.table("products")
            .update({"image_url": None, "video_url": video_url})
            .eq("id", product_id)
            .select("id")
            .maybe_single()
            .execute()
        )
        return bool(result.data)

    async def available_stock(self, product_id: str) -> int:
        result = await self.client.rpc(
            "available_stock_count", {"p_product_id": product_id}
        ).execute()
        return int(result.data or 0)

    async def reserve_order(self, chat_id: int, product_id: str, quantity: int) -> dict[str, Any]:
        result = await self.client.rpc(
            "reserve_order",
            {"p_chat_id": chat_id, "p_product_id": product_id, "p_quantity": quantity},
        ).execute()
        return result.data

    async def reserve_wallet_order(self, chat_id: int, product_id: str, quantity: int) -> dict[str, Any]:
        result = await self.client.rpc(
            "reserve_wallet_order",
            {"p_chat_id": chat_id, "p_product_id": product_id, "p_quantity": quantity},
        ).execute()
        return result.data

    async def set_payment(self, order_id: str, transaction_id: str, payment_url: str | None) -> None:
        await self.client.rpc(
            "set_order_payment",
            {"p_order_id": order_id, "p_transaction_id": transaction_id, "p_payment_url": payment_url},
        ).execute()

    async def pending_payment_deeplink(self, order_id: str) -> str | None:
        result = await (
            self.client.table("orders")
            .select("payment_url")
            .eq("id", order_id)
            .eq("status", "pending")
            .maybe_single()
            .execute()
        )
        return (result.data or {}).get("payment_url")

    async def set_payment_message(self, order_id: str, message_id: int) -> str | None:
        result = await (
            self.client.table("orders")
            .update({"message_id": message_id})
            .eq("id", order_id)
            .select("status")
            .maybe_single()
            .execute()
        )
        return (result.data or {}).get("status")

    async def payment_message(self, order_id: str) -> dict[str, Any] | None:
        result = await (
            self.client.table("orders")
            .select("chat_id,message_id")
            .eq("id", order_id)
            .maybe_single()
            .execute()
        )
        return result.data

    async def clear_payment_message(self, order_id: str) -> None:
        await (
            self.client.table("orders")
            .update({"message_id": None})
            .eq("id", order_id)
            .execute()
        )

    async def order_by_transaction(self, transaction_id: str) -> dict[str, Any] | None:
        result = await (
            self.client.table("orders")
            .select("id,chat_id,total,status")
            .eq("transaction_id", transaction_id)
            .maybe_single()
            .execute()
        )
        return result.data

    async def release_order(self, order_id: str, status: str = "cancelled") -> None:
        await self.client.rpc("release_order", {"p_order_id": order_id, "p_status": status}).execute()

    async def pending_orders(self) -> list[dict[str, Any]]:
        result = await (
            self.client.table("orders")
            .select("id,chat_id,transaction_id,status")
            .eq("status", "pending")
            .not_.is_("transaction_id", "null")
            .execute()
        )
        return result.data

    async def undelivered_orders(self) -> list[dict[str, Any]]:
        result = await (
            self.client.table("orders")
            .select("id,chat_id")
            .eq("status", "paid")
            .is_("delivered_at", "null")
            .execute()
        )
        return result.data

    async def fulfill_order(self, order_id: str) -> list[str]:
        result = await self.client.rpc("fulfill_order", {"p_order_id": order_id}).execute()
        return result.data or []

    async def order_delivery_details(self, order_id: str) -> dict[str, Any] | None:
        order_result = await (
            self.client.table("orders")
            .select("product_id")
            .eq("id", order_id)
            .maybe_single()
            .execute()
        )
        order = order_result.data
        if not order:
            return None
        product_result = await (
            self.client.table("products")
            .select("delivery_message,delivery_image_url,delivery_video_url")
            .eq("id", order["product_id"])
            .maybe_single()
            .execute()
        )
        return product_result.data

    async def mark_delivered(self, order_id: str) -> None:
        await self.client.rpc("mark_order_delivered", {"p_order_id": order_id}).execute()

    async def order_notification_details(self, order_id: str) -> dict[str, Any] | None:
        order_result = await (
            self.client.table("orders")
            .select("id,chat_id,product_id,quantity,total,transaction_id,created_at,status")
            .eq("id", order_id)
            .maybe_single()
            .execute()
        )
        order = order_result.data
        if not order:
            return None
        product_result = await (
            self.client.table("products")
            .select("name,price,reseller_price")
            .eq("id", order["product_id"])
            .maybe_single()
            .execute()
        )
        user_result = await (
            self.client.table("users")
            .select("username,first_name")
            .eq("telegram_id", order["chat_id"])
            .maybe_single()
            .execute()
        )
        return {
            **order,
            "product": product_result.data or {},
            "customer": user_result.data or {},
        }

    async def order_history_page(
        self,
        chat_id: int,
        page: int = 0,
        page_size: int = 10,
    ) -> tuple[list[dict[str, Any]], bool]:
        if page < 0 or page_size <= 0:
            raise ValueError("Order history page and page size must be valid")
        offset = page * page_size
        result = await (
            self.client.table("orders")
            .select("id,product_id,quantity,total,status,transaction_id,created_at")
            .eq("chat_id", chat_id)
            .order("created_at", desc=True)
            .range(offset, offset + page_size)
            .execute()
        )
        rows = result.data or []
        has_next = len(rows) > page_size
        orders = rows[:page_size]
        product_ids = list(dict.fromkeys(order["product_id"] for order in orders if order.get("product_id")))
        if product_ids:
            products_result = await (
                self.client.table("products")
                .select("id,name")
                .in_("id", product_ids)
                .execute()
            )
            product_names = {
                product["id"]: product.get("name", "")
                for product in (products_result.data or [])
            }
            for order in orders:
                order["product_name"] = product_names.get(order.get("product_id"), "")
        return orders, has_next

    async def order_history(self, chat_id: int) -> list[dict[str, Any]]:
        orders, _ = await self.order_history_page(chat_id)
        return orders

    async def import_stock(self, product_id: str, credentials: list[str], key: str) -> int:
        rows = [
            {
                "credential_cipher": encrypt_stock(value, key),
                "credential_fingerprint": stock_fingerprint(value, key),
            }
            for value in credentials
        ]
        result = await self.client.rpc(
            "import_stock_items",
            {"p_product_id": product_id, "p_items": rows},
        ).execute()
        return int(result.data or 0)

    async def remove_stock(self, product_id: str, credentials: list[str], key: str) -> int:
        fingerprints = list(dict.fromkeys(stock_fingerprint(value, key) for value in credentials))
        if not fingerprints:
            return 0
        result = await (
            self.client.table("stock_items")
            .delete()
            .eq("product_id", product_id)
            .eq("status", "available")
            .in_("credential_fingerprint", fingerprints)
            .select("id")
            .execute()
        )
        return len(result.data or [])

    async def sales_summary(self) -> dict[str, Any]:
        result = await self.client.rpc("sales_summary").execute()
        return result.data
