import argparse
import asyncio
import os
from pathlib import Path

from dotenv import load_dotenv
from supabase import acreate_client

from app.store import Store


async def main() -> None:
    load_dotenv()
    parser = argparse.ArgumentParser(description="Import license keys into encrypted Supabase stock")
    parser.add_argument("product_id", help="UUID of the product in Supabase")
    parser.add_argument("file", type=Path, help="UTF-8 text file, one license key per line")
    args = parser.parse_args()

    key = os.environ["STOCK_ENCRYPTION_KEY"]
    credentials = [line.strip() for line in args.file.read_text(encoding="utf-8").splitlines() if line.strip()]
    if not credentials:
        raise SystemExit("Stock file must contain at least one license key")
    supabase = await acreate_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_SERVICE_ROLE_KEY"])
    store = Store(supabase)
    added = await store.import_stock(args.product_id, credentials, key)
    print(f"Imported {added} stock item(s).")
    if added and os.environ.get("BOT_TOKEN"):
        from aiogram import Bot

        from app.bot import notify_stock_added
        from app.store import Store

        bot = Bot(os.environ["BOT_TOKEN"])
        try:
            await notify_stock_added(
                bot,
                store,
                os.environ.get("STOCK_NOTIFICATION_CHAT_IDS", ""),
                args.product_id,
                added,
            )
        finally:
            await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())
