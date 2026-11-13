import asyncio
import base64
import html
import io
import json
import logging
import re
from collections.abc import Awaitable, Callable
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from typing import Any
from urllib.parse import urlsplit

import qrcode
import cloudinary.uploader
from cloudinary.exceptions import Error as CloudinaryError
from httpx import RequestError as HttpRequestError
from aiogram import Bot, Dispatcher, F, Router
from aiogram.exceptions import TelegramAPIError, TelegramBadRequest, TelegramRetryAfter
from aiogram.types import (
    BufferedInputFile,
    CallbackQuery,
    CopyTextButton,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    InputMediaPhoto,
    InputMediaVideo,
    KeyboardButton,
    Message,
    ReplyKeyboardMarkup,
)
from postgrest.exceptions import APIError as SupabaseAPIError
from urllib3.exceptions import HTTPError as CloudinaryHTTPError

from app.config import Settings
from app.payments import (
    KHQRPayGateway,
    PaymentMethod,
    aba_mobile_deeplink,
    aba_mobile_redirect_url,
)
from app.security import decrypt_stock
from app.store import Store

logger = logging.getLogger(__name__)
router = Router()
MEDIA_UPLOAD_ERRORS = (
    TelegramAPIError,
    CloudinaryError,
    CloudinaryHTTPError,
    OSError,
    RuntimeError,
    ValueError,
)
MEDIA_SAVE_ERRORS = MEDIA_UPLOAD_ERRORS + (SupabaseAPIError, HttpRequestError)


class InputValidationError(ValueError):
    pass


pending_stock_upload: dict[int, str] = {}
pending_admin_input: dict[int, tuple[str, str | None]] = {}
pending_admin_media: dict[int, dict[str, Any]] = {}
pending_wallet_amount: set[int] = set()
admin_wizards: dict[int, dict[str, Any]] = {}
broadcast_drafts: dict[int, dict[str, Any]] = {}
broadcast_cancel_events: dict[str, asyncio.Event] = {}
broadcast_tasks: dict[str, asyncio.Task[None]] = {}
broadcast_active_keys: dict[int, str] = {}
welcome_message_ids: dict[int, int] = {}
active_screen_auxiliary_message_ids: dict[int, list[int]] = {}
menu_keyboard_messages: dict[int, Message] = {}
active_screen_messages: dict[int, Message] = {}
user_language_cache: dict[int, str] = {}
welcome_media_cache: dict[str, str] | None = None
bot_full_name = ""
welcome_settings_task: asyncio.Task[None] | None = None
welcome_setup_tasks: set[asyncio.Task[None]] = set()
payment_message_ids: dict[str, tuple[int, int]] = {}
PHNOM_PENH_TIMEZONE = timezone(timedelta(hours=7), "Asia/Phnom_Penh")
CATALOG_PAGE_SIZE = 8
ADMIN_WIZARD_STEPS = {
    "product": (
        "media",
        "name",
        "description",
        "price",
        "reseller_price",
    ),
    "category": (
        "name",
        "description",
        "delivery_message",
        "delivery_media",
    ),
}
BOT_SETTING_LABELS = {
    "how_to_buy_caption": ("Default tutorial caption", "ចំណងជើងវីដេអូណែនាំលំនាំដើម"),
    "how_to_buy_caption_km": ("Tutorial caption (Khmer)", "ចំណងជើងវីដេអូណែនាំ (ខ្មែរ)"),
    "how_to_buy_caption_en": (
        "Tutorial caption (English)",
        "ចំណងជើងវីដេអូណែនាំ (អង់គ្លេស)",
    ),
    "support_username": ("Support username", "ឈ្មោះអ្នកជំនួយ"),
    "install_group_url": ("Join group URL", "តំណចូល Group"),
    "stock_notification_chat_ids": (
        "Stock notification chats (groups/channels)",
        "Group/Channel ទទួលដំណឹងស្តុក",
    ),
    "order_notification_chat_ids": (
        "Order log chats (groups/channels)",
        "Group/Channel ទទួល Order Log",
    ),
}
BOT_SETTING_SECTIONS = {
    "captions": (
        "📝 Captions",
        "📝 ចំណងជើង",
        ("how_to_buy_caption", "how_to_buy_caption_km", "how_to_buy_caption_en"),
    ),
    "shop": (
        "🔗 Store links",
        "🔗 តំណ និងជំនួយ",
        ("support_username", "install_group_url"),
    ),
    "notifications": (
        "🔔 Notifications",
        "🔔 ការជូនដំណឹង",
        ("stock_notification_chat_ids", "order_notification_chat_ids"),
    ),
}


def bot_setting_section(key: str) -> str:
    for section, (_, _, keys) in BOT_SETTING_SECTIONS.items():
        if key in keys:
            return section
    if key in {"welcome_photo_url", "welcome_video_url", "how_to_buy_video_url"}:
        return "media"
    raise ValueError(f"Unknown bot setting: {key}")


def keyboard_button_style(button: InlineKeyboardButton) -> str:
    if button.style:
        return button.style

    callback_data = (button.callback_data or "").casefold()
    text = button.text.casefold()
    callback_words = set(re.split(r"[^a-z]+", callback_data))
    text_words = set(re.findall(r"\b[a-z]+\b", text))
    danger_words = {"cancel", "stop", "disable", "remove", "delete", "clear", "discard"}
    if callback_words & danger_words or text_words & danger_words or any(
        word in text for word in ("លុប", "បិទ", "ដក", "បោះបង់", "បញ្ឈប់")
    ):
        return "danger"

    success_words = {"add", "send", "save", "confirm", "pay", "paid", "check", "submit"}
    if callback_words & success_words or text_words & success_words or any(
        word in text for word in ("បន្ថែម", "បញ្ជាក់", "រក្សាទុក", "ផ្ញើ", "ទូទាត់")
    ):
        return "success"

    return "primary"


def keyboard(rows: list[list[InlineKeyboardButton]]) -> InlineKeyboardMarkup:
    styled_rows = [
        [
            button.model_copy(update={"style": keyboard_button_style(button)})
            for button in row
        ]
        for row in rows
    ]
    return InlineKeyboardMarkup(inline_keyboard=styled_rows)


def package_id_from_start(text: str) -> str | None:
    parts = text.split(maxsplit=1)
    if not parts or parts[0].split("@", 1)[0].casefold() != "/start" or len(parts) < 2:
        return None
    payload = parts[1].strip()
    prefix = "package_"
    if not payload.startswith(prefix) or len(payload) > 64:
        return None
    if not all(
        character in "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789_-"
        for character in payload
    ):
        return None
    product_id = payload[len(prefix) :]
    return product_id or None


def stock_notification_url(bot_username: str, product_id: str) -> str:
    return f"https://t.me/{bot_username.lstrip('@')}?start=package_{product_id}"


def parse_telegram_post_link(text: str) -> tuple[int | str, int] | None:
    normalized_text = text.strip()
    if re.match(
        r"^(?:www\.)?(?:t\.me|telegram\.me)/",
        normalized_text,
        flags=re.IGNORECASE,
    ):
        normalized_text = f"https://{normalized_text}"
    try:
        parsed = urlsplit(normalized_text)
    except ValueError:
        return None
    if parsed.scheme not in {"http", "https"} or parsed.hostname not in {
        "t.me",
        "www.t.me",
        "telegram.me",
        "www.telegram.me",
    }:
        return None

    parts = [part for part in parsed.path.split("/") if part]
    if len(parts) < 2:
        return None
    if parts[0].casefold() == "s":
        parts = parts[1:]
    if len(parts) < 2:
        return None
    if len(parts[-1]) > 20:
        return None
    try:
        message_id = int(parts[-1])
    except ValueError:
        return None
    if message_id <= 0:
        return None

    if parts[0].casefold() == "c":
        if not parts[1].isdigit() or len(parts[1]) > 20:
            return None
        return int(f"-100{parts[1]}"), message_id

    if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{3,31}", parts[0]):
        return None
    return f"@{parts[0]}", message_id


TELEGRAM_CONTENT_PREFIX = "telegram-content:"


def encode_telegram_content_reference(
    content: list[dict[str, Any]], *, drop_caption: bool = False
) -> str:
    if not content:
        raise ValueError("Telegram content must contain at least one message")
    source_chat_id = content[0].get("source_chat_id")
    message_ids = [int(content[0]["message_id"])]
    media_group_id = content[0].get("media_group_id")
    for item in content[1:]:
        if (
            item.get("source_chat_id") != source_chat_id
            or not media_group_id
            or item.get("media_group_id") != media_group_id
        ):
            raise ValueError("Send one Telegram message or one album per setting")
        message_ids.append(int(item["message_id"]))
    if source_chat_id is None or any(message_id <= 0 for message_id in message_ids):
        raise ValueError("Invalid Telegram message reference")
    payload: dict[str, Any] = {
        "source_chat_id": source_chat_id,
        "message_ids": message_ids,
    }
    if drop_caption:
        payload["drop_caption"] = True
    return TELEGRAM_CONTENT_PREFIX + json.dumps(payload, separators=(",", ":"))


def decode_telegram_content_reference(
    value: str | None,
) -> tuple[int | str, list[int]] | None:
    if not value:
        return None
    if not value.startswith(TELEGRAM_CONTENT_PREFIX):
        post_link = parse_telegram_post_link(value)
        if post_link:
            source_chat_id, message_id = post_link
            return source_chat_id, [message_id]
        return None
    try:
        payload = json.loads(value.removeprefix(TELEGRAM_CONTENT_PREFIX))
        source_chat_id = payload["source_chat_id"]
        message_ids = payload["message_ids"]
    except (KeyError, TypeError, json.JSONDecodeError) as exc:
        raise ValueError("Invalid stored Telegram content reference") from exc
    if (
        not isinstance(source_chat_id, (int, str))
        or isinstance(source_chat_id, bool)
        or (isinstance(source_chat_id, str) and not source_chat_id)
        or not isinstance(message_ids, list)
        or not message_ids
        or len(message_ids) > 100
        or any(
            not isinstance(message_id, int)
            or isinstance(message_id, bool)
            or message_id <= 0
            for message_id in message_ids
        )
    ):
        raise ValueError("Invalid stored Telegram content reference")
    return source_chat_id, message_ids


def telegram_content_reference_drops_caption(value: str) -> bool:
    if not value.startswith(TELEGRAM_CONTENT_PREFIX):
        return False
    payload = json.loads(value.removeprefix(TELEGRAM_CONTENT_PREFIX))
    return payload.get("drop_caption") is True


def telegram_content_item(message: Message) -> dict[str, Any]:
    post_link = parse_telegram_post_link(message.text or "")
    if post_link:
        source_chat_id, message_id = post_link
        return {
            "message_id": message_id,
            "media_group_id": None,
            "source_chat_id": source_chat_id,
        }
    return {
        "message_id": message.message_id,
        "media_group_id": message.media_group_id,
        "source_chat_id": message.chat.id,
    }


def telegram_media_upload_item(message: Message) -> dict[str, str] | None:
    candidates = (
        ("photo", "image"),
        ("video", "video"),
        ("animation", "animation"),
        ("document", "document"),
        ("audio", "audio"),
        ("voice", "audio"),
        ("video_note", "video"),
        ("sticker", "sticker"),
    )
    for attribute, media_type in candidates:
        value = getattr(message, attribute, None)
        if not value:
            continue
        if attribute == "photo":
            value = value[-1]
        file_id = getattr(value, "file_id", None)
        if not file_id:
            continue
        mime_type = getattr(value, "mime_type", None) or ""
        if attribute == "document":
            if mime_type.startswith("image/"):
                media_type = "image"
            elif mime_type.startswith("video/"):
                media_type = "video"
            elif mime_type.startswith("audio/"):
                media_type = "audio"
        file_name = getattr(value, "file_name", None)
        if not file_name:
            file_name = f"{media_type}-{message.message_id}"
        return {
            "file_id": file_id,
            "media_type": media_type,
            "file_name": file_name,
        }
    return None


def stored_telegram_content_reference(value: str | None) -> str | None:
    if not value:
        return None
    if decode_telegram_content_reference(value):
        return value
    post_link = parse_telegram_post_link(value)
    if not post_link:
        return None
    source_chat_id, message_id = post_link
    return encode_telegram_content_reference(
        [
            {
                "message_id": message_id,
                "media_group_id": None,
                "source_chat_id": source_chat_id,
            }
        ]
    )


def cloudinary_media_urls(value: str | None) -> list[str]:
    if not value:
        return []
    return [url.strip() for url in value.splitlines() if url.strip()]


def cloudinary_media_method(url: str, fallback: str = "document") -> str:
    path = urlsplit(url).path.lower()
    suffix = path.rsplit(".", 1)[-1] if "." in path.rsplit("/", 1)[-1] else ""
    if suffix in {"gif"}:
        return "animation"
    if suffix in {"mp3", "m4a", "ogg", "oga", "wav", "flac"}:
        return "audio"
    if "/image/upload/" in path or suffix in {
        "jpg",
        "jpeg",
        "png",
        "webp",
        "bmp",
        "avif",
    }:
        return "photo"
    if "/video/upload/" in path or suffix in {"mp4", "mov", "m4v", "webm"}:
        return "video"
    if "/raw/upload/" in path:
        return "document"
    if fallback in {"photo", "video", "animation", "audio", "document"}:
        return fallback
    return "document"


async def send_cloudinary_media(
    target: Bot | Message,
    media_value: str,
    *,
    chat_id: int | None = None,
    fallback: str = "document",
    **kwargs: Any,
) -> list[Message]:
    sent: list[Message] = []
    for index, url in enumerate(cloudinary_media_urls(media_value)):
        media_method = cloudinary_media_method(url, fallback)
        method_name = (
            f"send_{media_method}" if chat_id is not None else f"answer_{media_method}"
        )
        argument = {
            "photo": "photo",
            "video": "video",
            "animation": "animation",
            "audio": "audio",
            "document": "document",
        }[media_method]
        options = dict(kwargs) if index == 0 else {}
        sender = getattr(target, method_name)
        if chat_id is not None:
            sent.append(
                await sender(chat_id=chat_id, **{argument: url}, **options)
            )
        else:
            sent.append(await sender(**{argument: url}, **options))
    return sent


def paginate(items: list[Any], page: int) -> tuple[list[Any], int, int]:
    page_count = max(1, (len(items) + CATALOG_PAGE_SIZE - 1) // CATALOG_PAGE_SIZE)
    page = max(0, min(page, page_count - 1))
    start = page * CATALOG_PAGE_SIZE
    return items[start : start + CATALOG_PAGE_SIZE], page, page_count


def pagination_row(
    page: int, page_count: int, callback_prefix: str, language: str
) -> list[InlineKeyboardButton]:
    if page_count <= 1:
        return []
    row = []
    if page > 0:
        row.append(
            InlineKeyboardButton(
                text=copy(language, "⬅️ មុន", "⬅️ Previous"),
                callback_data=f"{callback_prefix}:{page - 1}",
            )
        )
    row.append(
        InlineKeyboardButton(text=f"{page + 1} / {page_count}", callback_data="noop")
    )
    if page < page_count - 1:
        row.append(
            InlineKeyboardButton(
                text=copy(language, "បន្ទាប់ ➡️", "Next ➡️"),
                callback_data=f"{callback_prefix}:{page + 1}",
            )
        )
    return row


async def edit_text_message(
    message: Message,
    text: str,
    *,
    parse_mode: str | None = None,
    reply_markup: InlineKeyboardMarkup | None = None,
    edit_message: bool = True,
) -> Message:
    if not edit_message:
        return await message.answer(
            text, parse_mode=parse_mode, reply_markup=reply_markup
        )
    if message.text is None:
        try:
            await message.delete()
        except TelegramBadRequest:
            logger.debug("Could not delete old photo message %s", message.message_id)
        return await message.answer(
            text, parse_mode=parse_mode, reply_markup=reply_markup
        )
    return await message.edit_text(
        text, parse_mode=parse_mode, reply_markup=reply_markup
    )


async def delete_replaced_message(message: Message) -> None:
    delete = getattr(message, "delete", None)
    if delete is None:
        return
    try:
        await delete()
    except TelegramBadRequest:
        logger.debug("Could not delete replaced message %s", message.message_id)


async def delete_active_screen_auxiliary_messages(bot: Bot, user_id: int) -> None:
    for message_id in active_screen_auxiliary_message_ids.pop(user_id, []):
        try:
            await bot.delete_message(user_id, message_id)
        except TelegramBadRequest:
            logger.debug("Could not delete auxiliary screen message %s", message_id)


async def clear_active_screen(user_id: int, keep_message_id: int | None = None) -> None:
    previous = active_screen_messages.pop(user_id, None)
    menu_message = menu_keyboard_messages.get(user_id)
    if (
        previous
        and previous.message_id != keep_message_id
        and (menu_message is None or previous.message_id != menu_message.message_id)
    ):
        if active_screen_auxiliary_message_ids.get(user_id):
            await delete_active_screen_auxiliary_messages(previous.bot, user_id)
        await delete_replaced_message(previous)
        if welcome_message_ids.get(user_id) == previous.message_id:
            welcome_message_ids.pop(user_id, None)


async def switch_active_screen(
    user_id: int,
    sent: Message,
    input_message: Message | None = None,
) -> Message:
    previous = active_screen_messages.pop(user_id, None)
    menu_message = menu_keyboard_messages.get(user_id)
    if (
        previous
        and previous.message_id != sent.message_id
        and (menu_message is None or previous.message_id != menu_message.message_id)
    ):
        if active_screen_auxiliary_message_ids.get(user_id):
            await delete_active_screen_auxiliary_messages(previous.bot, user_id)
        await delete_replaced_message(previous)
        if welcome_message_ids.get(user_id) == previous.message_id:
            welcome_message_ids.pop(user_id, None)
    if (
        input_message
        and input_message.from_user
        and input_message.from_user.id == user_id
    ):
        await delete_replaced_message(input_message)
    active_screen_messages[user_id] = sent
    return sent


async def send_replacing_screen(
    message: Message,
    user_id: int,
    text: str,
    *,
    parse_mode: str | None = None,
    reply_markup: InlineKeyboardMarkup | ReplyKeyboardMarkup | None = None,
) -> Message:
    sent = await message.answer(text, parse_mode=parse_mode, reply_markup=reply_markup)
    return await switch_active_screen(user_id, sent, message)


async def update_active_screen(
    user_id: int,
    text: str,
    *,
    input_message: Message | None = None,
    parse_mode: str | None = None,
    reply_markup: InlineKeyboardMarkup | ReplyKeyboardMarkup | None = None,
) -> Message | None:
    previous = active_screen_messages.get(user_id)
    if previous is None:
        if input_message is None:
            return None
        sent = await input_message.answer(
            text, parse_mode=parse_mode, reply_markup=reply_markup
        )
    else:
        sent = await edit_text_message(
            previous,
            text,
            parse_mode=parse_mode,
            reply_markup=reply_markup,
        )
    return await switch_active_screen(user_id, sent, input_message)


def money(amount: Any) -> str:
    return f"${Decimal(str(amount)):.2f}"


def is_wallet_schema_missing(error: Exception) -> bool:
    message = str(error)
    return "PGRST205" in message and (
        "wallet_deposits" in message or "user_wallets" in message
    )


def wallet_error_message(language: str) -> str:
    return copy(
        language,
        "មិនអាចប្រើ Wallet បានទេនៅពេលនេះ។ សូមព្យាយាមម្តងទៀតពេលក្រោយ។",
        "Wallet is temporarily unavailable. Please try again later.",
    )


def parse_wallet_deposit_amount(amount_text: str, language: str = "km") -> Decimal:
    try:
        amount = Decimal(amount_text.strip())
    except InvalidOperation:
        raise ValueError(
            copy(
                language,
                "ចំនួនទឹកប្រាក់មិនត្រឹមត្រូវទេ។",
                "Enter a valid deposit amount.",
            )
        ) from None
    try:
        has_two_decimal_places = amount.quantize(Decimal("0.01")) == amount
    except InvalidOperation:
        has_two_decimal_places = False
    if (
        not amount.is_finite()
        or amount < Decimal("1.00")
        or amount > Decimal("500.00")
        or not has_two_decimal_places
    ):
        raise ValueError(
            copy(
                language,
                "ចំនួនត្រូវតែចាប់ពី $1 ដល់ $500 និងមានអតិបរមា 2 ខ្ទង់ទសភាគ។",
                "Amount must be $1–$500 with at most 2 decimal places.",
            )
        )
    return amount


def payment_caption(
    product_name: str,
    quantity: int,
    amount: Any,
    language: str = "en",
) -> str:
    total = f"{Decimal(str(amount)):.2f}"
    return (
        f"💳 <b>{copy(language, 'ទូទាត់ $' + total, 'Pay $' + total)}</b>\n\n"
        f"🛍 <b>{copy(language, 'ផលិតផល៖', 'Product:')}</b> {html.escape(product_name)} x{quantity}\n"
        f"💰 <b>{copy(language, 'សរុប៖', 'Total:')}</b> ${total}\n"
        f"🟢 <b>{copy(language, 'នៅសល់ត្រូវបង់៖', 'Remaining to pay:')}</b> ${total}\n"
        f"⏱ <b>{copy(language, 'ស្កេន KHQR ដើម្បីបញ្ចប់ការទូទាត់។', 'Scan KHQR to complete payment.')}</b>\n"
        f"⏰ <b>{copy(language, 'អ្នកមានពេល 10 នាទីដើម្បីទូទាត់។ QR code នឹងផុតកំណត់ដោយស្វ័យប្រវត្តិ។', 'You have 10 minutes to pay. The QR refreshes itself.')}</b>"
    )


def normalize_language(language: str | None) -> str:
    code = (language or "km").strip().lower()
    if code in {"km", "km-kh", "kh", "khmer"}:
        return "km"
    if code.startswith("en"):
        return "en"
    return "km"


async def warm_welcome_media_settings(store: Store) -> None:
    global welcome_media_cache
    try:
        welcome_media_cache = await store.bot_settings(
            ("welcome_video_url", "welcome_photo_url")
        )
    except Exception:
        logger.exception("Could not preload welcome media settings")


def schedule_welcome_media_preload(store: Store) -> asyncio.Task[None]:
    global welcome_settings_task
    if welcome_settings_task is None or welcome_settings_task.done():
        welcome_settings_task = asyncio.create_task(warm_welcome_media_settings(store))
    return welcome_settings_task


def copy(language: str | None, khmer: str, english: str) -> str:
    return english if normalize_language(language) == "en" else khmer


def language_choice_keyboard() -> InlineKeyboardMarkup:
    return keyboard(
        [
            [InlineKeyboardButton(text="🇰🇭 ភាសាខ្មែរ", callback_data="lang:km")],
            [InlineKeyboardButton(text="🇺🇸 English", callback_data="lang:en")],
        ]
    )


def first_language_prompt() -> str:
    return "🌐 សូមជ្រើសរើសភាសាជាមុនសិន៖\nPlease choose your language first:"


def order_history_text(orders: list[dict[str, Any]], language: str = "km") -> str:
    if not orders:
        return copy(language, "អ្នកមិនទាន់មានការបញ្ជាទិញទេ។", "You have no orders yet.")

    status_labels = {
        "pending": copy(language, "កំពុងរង់ចាំ", "Pending"),
        "paid": copy(language, "បានទូទាត់", "Paid"),
        "expired": copy(language, "ផុតកំណត់", "Expired"),
        "failed": copy(language, "បរាជ័យ", "Failed"),
        "cancelled": copy(language, "បានលុបចោល", "Cancelled"),
    }
    lines = []
    for order in orders:
        created_at = str(order.get("created_at") or "")
        try:
            timestamp = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
            date_text = timestamp.astimezone(timezone.utc).strftime(
                "%Y-%m-%d %H:%M UTC"
            )
        except ValueError:
            date_text = created_at or copy(language, "មិនស្គាល់", "Unknown")
        product_name = html.escape(
            str(order.get("product_name") or copy(language, "មិនស្គាល់", "Unknown"))
        )
        status = html.escape(
            status_labels.get(order.get("status"), str(order.get("status") or "—"))
        )
        quantity = order.get("quantity", 1)
        lines.append(
            f"📅 {html.escape(date_text)}\n"
            f"📦 {product_name} ×{quantity} · {money(order.get('total', 0))}\n"
            f"📌 {copy(language, 'ស្ថានភាព', 'Status')}: {status}"
        )
    return (
        f"🛒 <b>{copy(language, 'ការបញ្ជាទិញរបស់ខ្ញុំ', 'My Orders')}</b>\n"
        "───────────────────\n" + "\n\n".join(lines)
    )


def order_history_keyboard(
    page: int,
    has_next: bool,
    language: str = "km",
) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    navigation: list[InlineKeyboardButton] = []
    if page > 0:
        navigation.append(
            InlineKeyboardButton(
                text=copy(language, "⏮️ មុន", "⏮️ Prev"),
                callback_data=f"history:page:{page - 1}",
            )
        )
    if has_next:
        navigation.append(
            InlineKeyboardButton(
                text=copy(language, "បន្ទាប់ ⏭️", "Next ⏭️"),
                callback_data=f"history:page:{page + 1}",
            )
        )
    if navigation:
        rows.append(navigation)
    rows.append(back_button(language=language))
    return keyboard(rows)


def broadcast_buttons_markup(
    buttons: list[dict[str, str]],
) -> InlineKeyboardMarkup | None:
    if not buttons:
        return None
    return keyboard(
        [
            [InlineKeyboardButton(text=button["text"], url=button["url"])]
            for button in buttons
        ]
    )


def broadcast_draft_keyboard(
    stage: str = "content", language: str = "km"
) -> InlineKeyboardMarkup:
    if stage == "content":
        rows = [
            [
                InlineKeyboardButton(
                    text=copy(language, "✅ មើល Preview", "✅ Preview"),
                    callback_data="admin:broadcast:preview",
                ),
                InlineKeyboardButton(
                    text=copy(language, "🔗 បន្ថែម Buttons", "🔗 Add buttons"),
                    callback_data="admin:broadcast:buttons",
                ),
            ],
            [
                InlineKeyboardButton(
                    text=copy(language, "❌ បោះបង់", "❌ Cancel"),
                    callback_data="admin:broadcast:cancel",
                )
            ],
        ]
    elif stage == "preview":
        rows = [
            [
                InlineKeyboardButton(
                    text=copy(language, "📣 ផ្ញើឥឡូវ", "📣 Send now"),
                    callback_data="admin:broadcast:send",
                ),
                InlineKeyboardButton(
                    text=copy(language, "🕒 កំណត់ពេល", "🕒 Schedule"),
                    callback_data="admin:broadcast:schedule",
                ),
            ],
            [
                InlineKeyboardButton(
                    text=copy(language, "🔗 កែ Buttons", "🔗 Edit buttons"),
                    callback_data="admin:broadcast:buttons",
                ),
                InlineKeyboardButton(
                    text=copy(language, "➕ បន្ថែមមាតិកា", "➕ Add content"),
                    callback_data="admin:broadcast:continue",
                ),
            ],
            [
                InlineKeyboardButton(
                    text=copy(language, "❌ បោះបង់", "❌ Cancel"),
                    callback_data="admin:broadcast:cancel",
                )
            ],
        ]
    else:
        rows = [
            [
                InlineKeyboardButton(
                    text=copy(language, "❌ បោះបង់", "❌ Cancel"),
                    callback_data="admin:broadcast:cancel",
                )
            ]
        ]
    return keyboard(rows)


async def telegram_call_with_retry(request: Callable[[], Awaitable[Any]]) -> Any:
    try:
        return await request()
    except TelegramRetryAfter as exc:
        await asyncio.sleep(float(exc.retry_after))
        return await request()


def parse_broadcast_buttons(text: str) -> list[dict[str, str]]:
    if text.strip() == "-":
        return []
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if not lines or len(lines) > 8:
        raise ValueError("Enter between 1 and 8 button links, or send - for none.")
    buttons: list[dict[str, str]] = []
    for line in lines:
        label, separator, url = line.partition("|")
        label = label.strip()
        url = url.strip()
        parsed = urlsplit(url)
        if (
            not separator
            or not label
            or len(label) > 64
            or parsed.scheme not in {"https", "http"}
            or not parsed.netloc
        ):
            raise ValueError("Use one `Button name | https://example.com` per line.")
        buttons.append({"text": label, "url": url})
    return buttons


async def broadcast_destinations(
    store: Store,
) -> list[int | str]:
    users = await store.stock_notification_users()
    configured = await store.bot_settings(
        ("stock_notification_chat_ids", "order_notification_chat_ids")
    )
    destinations: dict[str, int | str] = {}
    for user in users:
        telegram_id = int(user["telegram_id"])
        destinations[str(telegram_id)] = telegram_id
    for setting_key in ("stock_notification_chat_ids", "order_notification_chat_ids"):
        for raw_chat_id in configured.get(setting_key, "").split(","):
            value = raw_chat_id.strip()
            if not value:
                continue
            try:
                chat_id: int | str = int(value)
            except ValueError:
                if not value.startswith("@") or len(value) < 2:
                    logger.warning("Ignoring invalid broadcast chat ID %r", value)
                    continue
                chat_id = value
            destinations[str(chat_id)] = chat_id
    return list(destinations.values())


async def copy_broadcast_content(
    bot: Bot,
    chat_id: int | str,
    source_chat_id: int | str,
    content: list[dict[str, Any]],
    buttons: list[dict[str, str]],
) -> list[int]:
    copied_ids: list[int] = []
    source_groups: list[tuple[int | str, list[int]]] = []
    for item in content:
        item_source = item.get("source_chat_id", source_chat_id)
        if not source_groups or source_groups[-1][0] != item_source:
            source_groups.append((item_source, []))
        source_groups[-1][1].append(int(item["message_id"]))

    for item_source, message_ids in source_groups:
        copied = await telegram_call_with_retry(
            lambda item_source=item_source, message_ids=message_ids: bot.copy_messages(
                chat_id=chat_id,
                from_chat_id=item_source,
                message_ids=sorted(message_ids),
            )
        )
        copied_ids.extend(int(item.message_id) for item in copied)

    markup = broadcast_buttons_markup(buttons)
    if markup:
        button_message = await telegram_call_with_retry(
            lambda: bot.send_message(
                chat_id,
                "🔗 Choose a link below / សូមជ្រើសរើសតំណខាងក្រោម",
                reply_markup=markup,
            )
        )
        copied_ids.append(button_message.message_id)
    return copied_ids


async def copy_telegram_content_reference(
    bot: Bot, chat_id: int | str, reference: str
) -> list[Any]:
    decoded = decode_telegram_content_reference(reference)
    if not decoded:
        raise ValueError("Not a Telegram content reference")
    source_chat_id, message_ids = decoded
    if telegram_content_reference_drops_caption(reference) and len(message_ids) == 1:
        copied = await telegram_call_with_retry(
            lambda: bot.copy_message(
                chat_id=chat_id,
                from_chat_id=source_chat_id,
                message_id=message_ids[0],
                caption="",
            )
        )
        return [copied]
    copied = await telegram_call_with_retry(
        lambda: bot.copy_messages(
            chat_id=chat_id,
            from_chat_id=source_chat_id,
            message_ids=sorted(message_ids),
        )
    )
    return list(copied)


async def delete_broadcast_source(
    bot: Bot, source_chat_id: int, content: list[dict[str, Any]]
) -> None:
    for item in content:
        if item.get("source_chat_id", source_chat_id) != source_chat_id:
            continue
        try:
            await bot.delete_message(source_chat_id, int(item["message_id"]))
        except TelegramBadRequest:
            logger.debug(
                "Could not delete broadcast source message %s", item.get("message_id")
            )


async def execute_broadcast(
    bot: Bot,
    store: Store,
    source_chat_id: int,
    content: list[dict[str, Any]],
    buttons: list[dict[str, str]],
    progress_message: Message,
    cancel_event: asyncio.Event,
) -> tuple[int, int, bool]:
    destinations = await broadcast_destinations(store)
    total = len(destinations)
    sent = 0
    failed = 0
    cancelled = False

    async def update_progress(done: int, final: bool = False) -> None:
        markup = None
        if not final:
            markup = keyboard(
                [
                    [
                        InlineKeyboardButton(
                            text="⏹ Cancel", callback_data="admin:broadcast:stop"
                        )
                    ]
                ]
            )
        try:
            await progress_message.edit_text(
                f"📣 <b>Broadcast / ការផ្សព្វផ្សាយ</b>\n"
                f"✅ Sent / បានផ្ញើ: {sent}\n"
                f"❌ Failed / បរាជ័យ: {failed}\n"
                f"📊 Progress / វឌ្ឍនភាព: {done}/{total}",
                parse_mode="HTML",
                reply_markup=markup,
            )
        except TelegramBadRequest:
            logger.debug("Could not update broadcast progress message")

    await update_progress(0)
    for index, destination in enumerate(destinations, start=1):
        if cancel_event.is_set():
            cancelled = True
            break
        try:
            await copy_broadcast_content(
                bot,
                destination,
                source_chat_id,
                content,
                buttons,
            )
            sent += 1
        except Exception:
            logger.exception(
                "Broadcast delivery failed for destination %s", destination
            )
            failed += 1
        if index % 20 == 0 or index == total:
            await update_progress(index)
        await asyncio.sleep(0.1)

    done = sent + failed
    await update_progress(done, final=True)
    final_text = (
        (
            "📣 <b>Broadcast cancelled / បានបញ្ឈប់ការផ្សព្វផ្សាយ</b>\n"
            if cancelled
            else "📣 <b>Broadcast complete / ផ្ញើចប់ហើយ</b>\n"
        )
        + f"✅ Sent / បានផ្ញើ: {sent}\n❌ Failed / បរាជ័យ: {failed}\n📊 Total / សរុប: {total}"
    )
    try:
        await progress_message.edit_text(final_text, parse_mode="HTML")
    except TelegramBadRequest:
        logger.debug("Could not publish final broadcast report")
    return sent, failed, cancelled


def welcome_text(
    user: Any,
    language: str = "km",
    categories: list[dict[str, Any]] | None = None,
    bot_name: str | None = None,
) -> str:
    full_name = " ".join(
        part.strip()
        for part in (
            getattr(user, "first_name", None),
            getattr(user, "last_name", None),
        )
        if part and part.strip()
    )
    full_name = html.escape(full_name or "—")
    shop_name = html.escape(bot_name if bot_name is not None else bot_full_name)
    if normalize_language(language) == "en":
        greeting = f"Welcome to {shop_name}!" if shop_name else "Welcome!"
        return (
            f"💎 <b>{greeting}</b>\n\n"
            "📋 <b>Account information</b>\n"
            f"└ Name: {full_name}\n\n"
            "🔑 Buy digital keys, codes, and other digital products quickly and securely.\n\n"
            "Choose a product from the menu below to get started."
        )
    greeting = f"សូមស្វាគមន៍មកកាន់ {shop_name}!" if shop_name else "សូមស្វាគមន៍!"
    return (
        f"💎 <b>{greeting}</b>\n\n"
        "📋 <b>ព័ត៌មានគណនី</b>\n"
        f"└ ឈ្មោះ: {full_name}\n\n"
        "🔑 ទិញ Digital Key, Code និងផលិតផល Digital ផ្សេងៗបានងាយស្រួល រហ័ស និងមានសុវត្ថិភាព។\n\n"
        "សូមជ្រើសរើសផលិតផលពីម៉ឺនុយខាងក្រោម ដើម្បីចាប់ផ្តើម។"
    )


async def load_bot_full_name(bot: Bot) -> str:
    global bot_full_name
    bot_user = await bot.get_me()
    bot_full_name = " ".join(
        part.strip()
        for part in (bot_user.first_name, bot_user.last_name)
        if part and part.strip()
    )
    if not bot_full_name:
        raise ValueError("Telegram bot profile does not have a full name")
    return bot_full_name


def account_text(
    user: Any,
    balance: Any,
    language: str = "km",
    is_reseller: bool = False,
) -> str:
    full_name = " ".join(
        part.strip()
        for part in (getattr(user, "first_name", ""), getattr(user, "last_name", ""))
        if part and part.strip()
    )
    username = (
        f"@{html.escape(user.username)}"
        if getattr(user, "username", None)
        else copy(language, "មិនមាន", "Not set")
    )
    account_type = copy(
        language,
        "អ្នកលក់បន្ត" if is_reseller else "អតិថិជន",
        "Reseller" if is_reseller else "Customer",
    )
    return (
        f"👤 <b>{copy(language, 'ព័ត៌មានគណនី', 'Account')}</b>\n\n"
        f"• {copy(language, 'ឈ្មោះពេញ', 'Full name')}: {html.escape(full_name or copy(language, 'មិនមាន', 'Not set'))}\n"
        f"• {copy(language, 'ឈ្មោះអ្នកប្រើ', 'Username')}: {username}\n"
        f"• {copy(language, 'ប្រភេទគណនី', 'Account type')}: {account_type}\n"
        f"• {copy(language, 'សមតុល្យ', 'Balance')}: <b>{money(balance)}</b>"
    )


def quantity_limit(stock: int) -> int:
    return max(0, min(3, stock))


def stock_button_style(stock: int) -> str:
    return "success" if stock > 0 else "danger"


def checkout_payment_rows(
    product_id: str,
    quantity: int,
    total: Decimal,
    wallet_balance: Decimal,
    language: str,
    payment_methods: tuple[PaymentMethod, ...] = ("aba",),
) -> list[list[InlineKeyboardButton]]:
    rows: list[list[InlineKeyboardButton]] = []
    if wallet_balance >= total:
        rows.append(
            [
                InlineKeyboardButton(
                    text=f"💰 {copy(language, 'បង់តាមសមតុល្យ', 'Pay via balance')} • {money(total)}",
                    callback_data=f"bw:{product_id}:{quantity}",
                    style="success",
                )
            ]
        )
    for payment_method in payment_methods:
        method_label = "ABA" if payment_method == "aba" else "Bakong"
        rows.append(
            [
                InlineKeyboardButton(
                    text=f"💳 {copy(language, 'បង់តាម', 'Pay via')} {method_label} • {money(total)}",
                    callback_data=f"b:{payment_method}:{product_id}:{quantity}",
                    style="success",
                )
            ]
        )
    return rows


def parse_product_caption(
    caption: str, language: str = "km"
) -> tuple[str, str, str, str]:
    fields = [field.strip() for field in caption.split("|", 3)]
    if len(fields) < 2 or not fields[0] or not fields[1]:
        raise ValueError(
            copy(
                language,
                "សូមប្រើទម្រង់ ឈ្មោះ | តម្លៃធម្មតា | តម្លៃ Reseller | ព័ត៌មានលម្អិត។",
                "Use the format Name | regular price | reseller price | Details.",
            )
        )
    try:
        price = Decimal(fields[1])
    except InvalidOperation:
        raise ValueError(
            copy(language, "តម្លៃមិនត្រឹមត្រូវទេ។", "The price is invalid.")
        ) from None
    if not price.is_finite() or price <= 0:
        raise ValueError(
            copy(language, "តម្លៃត្រូវតែធំជាង 0។", "The price must be greater than 0.")
        )
    reseller_price = price
    description = ""
    if len(fields) == 4:
        try:
            reseller_price = Decimal(fields[2])
        except InvalidOperation:
            raise ValueError(
                copy(
                    language,
                    "តម្លៃ Reseller មិនត្រឹមត្រូវទេ។",
                    "The reseller price is invalid.",
                )
            ) from None
        if not reseller_price.is_finite() or reseller_price <= 0:
            raise ValueError(
                copy(
                    language,
                    "តម្លៃ Reseller ត្រូវតែធំជាង 0។",
                    "The reseller price must be greater than 0.",
                )
            )
        description = fields[3]
    elif len(fields) == 3:
        description = fields[2]
    return fields[0], str(price), str(reseller_price), description


def product_price(product_data: dict[str, Any], is_reseller: bool = False) -> Decimal:
    price = product_data["price"]
    if is_reseller:
        price = product_data.get("reseller_price", price)
    return Decimal(str(price))


def parse_stock_add(text: str, language: str = "km") -> tuple[str, list[str]] | None:
    header, separator, stock_text = text.partition("|")
    parts = header.strip().split()
    if not parts or parts[0].lower() != "addstock":
        return None
    if len(parts) != 2 or not separator:
        raise InputValidationError(
            copy(
                language,
                "សូមប្រើទម្រង់ addstock <product_id> | key1, key2។",
                "Use the format addstock <product_id> | key1, key2.",
            )
        )
    credentials = [
        item.strip()
        for item in stock_text.replace("\n", ",").split(",")
        if item.strip()
    ]
    if not credentials:
        raise InputValidationError(
            copy(
                language,
                "សូមបញ្ចូល Digital Key ឬ Code យ៉ាងហោចណាស់មួយ។",
                "Enter at least one digital key or code.",
            )
        )
    return parts[1], credentials


def description_preview(description: str, language: str = "km") -> str:
    lines = [line.strip() for line in description.splitlines() if line.strip()]
    if not lines:
        return copy(language, "មិនមានព័ត៌មានបន្ថែម។", "No additional details.")
    if len(lines) > 3:
        lines = [*lines[:3], "• ..."]
    return html.escape("\n".join(lines))


def payment_qr(payment: dict[str, Any]) -> BufferedInputFile | str:
    qr_string = payment.get("qr_string")
    if qr_string:
        image = qrcode.make(str(qr_string))
        output = io.BytesIO()
        image.save(output, format="PNG")
        return BufferedInputFile(output.getvalue(), filename="payment-qr.png")
    image_url = payment.get("qr_image_url")
    if image_url:
        return image_url
    image_data = (
        payment.get("qr_image_base64")
        or payment.get("image_base64")
        or payment.get("qr_image")
    )
    if image_data:
        if image_data.startswith("data:image"):
            image_data = image_data.split(",", 1)[-1]
        return BufferedInputFile(
            base64.b64decode(image_data), filename="payment-qr.png"
        )
    payload = (
        payment.get("qr_payload")
        or payment.get("qr_string")
        or payment.get("qr_code")
        or payment.get("qr_data")
        or payment.get("qr")
        or payment.get("khqr")
    )
    if not payload:
        raise ValueError("KHQRPay response is missing QR data")
    image = qrcode.make(str(payload))
    output = io.BytesIO()
    image.save(output, format="PNG")
    return BufferedInputFile(output.getvalue(), filename="payment-qr.png")


async def upload_product_image(bot: Bot, settings: Settings, file_id: str) -> str:
    return await upload_product_media(bot, settings, file_id, "image")


async def upload_product_video(bot: Bot, settings: Settings, file_id: str) -> str:
    return await upload_product_media(bot, settings, file_id, "video")


def cloudinary_is_configured(settings: Settings) -> bool:
    return all(
        (
            settings.cloudinary_cloud_name,
            settings.cloudinary_api_key,
            settings.cloudinary_api_secret,
        )
    )


async def upload_product_media(
    bot: Bot, settings: Settings, file_id: str, media_type: str
) -> str:
    if media_type not in {"image", "video"}:
        raise ValueError("Unsupported product media type")
    return await upload_cloudinary_file(
        bot, settings, file_id, "products", media_type
    )


async def upload_cloudinary_file(
    bot: Bot,
    settings: Settings,
    file_id: str,
    folder: str,
    resource_type: str = "auto",
    file_name: str | None = None,
) -> str:
    if not cloudinary_is_configured(settings):
        raise RuntimeError("Cloudinary credentials are not configured")
    if resource_type not in {"auto", "image", "video", "raw"}:
        raise ValueError("Unsupported Cloudinary resource type")
    media_stream = io.BytesIO()
    if file_name:
        media_stream.name = file_name
    await bot.download(file_id, destination=media_stream)
    media_stream.seek(0)
    result = await asyncio.to_thread(
        cloudinary.uploader.upload,
        media_stream,
        cloud_name=settings.cloudinary_cloud_name,
        api_key=settings.cloudinary_api_key,
        api_secret=settings.cloudinary_api_secret,
        resource_type=resource_type,
        folder=folder,
    )
    secure_url = result.get("secure_url")
    if not isinstance(secure_url, str) or not secure_url.startswith("https://"):
        raise ValueError("Cloudinary did not return a secure HTTPS URL")
    return secure_url


async def upload_telegram_media_items(
    bot: Bot,
    settings: Settings,
    items: list[dict[str, str]],
    folder: str,
) -> str:
    urls = []
    for item in items:
        file_id = item.get("file_id")
        if file_id:
            url = await upload_cloudinary_file(
                bot,
                settings,
                file_id,
                folder,
                resource_type="auto",
                file_name=item.get("file_name"),
            )
        else:
            url = item.get("url")
        if not url:
            raise ValueError("Media item has neither a Telegram file nor a URL")
        urls.append(url)
    if not urls:
        raise ValueError("No media was provided for Cloudinary upload")
    return "\n".join(urls)


async def send_welcome_content(
    target: Bot | Message,
    chat_id: int,
    text: str,
    media_value: str | None,
    fallback: str,
    reply_markup: ReplyKeyboardMarkup,
    *,
    via_bot: bool = False,
) -> tuple[Message, list[int]]:
    reference = stored_telegram_content_reference(media_value)
    if reference:
        media_bot = target if via_bot else target.bot
        copied = await copy_telegram_content_reference(
            media_bot, chat_id, reference
        )
        if via_bot:
            sent = await target.send_message(
                chat_id,
                text,
                parse_mode="HTML",
                reply_markup=reply_markup,
            )
        else:
            sent = await target.answer(
                text,
                parse_mode="HTML",
                reply_markup=reply_markup,
            )
        return sent, [item.message_id for item in copied]

    media = await send_cloudinary_media(
        target,
        media_value or "",
        chat_id=chat_id if via_bot else None,
        fallback=fallback,
        caption=text,
        parse_mode="HTML",
        reply_markup=reply_markup,
    )
    if media:
        return media[0], [item.message_id for item in media[1:]]
    if via_bot:
        sent = await target.send_message(
            chat_id,
            text,
            parse_mode="HTML",
            reply_markup=reply_markup,
        )
    else:
        sent = await target.answer(
            text,
            parse_mode="HTML",
            reply_markup=reply_markup,
        )
    return sent, []


def menu(
    settings: Settings, user_id: int, language: str = "km"
) -> InlineKeyboardMarkup:
    labels = {
        "shop": copy(language, "🛍 ហាងលក់", "🛍 Shop"),
        "orders": copy(language, "🛒 ការបញ្ជាទិញ", "🛒 My orders"),
        "guide": copy(language, "🎥 របៀបទិញ", "🎥 How to buy"),
        "language": copy(language, "🌐 ភាសា", "🌐 Language"),
        "support": copy(language, "💬 ជំនួយ", "💬 Support"),
    }
    rows = [
        [
            InlineKeyboardButton(
                text=labels["shop"], callback_data="shop", style="primary"
            )
        ],
        [
            InlineKeyboardButton(
                text=labels["orders"], callback_data="history", style="primary"
            )
        ],
        [InlineKeyboardButton(text=labels["guide"], callback_data="how_to_buy")],
        [
            InlineKeyboardButton(text=labels["language"], callback_data="language"),
            InlineKeyboardButton(text=labels["support"], callback_data="support"),
        ],
    ]
    if user_id in settings.admin_ids:
        rows.append(
            [
                InlineKeyboardButton(
                    text=copy(language, "⚙️ គ្រប់គ្រងហាង", "⚙️ Store admin"),
                    callback_data="admin",
                )
            ]
        )
    return keyboard(rows)


def welcome_keyboard(user_id: int, settings: Settings) -> ReplyKeyboardMarkup:
    rows = []
    if user_id in settings.admin_ids:
        rows.append([KeyboardButton(text="⚙️ ADMIN", style="primary")])
    rows.extend(
        [
            [
                KeyboardButton(text="👤 Account", style="primary"),
                KeyboardButton(text="💰 Add funds", style="success"),
            ],
            [KeyboardButton(text="🛍 Digital Products", style="primary")],
        ]
    )
    return ReplyKeyboardMarkup(keyboard=rows, resize_keyboard=True, is_persistent=True)


def account_keyboard(language: str = "km") -> InlineKeyboardMarkup:
    return keyboard(
        [
            [
                InlineKeyboardButton(
                    text=copy(language, "🛒 ការបញ្ជាទិញរបស់ខ្ញុំ", "🛒 My Orders"),
                    callback_data="history",
                ),
                InlineKeyboardButton(
                    text=copy(language, "🎥 របៀបទិញ", "🎥 How to Buy"),
                    callback_data="how_to_buy",
                ),
            ],
            [
                InlineKeyboardButton(
                    text=copy(language, "🔗 ចូល Group", "🔗 Join Group"),
                    callback_data="join_group",
                )
            ],
            [
                InlineKeyboardButton(
                    text=copy(language, "🌐 ភាសា", "🌐 Language"),
                    callback_data="language",
                ),
                InlineKeyboardButton(
                    text=copy(language, "💬 ជំនួយ", "💬 Support"),
                    callback_data="support",
                ),
            ],
            [
                InlineKeyboardButton(
                    text=copy(language, "💰 បញ្ចូលប្រាក់", "💰 Add funds"),
                    callback_data="wallet:add",
                )
            ],
        ]
    )


def back_button(
    target: str = "home", language: str = "km"
) -> list[InlineKeyboardButton]:
    return [
        InlineKeyboardButton(
            text=copy(language, "⬅️ ត្រឡប់", "⬅️ Back"),
            callback_data=target,
        )
    ]


def admin_category_actions(
    category_id: str, language: str = "km"
) -> list[list[InlineKeyboardButton]]:
    return [
        [
            InlineKeyboardButton(
                text=copy(language, "➕ បន្ថែម Package", "➕ Add package"),
                callback_data=f"admin:product-add:{category_id}",
            )
        ],
        [
            InlineKeyboardButton(
                text=copy(language, "✏️ កែ Catalog", "✏️ Edit catalog"),
                callback_data=f"admin:category-edit:{category_id}",
            ),
            InlineKeyboardButton(
                text=copy(language, "🗑 បិទ Catalog", "🗑 Disable catalog"),
                callback_data=f"admin:category-disable:{category_id}",
                style="danger",
            ),
        ],
        back_button("admin:catalogs", language),
    ]


def admin_product_actions(
    product_id: str, category_id: str, language: str = "km"
) -> list[list[InlineKeyboardButton]]:
    return [
        [
            InlineKeyboardButton(
                text=copy(language, "✏️ កែ Package", "✏️ Edit package"),
                callback_data=f"admin:product-edit:{product_id}",
            ),
            InlineKeyboardButton(
                text=copy(language, "🗑 បិទ Package", "🗑 Disable package"),
                callback_data=f"admin:product-disable:{product_id}",
                style="danger",
            ),
        ],
        [
            InlineKeyboardButton(
                text=copy(language, "➕ បន្ថែមស្តុក", "➕ Add stock"),
                callback_data=f"admin:stock-add:{product_id}",
                style="success",
            ),
            InlineKeyboardButton(
                text=copy(language, "➖ ដកស្តុក", "➖ Remove stock"),
                callback_data=f"admin:stock-remove:{product_id}",
                style="danger",
            ),
        ],
        [
            InlineKeyboardButton(
                text=copy(language, "📥 បញ្ចូលស្តុក (.txt)", "📥 Import stock (.txt)"),
                callback_data=f"au:{product_id}",
            )
        ],
        back_button(f"ac:{category_id}", language),
    ]


def payment_keyboard(
    order_id: str,
    open_url: str | None,
    language: str = "km",
    payment_method: PaymentMethod = "aba",
) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    if open_url:
        open_label_km = (
            "🏦 បើកក្នុង ABA Mobile"
            if payment_method == "aba"
            else "🔗 បើកទំព័រទូទាត់"
        )
        open_label_en = (
            "🏦 Open in ABA Mobile"
            if payment_method == "aba"
            else "🔗 Open payment page"
        )
        rows.append(
            [
                InlineKeyboardButton(
                    text=copy(language, open_label_km, open_label_en),
                    url=open_url,
                    style="primary",
                )
            ]
        )
    rows.append(
        [
            InlineKeyboardButton(
                text=copy(
                    language,
                    "✅ ខ្ញុំបានទូទាត់ · ពិនិត្យ",
                    "✅ I have paid · Check",
                ),
                callback_data=f"check_payment_{order_id}",
                style="success",
            )
        ]
    )
    return keyboard(rows)


def wallet_deposit_keyboard(language: str = "km") -> InlineKeyboardMarkup:
    amounts = ("1", "5", "10", "15", "20", "50")
    rows = [
        [
            InlineKeyboardButton(
                text=f"${amount}",
                callback_data=f"wallet:amount:{amount}",
            )
            for amount in amounts[index : index + 3]
        ]
        for index in range(0, len(amounts), 3)
    ]
    rows.extend(
        [
            [
                InlineKeyboardButton(
                    text=copy(language, "✏️ ចំនួនផ្សេង", "✏️ Custom amount"),
                    callback_data="wallet:custom",
                )
            ],
            back_button("home", language),
        ]
    )
    return keyboard(rows)


def wallet_custom_amount_prompt(language: str) -> str:
    return copy(
        language,
        "សូមបញ្ចូលចំនួនទឹកប្រាក់ពី $1 ដល់ $500 (ឧ. 12.50)។ Bot នឹងបង្កើត QR តាមចំនួននេះ។",
        "Enter an amount from $1 to $500 (for example, 12.50). The QR will be generated for this amount.",
    )


def wallet_payment_method_keyboard(
    amount: str,
    payment_methods: tuple[PaymentMethod, ...],
    language: str = "km",
) -> InlineKeyboardMarkup:
    rows = [
        [
            InlineKeyboardButton(
                text=f"💳 {method.upper()}",
                callback_data=f"wallet:method:{method}:{amount}",
                style="primary",
            )
        ]
        for method in payment_methods
    ]
    rows.append(back_button("home", language))
    return keyboard(rows)


def wallet_payment_keyboard(
    deposit_id: str,
    open_url: str | None,
    language: str = "km",
    payment_method: PaymentMethod = "aba",
) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    if open_url:
        open_label_km = (
            "🏦 បើកក្នុង ABA Mobile"
            if payment_method == "aba"
            else "🔗 បើកទំព័រទូទាត់"
        )
        open_label_en = (
            "🏦 Open in ABA Mobile"
            if payment_method == "aba"
            else "🔗 Open payment page"
        )
        rows.append(
            [
                InlineKeyboardButton(
                    text=copy(language, open_label_km, open_label_en),
                    url=open_url,
                    style="primary",
                )
            ]
        )
    rows.extend(
        [
            [
                InlineKeyboardButton(
                    text=copy(
                        language,
                        "✅ ខ្ញុំបានទូទាត់ · ពិនិត្យ",
                        "✅ I have paid · Check",
                    ),
                    callback_data=f"wallet:check:{deposit_id}",
                    style="success",
                )
            ],
            [
                InlineKeyboardButton(
                    text=copy(language, "❌ បោះបង់", "❌ Cancel"),
                    callback_data=f"wallet:cancel:{deposit_id}",
                    style="danger",
                )
            ],
        ]
    )
    return keyboard(rows)


async def close_wallet_deposit_message(bot: Bot, store: Store, deposit_id: str) -> None:
    try:
        payment_message = await store.wallet_deposit_message(deposit_id)
        if payment_message and payment_message.get("message_id"):
            await bot.delete_message(
                chat_id=int(payment_message["chat_id"]),
                message_id=int(payment_message["message_id"]),
            )
    except TelegramBadRequest as exc:
        if "message to delete not found" not in str(exc).lower():
            logger.exception("Could not delete QR for wallet deposit %s", deposit_id)
    except Exception:
        logger.exception("Could not delete QR for wallet deposit %s", deposit_id)


async def complete_wallet_deposit(
    bot: Bot,
    store: Store,
    deposit: dict[str, Any],
    language: str | None = None,
) -> bool:
    completed = await store.complete_wallet_deposit(str(deposit["id"]))
    if not completed:
        return False
    await close_wallet_deposit_message(bot, store, str(deposit["id"]))
    user_language = normalize_language(
        language or await safe_user_language(store, int(deposit["chat_id"]))
    )
    try:
        balance = money(await store.wallet_balance(int(deposit["chat_id"])))
        message = copy(
            user_language,
            f"✅ បានបញ្ចូលប្រាក់ {money(deposit['amount'])} ជោគជ័យ!\n💰 សមតុល្យថ្មី៖ {balance}",
            f"✅ {money(deposit['amount'])} was added to your wallet!\n💰 New balance: {balance}",
        )
    except Exception:
        logger.exception(
            "Wallet deposit %s was credited but the new balance could not be loaded",
            deposit["id"],
        )
        message = copy(
            user_language,
            f"✅ បានបញ្ចូលប្រាក់ {money(deposit['amount'])} ជោគជ័យ!",
            f"✅ {money(deposit['amount'])} was added to your wallet!",
        )
    try:
        await bot.send_message(int(deposit["chat_id"]), message)
    except Exception:
        logger.exception(
            "Wallet deposit %s was credited but the user notification could not be sent",
            deposit["id"],
        )
    await notify_wallet_deposit_success(bot, store, deposit)
    return True


async def notify_wallet_deposit_success(
    bot: Bot, store: Store, deposit: dict[str, Any]
) -> None:
    try:
        destinations_setting = (
            (await store.bot_settings(("order_notification_chat_ids",)))
            .get("order_notification_chat_ids", "")
            .strip()
        )
    except Exception:
        logger.exception("Could not load wallet deposit notification settings")
        return
    if not destinations_setting:
        return

    reference = deposit.get("transaction_id") or deposit["id"]
    text = (
        "🔔 <b>ការបញ្ចូលប្រាក់ថ្មី (New Wallet Deposit)</b>\n"
        "───────────────────\n"
        f"🆔 <b>Telegram ID:</b> <code>{int(deposit['chat_id'])}</code>\n"
        f"💰 <b>ចំនួនទឹកប្រាក់:</b> {money(deposit['amount'])}\n"
        f"🏦 <b>វិធីទូទាត់:</b> "
        f"{html.escape(str(deposit.get('payment_provider') or 'Unknown'))}\n"
        f"🏷 <b>Ref ID:</b> <code>{html.escape(str(reference))}</code>\n"
        "───────────────────\n"
        "✅ <b>ស្ថានភាព:</b> បញ្ចូលប្រាក់ទៅ Wallet រួចរាល់"
    )
    for value in destinations_setting.split(","):
        destination = value.strip()
        if not destination:
            continue
        try:
            chat_id: int | str = int(destination)
        except ValueError:
            if not destination.startswith("@"):
                logger.warning(
                    "Ignoring invalid wallet deposit notification destination %r",
                    destination,
                )
                continue
            chat_id = destination
        try:
            await bot.send_message(chat_id, text, parse_mode="HTML")
        except Exception:
            logger.exception(
                "Could not send wallet deposit notification to %s", chat_id
            )


async def expire_wallet_deposit(
    bot: Bot, store: Store, deposit: dict[str, Any], status: str
) -> None:
    await store.close_wallet_deposit(str(deposit["id"]), status)
    await close_wallet_deposit_message(bot, store, str(deposit["id"]))
    language = await safe_user_language(store, int(deposit["chat_id"]))
    await bot.send_message(
        int(deposit["chat_id"]),
        copy(
            language,
            "❌ ការបញ្ចូលប្រាក់មិនបានបញ្ចប់។ សូមព្យាយាមម្តងទៀត។",
            "❌ The deposit was not completed. Please try again.",
        ),
    )


async def safe_user_language(store: Store, telegram_id: int) -> str:
    try:
        language = normalize_language(await store.user_language(telegram_id))
        user_language_cache[telegram_id] = language
        return language
    except Exception:
        logger.exception(
            "Could not load language preference for Telegram user %s", telegram_id
        )
        return user_language_cache.get(telegram_id, "km")


async def safe_upsert_user(
    store: Store, telegram_id: int, username: str | None, first_name: str, language: str
) -> None:
    try:
        await store.upsert_user(telegram_id, username, first_name, language)
    except Exception:
        logger.exception("Could not save profile for Telegram user %s", telegram_id)


async def notify_stock_added(
    bot: Bot, store: Store, product_id: str, added: int
) -> None:
    if added < 1:
        return
    try:
        chat_ids = (await store.bot_settings(("stock_notification_chat_ids",))).get(
            "stock_notification_chat_ids", ""
        )
        product = await store.product(product_id)
        if not product:
            logger.warning(
                "Skipping stock notification for unavailable product %s", product_id
            )
            return
        category = await store.category(product["category_id"])
        users = await store.stock_notification_users()
        bot_username = (await bot.get_me()).username
    except Exception:
        logger.exception(
            "Could not prepare stock notification for product %s", product_id
        )
        return
    if not bot_username:
        logger.warning("Skipping stock notification without a bot username")
        return

    destinations: dict[int | str, str | None] = {
        int(user["telegram_id"]): user.get("language", "km") for user in users
    }
    for value in chat_ids.split(","):
        chat = value.strip()
        if not chat:
            continue
        try:
            chat_id: int | str = int(chat)
        except ValueError:
            if not chat.startswith("@"):
                logger.warning("Ignoring invalid stock notification chat %r", chat)
                continue
            chat_id = chat
        destinations.setdefault(chat_id, None)

    sent = 0
    failed = 0
    for index, (chat_id, language) in enumerate(destinations.items(), start=1):
        category_name = html.escape(
            str((category or {}).get("name") or "Unknown")
        )
        product_name = html.escape(str(product.get("name") or "Unknown"))
        if language == "en":
            text = (
                "📢 <b>New stock has arrived!</b>\n\n"
                f"📦 • Product: <b>{category_name}</b>\n"
                f"📦 • Package: <b>{product_name}</b>\n"
                f"📦 • Newly added: <b>{added}</b>\n\n"
                "✅ <b>Stock is ready to purchase!</b>\n"
                "👉 Tap the button below to buy:"
            )
            button_text = "🛍 Buy now"
        elif language == "km":
            text = (
                "📢 <b>ស្តុកថ្មីបានមកដល់ហើយ!</b>\n\n"
                f"📦 • ទំនិញ៖ <b>{category_name}</b>\n"
                f"📦 • កញ្ចប់៖ <b>{product_name}</b>\n"
                f"📦 • ចំនួនបន្ថែមថ្មី៖ <b>{added}</b>\n\n"
                "✅ <b>ស្តុកបានបន្ថែមរួចរាល់ អាចធ្វើការទិញបាន!</b>\n"
                "👉 ចុចប៊ូតុងខាងក្រោមដើម្បីទិញ៖"
            )
            button_text = "🛍 ទិញឥឡូវនេះ"
        else:
            text = (
                "📢 <b>ស្តុកថ្មីបានមកដល់ហើយ / New stock has arrived!</b>\n\n"
                f"📦 • ទំនិញ / Product: <b>{category_name}</b>\n"
                f"📦 • កញ្ចប់ / Package: <b>{product_name}</b>\n"
                f"📦 • ចំនួនបន្ថែមថ្មី / Newly added: <b>{added}</b>\n\n"
                "✅ <b>អាចធ្វើការទិញបាន / Ready to purchase!</b>\n"
                "👉 ចុចប៊ូតុងខាងក្រោម / Tap the button below:"
            )
            button_text = "🛍 ទិញ / Buy"
        keyboard_markup = keyboard(
            [
                [
                    InlineKeyboardButton(
                        text=button_text,
                        url=stock_notification_url(bot_username, product_id),
                    )
                ]
            ]
        )
        try:
            await bot.send_message(
                chat_id,
                text,
                parse_mode="HTML",
                reply_markup=keyboard_markup,
            )
            sent += 1
        except Exception:
            failed += 1
            logger.debug(
                "Could not send stock notification to %s", chat_id, exc_info=True
            )
        if index % 20 == 0:
            await asyncio.sleep(1)
    logger.info(
        "Stock notification for product %s: sent to %s chats, failed for %s",
        product_id,
        sent,
        failed,
    )


async def notify_order_success(
    bot: Bot, store: Store, settings: Settings, order_id: str
) -> None:
    try:
        destinations_setting = (
            (await store.bot_settings(("order_notification_chat_ids",)))
            .get("order_notification_chat_ids", "")
            .strip()
        )
    except Exception:
        logger.exception("Could not load order notification settings")
        return
    if not destinations_setting:
        return
    try:
        order = await store.order_notification_details(order_id)
        if not order:
            logger.error(
                "Could not find completed order %s for admin notification", order_id
            )
            return
    except Exception:
        logger.exception(
            "Could not load completed order %s for admin notification", order_id
        )
        return

    product = order.get("product") or {}
    customer = order.get("customer") or {}
    credentials = [
        decrypt_stock(value, settings.stock_encryption_key)
        for value in order.get("credentials", [])
    ]
    username = customer.get("username")
    customer_name = customer.get("first_name") or (
        f"@{username}" if username else "Unknown"
    )
    customer_label = html.escape(customer_name)
    if username and customer.get("first_name"):
        customer_label += f" (@{html.escape(username)})"

    created_at = order.get("created_at")
    try:
        timestamp = datetime.fromisoformat(str(created_at).replace("Z", "+00:00"))
        if timestamp.tzinfo is None:
            timestamp = timestamp.replace(tzinfo=timezone.utc)
        date_text = timestamp.astimezone(timezone(timedelta(hours=7))).strftime(
            "%Y-%m-%d %H:%M:%S"
        )
    except (TypeError, ValueError):
        logger.warning(
            "Invalid created_at for completed order %s: %r", order_id, created_at
        )
        date_text = str(created_at or "Unknown")

    quantity = int(order["quantity"])
    reseller_price = Decimal(
        str(product.get("reseller_price", product.get("price", "0")))
    )
    reseller_order = (
        Decimal(str(order["total"])) == reseller_price * quantity
    )
    price_type = "Reseller Price" if reseller_order else "Regular Price"
    reference = order.get("transaction_id") or order["id"]
    key_lines = "\n".join(
        f"- {html.escape(value)}" for value in credentials
    ) or "- —"
    text = (
        "🔔 <b>ការបញ្ជាទិញថ្មី (New Order)</b>\n"
        "───────────────────\n"
        f"👤 <b>អតិថិជន:</b> {customer_label}\n"
        f"🆔 <b>Telegram ID:</b> <code>{int(order['chat_id'])}</code>\n"
        f"🛍 <b>ប្រភេទផលិតផល:</b> {html.escape(str(product.get('category_name') or 'Unknown'))}\n"
        f"📦 <b>ផលិតផល:</b> {html.escape(str(product.get('name') or 'Unknown'))} (x{quantity})\n"
        "<b>🔑 បញ្ជីទំនិញ:</b>\n"
        f"{key_lines}\n"
        f"💰 <b>ចំនួនទឹកប្រាក់:</b> {money(order['total'])} ({price_type})\n"
        f"🏷 <b>Ref ID:</b> <code>{html.escape(str(reference))}</code>\n"
        f"⏰ <b>កាលបរិច្ឆេទ:</b> {date_text} (Asia/Phnom_Penh)\n"
        "───────────────────\n"
        "✅ <b>ស្ថានភាព:</b> បង់ប្រាក់រួចរាល់ &amp; ប្រគល់ទំនិញ រួចរាល់"
    )

    destinations: list[int | str] = []
    for value in destinations_setting.split(","):
        destination = value.strip()
        if not destination:
            continue
        try:
            destinations.append(int(destination))
        except ValueError:
            if destination.startswith("@"):
                destinations.append(destination)
            else:
                logger.warning(
                    "Ignoring invalid order notification destination %r", destination
                )
    for destination in destinations:
        try:
            await bot.send_message(destination, text, parse_mode="HTML")
        except Exception:
            logger.exception(
                "Could not send completed order %s notification to %s",
                order_id,
                destination,
            )


async def finish_immediate_welcome(
    message: Message,
    welcome_message: Message,
    store: Store,
    settings: Settings,
    initial_language: str,
    previous_message: Message | None,
    previous_menu_message: Message | None,
    previous_message_id: int | None,
) -> None:
    user = message.from_user
    if not user:
        return
    if previous_message:
        try:
            await delete_replaced_message(previous_message)
        except Exception:
            logger.exception(
                "Could not delete previous welcome screen for Telegram user %s", user.id
            )
    if previous_menu_message and (
        previous_message is None
        or previous_menu_message.message_id != previous_message.message_id
    ):
        try:
            await delete_replaced_message(previous_menu_message)
        except Exception:
            logger.exception(
                "Could not delete previous menu for Telegram user %s", user.id
            )
    elif (
        not previous_menu_message
        and previous_message_id
        and (
            previous_message is None
            or previous_message_id != previous_message.message_id
        )
    ):
        try:
            await message.bot.delete_message(
                chat_id=user.id, message_id=previous_message_id
            )
        except Exception:
            logger.exception(
                "Could not delete previous welcome message %s", previous_message_id
            )
    try:
        await delete_replaced_message(message)
    except Exception:
        logger.exception(
            "Could not delete /start command for Telegram user %s", user.id
        )

    try:
        saved_language = await store.user_language(user.id)
    except Exception:
        logger.exception(
            "Could not load language preference for Telegram user %s", user.id
        )
    else:
        language = normalize_language(saved_language or initial_language)
        user_language_cache[user.id] = language
        await safe_upsert_user(store, user.id, user.username, user.first_name, language)
        if (
            language != initial_language
            and active_screen_messages.get(user.id) is welcome_message
            and welcome_message.text is not None
        ):
            try:
                updated_message = await edit_text_message(
                    welcome_message,
                    welcome_text(user, language),
                    parse_mode="HTML",
                    reply_markup=welcome_keyboard(user.id, settings),
                )
            except Exception:
                logger.exception(
                    "Could not update welcome language for Telegram user %s", user.id
                )
            else:
                active_screen_messages[user.id] = updated_message
                menu_keyboard_messages[user.id] = updated_message
                welcome_message_ids[user.id] = updated_message.message_id
    if welcome_media_cache is None:
        schedule_welcome_media_preload(store)


async def send_welcome(
    message: Message,
    store: Store,
    settings: Settings,
    language: str | None = None,
    *,
    immediate: bool = False,
) -> None:
    global welcome_media_cache
    user = message.from_user
    if not user:
        return
    if immediate:
        language = normalize_language(
            language
            or user_language_cache.get(user.id)
            or getattr(user, "language_code", None)
        )
    else:
        language = normalize_language(
            language or await safe_user_language(store, user.id)
        )
    if immediate:
        await delete_active_screen_auxiliary_messages(message.bot, user.id)
        previous_message = active_screen_messages.get(user.id)
        previous_menu_message = menu_keyboard_messages.get(user.id)
        previous_message_id = welcome_message_ids.get(user.id)
        text = welcome_text(user, language)
        reply_markup = welcome_keyboard(user.id, settings)
        media_settings = welcome_media_cache or {}
        welcome_media = (
            media_settings.get("welcome_video_url")
            or media_settings.get("welcome_photo_url")
        )
        welcome_message, welcome_content_message_ids = await send_welcome_content(
            message,
            user.id,
            text,
            welcome_media,
            "video" if media_settings.get("welcome_video_url") else "photo",
            reply_markup,
        )
        if active_screen_messages.get(user.id) is previous_message:
            active_screen_messages.pop(user.id, None)
        if menu_keyboard_messages.get(user.id) is previous_menu_message:
            menu_keyboard_messages.pop(user.id, None)
        if welcome_message_ids.get(user.id) == previous_message_id:
            welcome_message_ids.pop(user.id, None)
        welcome_message_ids[user.id] = welcome_message.message_id
        menu_keyboard_messages[user.id] = welcome_message
        active_screen_messages[user.id] = welcome_message
        if welcome_content_message_ids:
            active_screen_auxiliary_message_ids[user.id] = welcome_content_message_ids
        task = asyncio.create_task(
            finish_immediate_welcome(
                message,
                welcome_message,
                store,
                settings,
                language,
                previous_message,
                previous_menu_message,
                previous_message_id,
            )
        )
        welcome_setup_tasks.add(task)
        task.add_done_callback(welcome_setup_tasks.discard)
        return

    await safe_upsert_user(store, user.id, user.username, user.first_name, language)
    await delete_active_screen_auxiliary_messages(message.bot, user.id)
    previous_message = active_screen_messages.pop(user.id, None)
    previous_menu_message = menu_keyboard_messages.pop(user.id, None)
    previous_message_id = welcome_message_ids.pop(user.id, None)
    if previous_message:
        await delete_replaced_message(previous_message)
    if previous_menu_message and (
        previous_message is None
        or previous_menu_message.message_id != previous_message.message_id
    ):
        await delete_replaced_message(previous_menu_message)
    elif (
        not previous_menu_message
        and previous_message_id
        and (
            previous_message is None
            or previous_message_id != previous_message.message_id
        )
    ):
        try:
            await message.bot.delete_message(
                chat_id=user.id, message_id=previous_message_id
            )
        except TelegramBadRequest:
            logger.debug(
                "Could not delete previous welcome message %s", previous_message_id
            )
    settings_data: dict[str, str] = {}
    load_settings = getattr(store, "bot_settings", None)
    if load_settings:
        try:
            settings_data = await load_settings(
                ("welcome_video_url", "welcome_photo_url")
            )
            welcome_media_cache = settings_data
        except Exception:
            logger.exception("Could not load welcome media settings")
    text = welcome_text(user, language)
    reply_markup = welcome_keyboard(user.id, settings)
    welcome_reference = (
        settings_data.get("welcome_video_url")
        or settings_data.get("welcome_photo_url")
    )
    welcome_message, welcome_media_message_ids = await send_welcome_content(
        message,
        user.id,
        text,
        welcome_reference,
        "video" if settings_data.get("welcome_video_url") else "photo",
        reply_markup,
    )
    if welcome_media_message_ids:
        active_screen_auxiliary_message_ids[user.id] = welcome_media_message_ids
    await delete_replaced_message(message)
    welcome_message_ids[user.id] = welcome_message.message_id
    menu_keyboard_messages[user.id] = welcome_message
    active_screen_messages[user.id] = welcome_message


async def send_home_screen(
    bot: Bot,
    user: Any,
    store: Store,
    settings: Settings,
    language: str | None = None,
) -> Message:
    global welcome_media_cache
    language = normalize_language(language or await safe_user_language(store, user.id))
    await delete_active_screen_auxiliary_messages(bot, user.id)
    previous_message = active_screen_messages.pop(user.id, None)
    previous_menu_message = menu_keyboard_messages.pop(user.id, None)
    previous_message_id = welcome_message_ids.pop(user.id, None)
    if previous_message:
        await delete_replaced_message(previous_message)
    if previous_menu_message and (
        previous_message is None
        or previous_menu_message.message_id != previous_message.message_id
    ):
        await delete_replaced_message(previous_menu_message)
    elif (
        not previous_menu_message
        and previous_message_id
        and (
            previous_message is None
            or previous_message_id != previous_message.message_id
        )
    ):
        try:
            await bot.delete_message(chat_id=user.id, message_id=previous_message_id)
        except TelegramBadRequest:
            logger.debug(
                "Could not delete previous welcome message %s", previous_message_id
            )
    text = welcome_text(user, language)
    settings_data: dict[str, str] = {}
    try:
        settings_data = await store.bot_settings(
            ("welcome_video_url", "welcome_photo_url")
        )
        welcome_media_cache = settings_data
    except Exception:
        logger.exception("Could not load welcome media settings")
    reply_markup = welcome_keyboard(user.id, settings)
    video_url = settings_data.get("welcome_video_url")
    photo_url = settings_data.get("welcome_photo_url")
    welcome_reference = video_url or photo_url
    sent, media_message_ids = await send_welcome_content(
        bot,
        user.id,
        text,
        welcome_reference,
        "video" if video_url else "photo",
        reply_markup,
        via_bot=True,
    )
    if media_message_ids:
        active_screen_auxiliary_message_ids[user.id] = media_message_ids
    active_screen_messages[user.id] = sent
    welcome_message_ids[user.id] = sent.message_id
    menu_keyboard_messages[user.id] = sent
    return sent


async def deliver_order(
    bot: Bot, store: Store, settings: Settings, order_id: str, chat_id: int
) -> None:
    await delete_payment_message(bot, store, order_id)
    delivery = await store.order_delivery_details(order_id)
    credentials = await store.fulfill_order(order_id)
    if not credentials:
        return
    keys = [
        decrypt_stock(value, settings.stock_encryption_key) for value in credentials
    ]
    delivery_message = (delivery or {}).get("delivery_message", "").strip()
    if delivery_message:
        await bot.send_message(
            chat_id, html.escape(delivery_message), parse_mode="HTML"
        )
    delivery_media_reference = (delivery or {}).get("delivery_media_url")
    if delivery_media_reference:
        stored_reference = stored_telegram_content_reference(
            delivery_media_reference
        )
        if stored_reference:
            try:
                await copy_telegram_content_reference(
                    bot, chat_id, stored_reference
                )
            except TelegramBadRequest:
                logger.exception(
                    "Could not copy after-order media for order %s", order_id
                )
        else:
            try:
                await send_cloudinary_media(
                    bot, delivery_media_reference, chat_id=chat_id
                )
            except TelegramAPIError:
                logger.exception(
                    "Could not send after-order Cloudinary media for order %s",
                    order_id,
                )
    key_lines = "\n".join(f"- <code>{html.escape(value)}</code>" for value in keys)
    text = (
        "✅ ទំនិញបានជោគជ័យ\n\n"
        f"🔑 បញ្ជីទំនិញ របស់អ្នក [ សរុប {len(keys)} កូដ ]\n"
        f"{key_lines}\n\n"
        "🙏 អរគុណសម្រាប់ការទិញទំនិញ 🙏"
    )
    delivery_rows = [
        [
            InlineKeyboardButton(
                text=f"📋 ចំលងកូដ {index}",
                copy_text=CopyTextButton(text=value),
            )
        ]
        for index, value in enumerate(keys, start=1)
    ]
    await bot.send_message(
        chat_id,
        text,
        parse_mode="HTML",
        reply_markup=keyboard(delivery_rows),
    )
    await store.mark_delivered(order_id)
    await notify_order_success(bot, store, settings, order_id)


async def delete_payment_message(bot: Bot, store: Store, order_id: str) -> None:
    payment_message = None
    try:
        payment_message = await store.payment_message(order_id)
    except Exception:
        logger.exception("Could not load payment message for order %s", order_id)
    if payment_message and payment_message.get("message_id"):
        chat_id = int(payment_message["chat_id"])
        message_id = int(payment_message["message_id"])
    elif order_id in payment_message_ids:
        chat_id, message_id = payment_message_ids[order_id]
    else:
        logger.warning("No QR message ID is available for order %s", order_id)
        return
    try:
        await bot.delete_message(
            chat_id=chat_id,
            message_id=message_id,
        )
    except TelegramBadRequest as exc:
        if "message to delete not found" not in str(exc).lower():
            raise
    payment_message_ids.pop(order_id, None)
    try:
        await store.clear_payment_message(order_id)
    except Exception:
        logger.exception("Could not clear payment message ID for order %s", order_id)


async def expire_payment(
    bot: Bot,
    store: Store,
    order_id: str,
    chat_id: int,
    status: str,
) -> None:
    await delete_payment_message(bot, store, order_id)
    language = await safe_user_language(store, chat_id)
    await bot.send_message(
        chat_id,
        copy(
            language,
            "❌ ការទូទាត់មិនបានបញ្ចប់។ QR code ត្រូវបានលុបចោល។ សូមព្យាយាមម្តងទៀតប្រសិនបើអ្នកនៅតែចង់ទិញផលិតផលនេះ។",
            "❌ Payment was not completed and the QR code was cancelled. Please try again if you still want to buy this product.",
        ),
    )
    await store.release_order(order_id, status)


async def track_payment_message(
    bot: Bot,
    store: Store,
    order_id: str,
    chat_id: int,
    message_id: int,
) -> None:
    payment_message_ids[order_id] = (chat_id, message_id)
    try:
        payment_status = await store.set_payment_message(order_id, message_id)
    except Exception:
        logger.exception("Could not save payment message ID for order %s", order_id)
        return
    if payment_status and payment_status != "pending":
        await delete_payment_message(bot, store, order_id)


def register_handlers(
    dispatcher: Dispatcher,
    bot: Bot,
    store: Store,
    payments: KHQRPayGateway,
    settings: Settings,
) -> None:
    async def replace_callback_screen(
        callback: CallbackQuery,
        text: str,
        reply_markup: InlineKeyboardMarkup | None = None,
        parse_mode: str | None = None,
    ) -> Message:
        sent = await edit_text_message(
            callback.message,
            text,
            parse_mode=parse_mode,
            reply_markup=reply_markup,
        )
        return await switch_active_screen(callback.from_user.id, sent)

    async def delete_broadcast_preview(user_id: int, draft: dict[str, Any]) -> None:
        for message_id in draft.pop("preview_message_ids", []):
            try:
                await bot.delete_message(user_id, message_id)
            except TelegramBadRequest:
                logger.debug("Could not delete broadcast preview %s", message_id)

    async def show_broadcast_prompt(
        user_id: int,
        text: str,
        stage: str = "content",
        input_message: Message | None = None,
    ) -> None:
        language = await safe_user_language(store, user_id)
        await update_active_screen(
            user_id,
            text,
            input_message=input_message,
            parse_mode="HTML",
            reply_markup=broadcast_draft_keyboard(stage, language),
        )

    async def preview_broadcast(user_id: int, language: str) -> None:
        draft = broadcast_drafts[user_id]
        if not draft["content"]:
            await show_broadcast_prompt(
                user_id,
                copy(
                    language,
                    "⚠️ សូមផ្ញើអត្ថបទ ឬ Media យ៉ាងហោចណាស់មួយសិន។",
                    "⚠️ Send at least one text or media item first.",
                ),
            )
            return
        await delete_broadcast_preview(user_id, draft)
        try:
            destinations = await broadcast_destinations(store)
            preview_ids = await copy_broadcast_content(
                bot,
                user_id,
                user_id,
                draft["content"],
                draft["buttons"],
            )
        except Exception:
            logger.exception(
                "Could not prepare broadcast preview for admin %s", user_id
            )
            await show_broadcast_prompt(
                user_id,
                copy(
                    language,
                    "មិនអាចបង្កើត Preview បានទេ។ សូមសាកល្បងម្តងទៀត។",
                    "Could not prepare the preview. Please try again.",
                ),
            )
            return
        draft["preview_message_ids"] = preview_ids
        draft["stage"] = "preview"
        group_count = len(
            {
                str(item.get("media_group_id"))
                for item in draft["content"]
                if item.get("media_group_id")
            }
        )
        summary = (
            "📣 <b>Broadcast Preview</b>\n"
            f"👥 Recipients: {len(destinations)} (users + configured notification chats)\n"
            f"📎 Content messages: {len(draft['content'])}"
            + (f" · Albums: {group_count}" if group_count else "")
            + f"\n🔗 URL buttons: {len(draft['buttons'])}\n\n"
            "Preview content is shown above. Choose Send now or Schedule."
        )
        await show_broadcast_prompt(user_id, summary, "preview")

    async def handle_broadcast_input(message: Message, language: str) -> bool:
        if not message.from_user or message.from_user.id not in settings.admin_ids:
            return False
        user_id = message.from_user.id
        draft = broadcast_drafts.get(user_id)
        if not draft:
            return False
        text = (message.text or "").strip()
        stage = draft["stage"]
        if text.casefold() in {"/cancel", "/cancelbroadcast"}:
            await delete_broadcast_preview(user_id, draft)
            await delete_broadcast_source(bot, user_id, draft["content"])
            broadcast_drafts.pop(user_id, None)
            await update_active_screen(
                user_id,
                copy(language, "បានបោះបង់ Broadcast។", "Broadcast cancelled."),
                input_message=message,
                reply_markup=keyboard([back_button("admin", language)]),
            )
            return True
        if stage == "content":
            if text.casefold() in {"/done", "/preview"}:
                await delete_replaced_message(message)
                await preview_broadcast(user_id, language)
                return True
            if text.casefold() in {"/buttons", "/links"}:
                draft["stage"] = "buttons"
                await show_broadcast_prompt(
                    user_id,
                    copy(
                        language,
                        "ផ្ញើ URL Buttons មួយក្នុងមួយបន្ទាត់ តាមទម្រង់៖\n<code>ឈ្មោះប៊ូតុង | https://example.com</code>\nអតិបរមា 8 ប៊ូតុង។ ផ្ញើ <code>-</code> ដើម្បីលុប Buttons។",
                        "Send URL buttons one per line:\n<code>Button label | https://example.com</code>\nUp to 8 buttons. Send <code>-</code> to remove buttons.",
                    ),
                    "buttons",
                )
                await delete_replaced_message(message)
                return True
            if len(draft["content"]) >= 100:
                await show_broadcast_prompt(
                    user_id,
                    copy(
                        language,
                        "ដល់កំណត់ 100 សារ/ឯកសារហើយ។ ចុច Preview ដើម្បីបន្ត។",
                        "Broadcast content is limited to 100 messages/files. Preview it to continue.",
                    ),
                )
                await delete_replaced_message(message)
                return True
            telegram_post = parse_telegram_post_link(text)
            if telegram_post:
                post_chat_id, post_message_id = telegram_post
                draft["content"].append(
                    {
                        "message_id": post_message_id,
                        "media_group_id": None,
                        "source_chat_id": post_chat_id,
                    }
                )
                await delete_replaced_message(message)
            else:
                draft["content"].append(
                    {
                        "message_id": message.message_id,
                        "media_group_id": message.media_group_id,
                    }
                )
            draft["stage"] = "content"
            if (
                message.media_group_id
                and sum(
                    item.get("media_group_id") == message.media_group_id
                    for item in draft["content"]
                )
                > 1
            ):
                return True
            await show_broadcast_prompt(
                user_id,
                copy(
                    language,
                    f"✅ បានបន្ថែមមាតិកា {len(draft['content'])} សារ។ បន្តផ្ញើអត្ថបទ/Media ឬចុច Preview។",
                    f"✅ Added content item {len(draft['content'])}. Send more text/media or a Telegram post link, or choose Preview.",
                ),
            )
            return True
        if stage == "buttons":
            try:
                draft["buttons"] = parse_broadcast_buttons(text)
            except ValueError as exc:
                await show_broadcast_prompt(
                    user_id,
                    f"⚠️ {html.escape(str(exc))}",
                    "buttons",
                    input_message=message,
                )
                return True
            await delete_replaced_message(message)
            draft["stage"] = "content"
            await preview_broadcast(user_id, language)
            return True
        if stage == "schedule":
            try:
                scheduled_local = datetime.strptime(text, "%Y-%m-%d %H:%M").replace(
                    tzinfo=PHNOM_PENH_TIMEZONE
                )
            except ValueError:
                await show_broadcast_prompt(
                    user_id,
                    copy(
                        language,
                        "ទម្រង់ថ្ងៃ/ម៉ោងមិនត្រឹមត្រូវ។ ប្រើ YYYY-MM-DD HH:MM (ម៉ោងភ្នំពេញ)។",
                        "Invalid date/time. Use YYYY-MM-DD HH:MM (Phnom Penh time).",
                    ),
                    "schedule",
                    input_message=message,
                )
                return True
            if scheduled_local <= datetime.now(PHNOM_PENH_TIMEZONE):
                await show_broadcast_prompt(
                    user_id,
                    copy(
                        language,
                        "ពេលវេលាត្រូវនៅអនាគត។ សូមបញ្ចូលម្ដងទៀត។",
                        "The scheduled time must be in the future. Enter it again.",
                    ),
                    "schedule",
                    input_message=message,
                )
                return True
            try:
                job_id = await store.create_scheduled_broadcast(
                    user_id,
                    scheduled_local.astimezone(timezone.utc).isoformat(),
                    draft["content"],
                    draft["buttons"],
                )
            except Exception:
                logger.exception("Could not schedule broadcast for admin %s", user_id)
                await show_broadcast_prompt(
                    user_id,
                    copy(
                        language,
                        "មិនអាចរក្សាទុក Schedule បានទេ។ សូមព្យាយាមម្ដងទៀត។",
                        "Could not save the schedule. Please try again.",
                    ),
                    "schedule",
                    input_message=message,
                )
                return True
            await delete_broadcast_preview(user_id, draft)
            broadcast_drafts.pop(user_id, None)
            await update_active_screen(
                user_id,
                copy(
                    language,
                    f"✅ បានកំណត់ Broadcast រួចរាល់សម្រាប់ {scheduled_local.strftime('%Y-%m-%d %H:%M')} (ម៉ោងភ្នំពេញ)។\nID: <code>{html.escape(job_id)}</code>",
                    f"✅ Broadcast scheduled for {scheduled_local.strftime('%Y-%m-%d %H:%M')} (Phnom Penh time).\nID: <code>{html.escape(job_id)}</code>",
                ),
                input_message=message,
                parse_mode="HTML",
                reply_markup=keyboard(
                    [back_button("admin:broadcast:scheduled", language)]
                ),
            )
            return True
        return False

    async def show_catalogs(
        message: Message, page: int, language: str, edit_message: bool = True
    ) -> None:
        async def render(text: str, **kwargs: Any) -> None:
            if edit_message:
                await edit_text_message(message, text, **kwargs)
                return
            user_id = message.from_user.id if message.from_user else message.chat.id
            await send_replacing_screen(
                message,
                user_id,
                text,
                parse_mode=kwargs.get("parse_mode"),
                reply_markup=kwargs.get("reply_markup"),
            )

        categories = await store.categories()
        if not categories:
            await render(
                copy(
                    language,
                    "បច្ចុប្បន្នមិនមានប្រភេទទំនិញទេ។ សូមពិនិត្យម្ដងទៀតពេលក្រោយ។",
                    "There are no product categories yet. Please check back later.",
                ),
                reply_markup=keyboard([back_button(language=language)]),
            )
            return
        visible_categories, page, page_count = paginate(categories, page)
        rows = [
            [
                InlineKeyboardButton(
                    text=f"🛍 {item['name']}", callback_data=f"c:{item['id']}"
                )
            ]
            for item in visible_categories
        ]
        navigation = pagination_row(page, page_count, "shop:catalogs", language)
        if navigation:
            rows.append(navigation)
        rows.append(back_button(language=language))
        text = copy(
            language,
            "🛍 <b>សូមជ្រើសរើសប្រភេទផលិតផល</b>\n"
            "───────────────────\n"
            "សូមជ្រើសរើស Catalog ណាមួយខាងក្រោមដើម្បីមើល Package ទំនិញ៖",
            "🛍 <b>Choose a product category</b>\n"
            "───────────────────\n"
            "Choose a catalog below to browse its packages:",
        )
        await render(
            text,
            parse_mode="HTML",
            reply_markup=keyboard(rows),
        )

    async def show_packages(
        message: Message,
        category_id: str,
        page: int,
        language: str,
        is_reseller: bool = False,
    ) -> None:
        category_data = await store.category(category_id)
        if not category_data:
            await edit_text_message(
                message,
                copy(
                    language,
                    "ប្រភេទផលិតផលនេះមិនមានទៀតទេ។",
                    "This category is no longer available.",
                ),
                reply_markup=keyboard([back_button("shop", language)]),
            )
            return
        products = await store.products(category_id)
        if not products:
            await edit_text_message(
                message,
                f"🛍 <b>{html.escape(category_data['name'])}</b>\n"
                "───────────────────\n"
                + copy(
                    language,
                    "ប្រភេទនេះមិនទាន់មាន Package ទេ។",
                    "There are no packages in this category yet.",
                ),
                parse_mode="HTML",
                reply_markup=keyboard([back_button("shop", language)]),
            )
            return
        visible_products, page, page_count = paginate(products, page)
        stock_counts = await asyncio.gather(
            *(store.available_stock(item["id"]) for item in visible_products)
        )
        rows = []
        for item, stock in zip(visible_products, stock_counts):
            stock_label = str(stock) if stock else copy(language, "អស់ស្តុក", "Out")
            price = product_price(item, is_reseller)
            rows.append(
                [
                    InlineKeyboardButton(
                        text=f"📦 {item['name']} • {money(price)} ({stock_label})",
                        callback_data=f"p:{item['id']}",
                        style=stock_button_style(stock),
                    )
                ]
            )
        navigation = pagination_row(
            page, page_count, f"shop:packages:{category_id}", language
        )
        if navigation:
            rows.append(navigation)
        rows.append(back_button("shop", language))
        description = (category_data.get("description") or "").strip()
        intro = html.escape(description) if description else ""
        text = copy(
            language,
            f"🛍 <b>{html.escape(category_data['name'])}</b>\n"
            "───────────────────\n"
            f"{intro + chr(10) if intro else ''}សូមជ្រើសរើស Package ខាងក្រោម៖",
            f"🛍 <b>{html.escape(category_data['name'])}</b>\n"
            "───────────────────\n"
            f"{intro + chr(10) if intro else ''}Choose a package below:",
        )
        await edit_text_message(
            message, text, parse_mode="HTML", reply_markup=keyboard(rows)
        )

    async def show_how_to_buy(
        message: Message,
        language: str,
        replace_user_screen: bool = False,
    ) -> None:
        user_id = message.from_user.id if message.from_user else None

        async def answer(text: str, *, parse_mode: str | None = None) -> None:
            options = {"parse_mode": parse_mode} if parse_mode else {}
            if replace_user_screen and user_id is not None:
                await send_replacing_screen(message, user_id, text, **options)
            else:
                await message.answer(text, **options)

        try:
            settings_data = await store.bot_settings(
                (
                    "how_to_buy_video_url",
                    "how_to_buy_caption",
                    "how_to_buy_caption_km",
                    "how_to_buy_caption_en",
                )
            )
        except Exception:
            logger.exception("Could not load tutorial settings")
            await answer(
                copy(
                    language,
                    "មិនអាចទាញយកវីដេអូណែនាំបានទេ។",
                    "Could not load the tutorial video.",
                )
            )
            return
        video = settings_data.get("how_to_buy_video_url")
        if not video:
            await answer(
                copy(
                    language,
                    "មិនទាន់មានវីដេអូណែនាំទេ។",
                    "The tutorial video is not configured yet.",
                )
            )
            return
        caption = (
            settings_data.get(f"how_to_buy_caption_{language}")
            or settings_data.get("how_to_buy_caption", "")
        ).strip()[:1024]
        reference = stored_telegram_content_reference(video)
        if reference:
            try:
                copied = await copy_telegram_content_reference(
                    bot, message.chat.id, reference
                )
            except TelegramAPIError as exc:
                logger.warning("Could not copy configured How to Buy media: %s", exc)
                post_link = video.strip()
                if parse_telegram_post_link(post_link):
                    link_label = copy(
                        language,
                        "បើក Media ណែនាំក្នុង Telegram",
                        "Open the tutorial in Telegram",
                    )
                    fallback_text = (
                        f"{html.escape(caption)}\n\n" if caption else ""
                    ) + (
                        "មិនអាចផ្ញើ Media តាម Bot បានទេ។ "
                        if language == "km"
                        else "The bot could not send this media. "
                    )
                    fallback_text += (
                        f'<a href="{html.escape(post_link, quote=True)}">'
                        f"{link_label}</a>"
                    )
                    await answer(fallback_text, parse_mode="HTML")
                    return
                await answer(
                    copy(
                        language,
                        "មិនអាចចម្លង Media ណែនាំបានទេ។ ប្រភពអាចត្រូវបានលុប ឬ Bot មិនអាចចូលបាន។ សូមជូនដំណឹង Admin ឲ្យកំណត់ Media ថ្មី។",
                        "Could not copy the tutorial media. Its source may be deleted or inaccessible to the bot. Please ask the admin to set a new source.",
                    )
                )
                return
            sent = (
                await message.answer(
                    caption
                    or copy(language, "🎥 វីដេអូណែនាំ", "🎥 Tutorial media")
                )
            )
            if replace_user_screen and user_id is not None:
                await switch_active_screen(user_id, sent, message)
                active_screen_auxiliary_message_ids[user_id] = [
                    item.message_id
                    for item in copied
                    if item.message_id != sent.message_id
                ]
            return
        sent_messages = await send_cloudinary_media(
            message,
            video,
            fallback="video",
            caption=caption or None,
        )
        if not sent_messages:
            await answer(copy(language, "Media មិនត្រឹមត្រូវទេ។", "Invalid media URL."))
            return
        sent = sent_messages[0]
        if replace_user_screen and user_id is not None:
            await switch_active_screen(user_id, sent, message)
            active_screen_auxiliary_message_ids[user_id] = [
                item.message_id
                for item in sent_messages[1:]
                if item.message_id != sent.message_id
            ]

    async def show_admin_settings(
        message: Message, language: str, section: str | None = None
    ) -> None:
        if section is None:
            rows = [
                [
                    InlineKeyboardButton(
                        text=copy(language, "🖼 Media", "🖼 Media"),
                        callback_data="admin:settings:section:media",
                    ),
                    InlineKeyboardButton(
                        text=copy(language, "📝 Captions", "📝 Captions"),
                        callback_data="admin:settings:section:captions",
                    ),
                ],
                [
                    InlineKeyboardButton(
                        text=copy(language, "🔗 Store links", "🔗 Store links"),
                        callback_data="admin:settings:section:shop",
                    ),
                    InlineKeyboardButton(
                        text=copy(language, "🔔 Notifications", "🔔 Notifications"),
                        callback_data="admin:settings:section:notifications",
                    ),
                ],
                back_button("admin", language),
            ]
            await edit_text_message(
                message,
                f"⚙️ <b>{copy(language, 'Bot Settings', 'Bot Settings')}</b>\n"
                f"{copy(language, 'ជ្រើសរើសផ្នែកដែលចង់កំណត់។', 'Choose a settings section.')}",
                parse_mode="HTML",
                reply_markup=keyboard(rows),
            )
            return

        if section == "media":
            keys = ("welcome_photo_url", "welcome_video_url", "how_to_buy_video_url")
            title = copy(language, "🖼 Media", "🖼 Media")
        elif section in BOT_SETTING_SECTIONS:
            english_title, khmer_title, setting_keys = BOT_SETTING_SECTIONS[section]
            keys = setting_keys
            title = copy(language, khmer_title, english_title)
        else:
            await edit_text_message(
                message,
                copy(
                    language,
                    "រកមិនឃើញផ្នែក Settings ទេ។",
                    "Settings section not found.",
                ),
                reply_markup=keyboard([back_button("admin:settings", language)]),
            )
            return

        try:
            values = await store.bot_settings(keys)
        except Exception:
            logger.exception("Could not load bot settings for admin")
            await edit_text_message(
                message,
                copy(
                    language,
                    "មិនអាចទាញយក Bot settings បានទេ។",
                    "Could not load bot settings.",
                ),
                reply_markup=keyboard([back_button("admin", language)]),
            )
            return

        def state(key: str) -> str:
            return copy(
                language,
                "✅ បានកំណត់" if values.get(key) else "➖ មិនទាន់កំណត់",
                "✅ Set" if values.get(key) else "➖ Not set",
            )

        rows: list[list[InlineKeyboardButton]] = []
        if section == "media":
            welcome_media_state = state(
                "welcome_photo_url"
                if values.get("welcome_photo_url")
                else "welcome_video_url"
            )
            rows.extend(
                [
                    [
                        InlineKeyboardButton(
                            text=f"🖼 Welcome media · {welcome_media_state}",
                            callback_data="admin:media:welcome",
                        )
                    ],
                    [
                        InlineKeyboardButton(
                            text=f"🎥 How to Buy · {state('how_to_buy_video_url')}",
                            callback_data="admin:media:tutorial",
                        )
                    ],
                ]
            )
            welcome_media_is_configured = bool(
                values.get("welcome_photo_url") or values.get("welcome_video_url")
            )
            if welcome_media_is_configured:
                rows.append(
                    [
                        InlineKeyboardButton(
                            text=copy(
                                language, "🗑 លុប Welcome media", "🗑 Clear Welcome media"
                            ),
                            callback_data="admin:setting:clear:welcome_media",
                        )
                    ]
                )
            if values.get("how_to_buy_video_url"):
                rows.append(
                    [
                        InlineKeyboardButton(
                            text=copy(
                                language,
                                "🗑 លុប Tutorial video",
                                "🗑 Clear Tutorial video",
                            ),
                            callback_data="admin:setting:clear:how_to_buy_video_url",
                        )
                    ]
                )
        else:
            for key in keys:
                english_label, khmer_label = BOT_SETTING_LABELS[key]
                label = copy(language, khmer_label, english_label)
                rows.append(
                    [
                        InlineKeyboardButton(
                            text=f"✏️ {label} · {state(key)}",
                            callback_data=f"admin:setting:set:{key}",
                        )
                    ]
                )
                if values.get(key):
                    rows.append(
                        [
                            InlineKeyboardButton(
                                text=copy(
                                    language,
                                    f"🗑 លុប {khmer_label}",
                                    f"🗑 Clear {english_label}",
                                ),
                                callback_data=f"admin:setting:clear:{key}",
                            )
                        ]
                    )
        rows.append(back_button("admin:settings", language))
        await edit_text_message(
            message,
            f"⚙️ <b>{title}</b>\n"
            f"{copy(language, 'កំណត់ ឬកែប្រែតម្លៃខាងក្រោម។', 'Configure or update the settings below.')}",
            parse_mode="HTML",
            reply_markup=keyboard(rows),
        )

    async def start_wallet_deposit(
        user: Any,
        amount_text: str,
        language: str,
        payment_method: PaymentMethod,
    ) -> None:
        amount = parse_wallet_deposit_amount(amount_text, language)

        deposit = await store.create_wallet_deposit(user.id, f"{amount:.2f}")
        deposit_id = str(deposit["id"])
        payment = await payments.create_payment(
            f"{amount:.2f}", deposit_id, payment_method
        )
        deeplink = (
            aba_mobile_deeplink(payment["qr_string"])
            if payment_method == "aba"
            else None
        )
        open_url = (
            aba_mobile_redirect_url(settings.app_base_url or "", deposit_id)
            if payment_method == "aba"
            else payment.get("checkout_url")
        )
        await store.set_wallet_deposit_payment(
            deposit_id,
            str(payment["transaction_id"]),
            deeplink,
            payment_method,
        )
        caption = copy(
            language,
            f"💰 <b>បញ្ចូលប្រាក់ {money(amount)}</b>\n\nសូមស្កេន KHQR ដើម្បីបញ្ចូលប្រាក់ទៅក្នុង Wallet។ សមតុល្យនឹងកើនឡើងបន្ទាប់ពីការទូទាត់ត្រូវបានបញ្ជាក់។",
            f"💰 <b>Add {money(amount)} to your wallet</b>\n\nScan the KHQR to add funds. Your balance is credited after payment is confirmed.",
        )
        sent = await bot.send_photo(
            user.id,
            payment_qr(payment),
            caption=caption,
            parse_mode="HTML",
            reply_markup=wallet_payment_keyboard(
                deposit_id, open_url, language, payment_method
            ),
        )
        await store.set_wallet_deposit_message(deposit_id, sent.message_id)

    async def show_order_history(
        message: Message,
        user_id: int,
        page: int,
        language: str,
        replace_user_screen: bool = False,
    ) -> None:
        orders, has_next = await store.order_history_page(user_id, page)
        text = order_history_text(orders, language)
        markup = order_history_keyboard(page, has_next, language)
        if replace_user_screen:
            await send_replacing_screen(
                message,
                user_id,
                text,
                parse_mode="HTML",
                reply_markup=markup,
            )
            return
        sent = await edit_text_message(
            message,
            text,
            parse_mode="HTML",
            reply_markup=markup,
        )
        await switch_active_screen(
            user_id,
            sent if isinstance(sent, Message) else message,
        )

    async def show_customer_action(
        message: Message, user: Any, text: str, language: str
    ) -> bool:
        action = text.strip().casefold()
        if action in {"/start", "start", "🛍 buy now", "buy now"}:
            await send_welcome(message, store, settings, language, immediate=True)
        elif action in {"👤 account", "account"}:
            try:
                balance = await store.wallet_balance(user.id)
                is_reseller = await store.is_reseller(user.id)
            except Exception:
                logger.exception(
                    "Could not load account wallet for Telegram user %s", user.id
                )
                await send_replacing_screen(
                    message,
                    user.id,
                    wallet_error_message(language),
                )
                return True
            await send_replacing_screen(
                message,
                user.id,
                account_text(user, balance, language, is_reseller),
                parse_mode="HTML",
                reply_markup=account_keyboard(language),
            )
        elif action in {"💰 add funds", "add funds"}:
            pending_wallet_amount.discard(user.id)
            await send_replacing_screen(
                message,
                user.id,
                copy(
                    language,
                    "💰 <b>ជ្រើសរើសចំនួនទឹកប្រាក់ដែលអ្នកចង់បញ្ចូល៖</b>",
                    "💰 <b>Choose the amount you want to deposit:</b>",
                ),
                parse_mode="HTML",
                reply_markup=wallet_deposit_keyboard(language),
            )
        elif action in {
            "🛍 digital products",
            "digital products",
            "🎮 games service",
            "games service",
        }:
            await show_catalogs(message, 0, language, edit_message=False)
        elif action in {"🛒 my orders", "my orders", "orders"}:
            await show_order_history(
                message, user.id, 0, language, replace_user_screen=True
            )
        elif action in {"🎥 how to buy", "how to buy"}:
            await show_how_to_buy(message, language, replace_user_screen=True)
        elif action in {
            "🔗 join group",
            "join group",
            "🔗 install group",
            "install group",
        }:
            try:
                install_group_url = (
                    await store.bot_settings(("install_group_url",))
                ).get("install_group_url", "")
            except Exception:
                logger.exception("Could not load install group URL")
                await send_replacing_screen(
                    message,
                    user.id,
                    copy(
                        language,
                        "មិនអាចទាញយកតំណ Group បានទេ។",
                        "Could not load the install group link.",
                    ),
                )
                return True
            if not install_group_url:
                await send_replacing_screen(
                    message,
                    user.id,
                    copy(
                        language,
                        "មិនទាន់មានតំណចូល Group ទេ។",
                        "The join group link is not configured yet.",
                    ),
                )
            else:
                await send_replacing_screen(
                    message,
                    user.id,
                    copy(
                        language,
                        "ចូល Group តាមតំណខាងក្រោម៖",
                        "Join the group using the link below:",
                    ),
                    reply_markup=keyboard(
                        [
                            [
                                InlineKeyboardButton(
                                    text=copy(
                                        language,
                                        "🔗 ចូល Group",
                                        "🔗 Join Group",
                                    ),
                                    url=install_group_url,
                                )
                            ],
                            back_button(language=language),
                        ]
                    ),
                )
        else:
            return False
        return True

    async def show_resellers(message: Message, language: str) -> None:
        entries = await store.resellers()
        rows = [
            [
                InlineKeyboardButton(
                    text=f"🗑 {item['telegram_id']}",
                    callback_data=f"admin:reseller-remove:{item['telegram_id']}",
                )
            ]
            for item in entries
        ]
        rows.extend(
            [
                [
                    InlineKeyboardButton(
                        text=copy(language, "➕ បន្ថែម Reseller", "➕ Add reseller"),
                        callback_data="admin:reseller-add",
                    )
                ],
                back_button("admin", language),
            ]
        )
        await message.edit_text(
            f"👥 <b>{copy(language, 'គ្រប់គ្រង Reseller', 'Manage resellers')}</b>\n"
            f"{copy(language, 'ចុច Telegram ID ដើម្បីដកសិទ្ធិ។', 'Select a Telegram ID to remove reseller access.')} "
            f"({len(entries)})",
            parse_mode="HTML",
            reply_markup=keyboard(rows),
        )

    async def show_admin_wizard(
        message: Message,
        user_id: int,
        language: str,
        edit_message: bool = True,
        notice: str | None = None,
        preserve_input: bool = False,
    ) -> None:
        async def render(text: str, **kwargs: Any) -> None:
            if edit_message:
                sent = await message.edit_text(text, **kwargs)
                await clear_active_screen(user_id, keep_message_id=message.message_id)
                active_screen_messages[user_id] = sent or message
            else:
                if preserve_input:
                    sent = await message.answer(
                        text,
                        parse_mode=kwargs.get("parse_mode"),
                        reply_markup=kwargs.get("reply_markup"),
                    )
                    await switch_active_screen(user_id, sent)
                else:
                    await send_replacing_screen(
                        message,
                        user_id,
                        text,
                        parse_mode=kwargs.get("parse_mode"),
                        reply_markup=kwargs.get("reply_markup"),
                    )

        wizard = admin_wizards[user_id]
        kind = wizard["kind"]
        steps = ADMIN_WIZARD_STEPS[kind]
        step = wizard.get("step")
        values = wizard["values"]
        notice_text = f"⚠️ {html.escape(notice)}\n\n" if notice else ""
        if step is None:
            title = copy(language, "💾 ពិនិត្យ និងរក្សាទុក", "💾 Review and save")
            rows = [
                [
                    InlineKeyboardButton(
                        text=copy(language, "💾 បញ្ជាក់ការរក្សាទុក", "💾 Confirm save"),
                        callback_data="admin:wizard-save",
                    )
                ],
                [
                    InlineKeyboardButton(
                        text=copy(language, "❌ បោះបង់", "❌ Cancel"),
                        callback_data="admin:wizard-cancel",
                    )
                ],
            ]
            if kind == "product":
                summary = (
                    f"📦 <b>{html.escape(values['name'])}</b>\n"
                    f"{copy(language, 'ព័ត៌មានលម្អិត៖', 'Details:')} {html.escape(values.get('description') or '-')}\n"
                    f"{copy(language, 'តម្លៃអតិថិជន៖', 'Customer price:')} {money(values['price'])}\n"
                    f"{copy(language, 'តម្លៃ Reseller៖', 'Reseller price:')} {money(values['reseller_price'])}\n"
                    f"{copy(language, 'Media៖', 'Media:')} "
                    f"{'✅' if values.get('image_url') or values.get('video_url') or values.get('media_url') or values.get('media_content_ref') else '—'}"
                )
            else:
                summary = (
                    f"🗂 <b>{html.escape(values['name'])}</b>\n"
                    f"{copy(language, 'ព័ត៌មានលម្អិត៖', 'Details:')} {html.escape(values.get('description') or '-')}\n"
                    f"{copy(language, 'សារក្រោយ Order៖', 'After-order message:')} "
                    f"{'✅' if values.get('delivery_message') else '—'}\n"
                    f"{copy(language, 'Media ក្រោយ Order៖', 'After-order media:')} "
                    f"{'✅' if values.get('delivery_media_url') else '—'}"
                )
            await render(
                notice_text + f"{title}\n───────────────────\n{summary}",
                parse_mode="HTML",
                reply_markup=keyboard(rows),
            )
            return

        step_number = steps.index(step) + 1
        step_labels = {
            "media": copy(language, "Media Package", "Package media"),
            "name": copy(language, "ឈ្មោះ", "Name"),
            "description": copy(language, "ព័ត៌មានលម្អិត", "Details"),
            "price": copy(language, "តម្លៃទូទៅ", "Customer price"),
            "reseller_price": copy(language, "តម្លៃ Reseller", "Reseller price"),
            "delivery_message": copy(
                language, "សារ/របៀបប្រើក្រោយទិញ", "After-order message/instructions"
            ),
            "delivery_media": copy(
                language, "Media ក្រោយទិញ", "After-order media"
            ),
        }
        prompts = {
            "media": copy(
                language,
                "សូមផ្ញើរូបភាព ឬវីដេអូ Package។",
                "Send a package photo or video.",
            ),
            "name": copy(language, "សូមបញ្ចូលឈ្មោះផលិតផល។", "Enter the product name."),
            "description": copy(
                language, "សូមបញ្ចូលព័ត៌មានលម្អិត។", "Enter the details."
            ),
            "price": copy(
                language,
                "សូមបញ្ចូលតម្លៃអតិថិជន (USD)។",
                "Enter the customer price (USD).",
            ),
            "reseller_price": copy(
                language,
                "សូមបញ្ចូលតម្លៃ Reseller (USD)។",
                "Enter a reseller price (USD).",
            ),
            "delivery_message": copy(
                language,
                "ផ្ញើសារ ឬរបៀបប្រើដែលអតិថិជនទាំងអស់ក្នុង Catalog នេះនឹងទទួលក្រោយ Order។ ផ្ញើ - ដើម្បីលុប។",
                "Send the message or usage instructions all customers in this catalog should receive after ordering. Send - to clear.",
            ),
            "delivery_media": copy(
                language,
                "ផ្ញើ Media មួយ ឬ Album ដែលអតិថិជនទាំងអស់ក្នុង Catalog នេះនឹងទទួលក្រោយ Order។",
                "Send one media item or album all customers in this catalog should receive after ordering.",
            ),
        }
        telegram_media_steps = {"media", "delivery_media"}
        if step in telegram_media_steps:
            prompts[step] = copy(
                language,
                "ផ្ញើរូបភាព, វីដេអូ, GIF ឬឯកសារ។ Bot នឹង upload ទៅ Cloudinary ហើយរក្សាទុក URL។ អាចផ្ញើ Telegram post link ជំនួសបាន។ ចុច Save media ពេលរួច។",
                "Send an image, video, GIF, or file. The bot uploads files to Cloudinary and saves the URL. You may send a Telegram post link instead. Choose Save media when done.",
            )
        wizard["step_index"] = step_number - 1
        rows: list[list[InlineKeyboardButton]] = []
        optional = step in {
            "media",
            "description",
            "reseller_price",
            "delivery_message",
            "delivery_media",
        }
        can_skip = wizard["edit"] or optional
        if can_skip:
            rows.append(
                [
                    InlineKeyboardButton(
                        text=copy(
                            language, "⏩ រំលង / ទុកដដែល", "⏩ Skip / Keep current"
                        ),
                        callback_data=f"admin:wizard-skip:{step}",
                    )
                ]
            )
        if step in telegram_media_steps and wizard.get("telegram_media_items"):
            rows.append(
                [
                    InlineKeyboardButton(
                        text=copy(language, "✅ រក្សាទុក Media", "✅ Save media"),
                        callback_data="admin:wizard-media-save",
                    )
                ]
            )
        has_delivery_media = bool(
            values.get("delivery_media_url")
        )
        if (
            wizard["edit"]
            and step == "delivery_media"
            and has_delivery_media
        ):
            rows.append(
                [
                    InlineKeyboardButton(
                        text=copy(language, "🗑 លុប Media នេះ", "🗑 Clear this media"),
                        callback_data=f"admin:wizard-clear:{step}",
                    )
                ]
            )
        rows.append(
            [
                InlineKeyboardButton(
                    text=copy(language, "❌ បោះបង់", "❌ Cancel"),
                    callback_data="admin:wizard-cancel",
                )
            ]
        )
        await render(
            notice_text
            + f"🧭 <b>{copy(language, 'ជំហាន', 'Step')} {step_number}/{len(steps)} · {step_labels[step]}</b>\n"
            "───────────────────\n"
            f"{prompts[step]}",
            parse_mode="HTML",
            reply_markup=keyboard(rows),
        )

    async def capture_wizard_telegram_media(
        message: Message, language: str
    ) -> bool:
        if not message.from_user:
            return False
        user_id = message.from_user.id
        wizard = admin_wizards.get(user_id)
        if not wizard or wizard.get("step") not in {
            "media",
            "delivery_media",
        }:
            return False
        items = wizard.setdefault("telegram_media_items", [])
        if len(items) >= 100:
            await show_admin_wizard(
                message,
                user_id,
                language,
                edit_message=False,
                notice=copy(
                    language,
                    "មាតិកាលើសកំណត់។ សូមរក្សាទុកមួយ Message ឬមួយ Album ប៉ុណ្ណោះ។",
                    "Content limit reached. Save one message or one album only.",
                ),
                preserve_input=True,
            )
            return True
        file_item = telegram_media_upload_item(message)
        if file_item:
            items.append(file_item)
        else:
            post_link = parse_telegram_post_link(message.text or "")
            if not post_link:
                await show_admin_wizard(
                    message,
                    user_id,
                    language,
                    edit_message=False,
                    notice=copy(
                        language,
                        "សូមផ្ញើឯកសារ Media ឬ Telegram post link។",
                        "Send a media file or a Telegram post link.",
                    ),
                    preserve_input=True,
                )
                return True
            items.append({"url": (message.text or "").strip(), "media_type": "link"})
        await show_admin_wizard(
            message,
            user_id,
            language,
            edit_message=False,
            preserve_input=True,
        )
        return True

    async def show_pending_admin_media_prompt(
        user_id: int, language: str, notice: str | None = None
    ) -> None:
        pending = pending_admin_media.get(user_id)
        if not pending:
            return
        text = copy(
            language,
            "ផ្ញើរូបភាព, វីដេអូ, GIF ឬឯកសារ។ Bot នឹង upload ឯកសារទៅ Cloudinary ហើយរក្សាទុក URL ក្នុង Supabase។ អាចផ្ញើ Telegram post link ជំនួសបាន។ ចុច Save media ពេលរួច។",
            "Send an image, video, GIF, or file. The bot uploads it to Cloudinary and saves the URL in Supabase. You may send a Telegram post link instead. Choose Save media when done.",
        )
        if pending["items"]:
            text += copy(
                language,
                f"\n\nបានបន្ថែម {len(pending['items'])} Message។",
                f"\n\nAdded {len(pending['items'])} message(s).",
            )
        if notice:
            text = f"⚠️ {html.escape(notice)}\n\n{text}"
        rows: list[list[InlineKeyboardButton]] = []
        if pending["items"]:
            rows.append(
                [
                    InlineKeyboardButton(
                        text=copy(language, "✅ រក្សាទុក Media", "✅ Save media"),
                        callback_data="admin:media-save",
                    )
                ]
            )
        rows.append(back_button("admin:media-cancel", language))
        await update_active_screen(
            user_id,
            text,
            parse_mode="HTML",
            reply_markup=keyboard(rows),
        )

    async def capture_pending_admin_media(
        message: Message, language: str
    ) -> bool:
        if not message.from_user:
            return False
        user_id = message.from_user.id
        pending = pending_admin_media.get(user_id)
        if not pending:
            return False
        if len(pending["items"]) >= 100:
            await show_pending_admin_media_prompt(
                user_id,
                language,
                copy(
                    language,
                    "មាតិកាលើសកំណត់សម្រាប់ setting មួយ។",
                    "Content limit reached for one setting.",
                ),
            )
            return True
        file_item = telegram_media_upload_item(message)
        if file_item:
            pending["items"].append(file_item)
        else:
            post_link = parse_telegram_post_link(message.text or "")
            if not post_link:
                await show_pending_admin_media_prompt(
                    user_id,
                    language,
                    copy(
                        language,
                        "សូមផ្ញើឯកសារ Media ឬ Telegram post link។",
                        "Send a media file or a Telegram post link.",
                    ),
                )
                return True
            pending["items"].append(
                {"url": (message.text or "").strip(), "media_type": "link"}
            )
        await show_pending_admin_media_prompt(user_id, language)
        return True

    async def finish_admin_wizard(user_id: int, language: str) -> str:
        wizard = admin_wizards[user_id]
        values = wizard["values"]
        kind = wizard["kind"]
        if kind == "product":
            if not values.get("name") or not values.get("price"):
                raise InputValidationError(
                    copy(
                        language,
                        "ឈ្មោះ និងតម្លៃទូទៅត្រូវតែបញ្ចូល។",
                        "Product name and customer price are required.",
                    )
                )
            price = Decimal(str(values["price"]))
            reseller_price = Decimal(str(values.get("reseller_price") or price))
            if (
                not price.is_finite()
                or price <= 0
                or not reseller_price.is_finite()
                or reseller_price <= 0
            ):
                raise InputValidationError(
                    copy(
                        language,
                        "តម្លៃមិនត្រឹមត្រូវទេ។ តម្លៃ Reseller ត្រូវតែធំជាង 0។",
                        "Invalid prices. Reseller price must be greater than 0.",
                    )
                )

            media_content_ref = values.get("media_content_ref")
            media_url = media_content_ref or values.get("media_url")
            media_type = "telegram" if media_content_ref else values.get("media_type")
            if wizard["action"] == "product_add":
                if wizard.get("category_id") is None:
                    raise InputValidationError(
                        copy(
                            language,
                            "សូមជ្រើសរើស Catalog ជាមុនសិន។",
                            "Choose a catalog first.",
                        )
                    )
                await store.create_product(
                    wizard["category_id"],
                    values["name"],
                    str(price),
                    values.get("description", ""),
                    image_url=media_url if media_type != "video" else None,
                    video_url=media_url if media_type == "video" else None,
                    reseller_price=str(reseller_price),
                )
                return f"ac:{wizard['category_id']}"

            await store.update_product(
                wizard["target_id"],
                values["name"],
                str(price),
                str(reseller_price),
                values.get("description", ""),
                image_url=media_url if media_type != "video" else None,
                video_url=media_url if media_type == "video" else None,
                update_media=media_type is not None,
            )
            return f"ap:{wizard['target_id']}"

        if not values.get("name"):
            raise InputValidationError(
                copy(
                    language,
                    "ឈ្មោះ Catalog ត្រូវតែបញ្ចូល។",
                    "Catalog name is required.",
                )
            )
        category_values = {
            "name": values["name"],
            "description": values.get("description", ""),
            "delivery_message": values.get("delivery_message", ""),
            "delivery_media_url": values.get("delivery_media_url"),
        }
        if wizard["action"] == "category_add":
            await store.create_category(**category_values)
            return "admin:catalogs"
        await store.update_category(wizard["target_id"], **category_values)
        return f"ac:{wizard['target_id']}"

    @router.callback_query(F.data == "home")
    async def home(callback: CallbackQuery) -> None:
        await callback.answer()
        pending_wallet_amount.discard(callback.from_user.id)
        pending_admin_media.pop(callback.from_user.id, None)
        language = await safe_user_language(store, callback.from_user.id)
        welcome_message = await send_home_screen(
            bot, callback.from_user, store, settings, language
        )
        welcome_message_ids[callback.from_user.id] = welcome_message.message_id

    @router.callback_query(F.data == "language")
    async def language_menu(callback: CallbackQuery) -> None:
        await callback.answer()
        language = await safe_user_language(store, callback.from_user.id)
        rows = [
            [InlineKeyboardButton(text="🇰🇭 ភាសាខ្មែរ", callback_data="lang:km")],
            [InlineKeyboardButton(text="🇺🇸 English", callback_data="lang:en")],
            back_button("home", language),
        ]
        await callback.message.edit_text(
            copy(language, "🌐 សូមជ្រើសរើសភាសា៖", "🌐 Choose your language:"),
            reply_markup=keyboard(rows),
        )

    @router.callback_query(F.data.startswith("lang:"))
    async def set_language(callback: CallbackQuery) -> None:
        language = normalize_language(callback.data[5:])
        if language not in {"km", "en"}:
            await callback.answer("Unsupported language", show_alert=True)
            return
        user_language_cache[callback.from_user.id] = language
        try:
            await store.set_user_language(callback.from_user.id, language)
        except Exception:
            logger.exception(
                "Could not save language preference for Telegram user %s",
                callback.from_user.id,
            )
        await safe_upsert_user(
            store,
            callback.from_user.id,
            callback.from_user.username,
            callback.from_user.first_name,
            language,
        )
        await callback.answer()
        welcome_message = await send_home_screen(
            bot, callback.from_user, store, settings, language
        )
        welcome_message_ids[callback.from_user.id] = welcome_message.message_id

    @router.callback_query(F.data.in_({"how_to_buy", "guide"}))
    async def buying_guide(callback: CallbackQuery) -> None:
        await callback.answer()
        language = await safe_user_language(store, callback.from_user.id)
        await show_how_to_buy(callback.message, language)
        await delete_replaced_message(callback.message)

    @router.callback_query(F.data == "join_group")
    async def join_group(callback: CallbackQuery) -> None:
        await callback.answer()
        language = await safe_user_language(store, callback.from_user.id)
        await show_customer_action(
            callback.message, callback.from_user, "join group", language
        )
        await delete_replaced_message(callback.message)

    @router.callback_query(F.data == "wallet:add")
    async def wallet_add_prompt(callback: CallbackQuery) -> None:
        await callback.answer()
        language = await safe_user_language(store, callback.from_user.id)
        sent = await edit_text_message(
            callback.message,
            copy(
                language,
                "💰 <b>ជ្រើសរើសចំនួនទឹកប្រាក់ដែលអ្នកចង់បញ្ចូល៖</b>",
                "💰 <b>Choose the amount you want to deposit:</b>",
            ),
            parse_mode="HTML",
            reply_markup=wallet_deposit_keyboard(language),
        )
        await switch_active_screen(callback.from_user.id, sent)

    @router.callback_query(F.data == "wallet:custom")
    async def wallet_custom_prompt(callback: CallbackQuery) -> None:
        pending_wallet_amount.add(callback.from_user.id)
        language = await safe_user_language(store, callback.from_user.id)
        await callback.answer()
        sent = await edit_text_message(
            callback.message,
            wallet_custom_amount_prompt(language),
            reply_markup=keyboard([back_button("home", language)]),
        )
        await switch_active_screen(callback.from_user.id, sent)

    @router.callback_query(F.data.startswith("wallet:amount:"))
    async def wallet_selected_amount(callback: CallbackQuery) -> None:
        language = await safe_user_language(store, callback.from_user.id)
        amount = callback.data.removeprefix("wallet:amount:")
        await callback.answer()
        if len(payments.available_methods) > 1:
            await edit_text_message(
                callback.message,
                copy(
                    language,
                    "សូមជ្រើសរើសវិធីទូទាត់សម្រាប់ការបញ្ចូលប្រាក់។",
                    "Choose a payment method for this deposit.",
                ),
                reply_markup=wallet_payment_method_keyboard(
                    amount, payments.available_methods, language
                ),
            )
            return
        await clear_active_screen(callback.from_user.id)
        await delete_replaced_message(callback.message)
        try:
            await start_wallet_deposit(
                callback.from_user, amount, language, payments.default_method()
            )
        except Exception:
            logger.exception(
                "Could not start wallet deposit for Telegram user %s",
                callback.from_user.id,
            )
            await bot.send_message(
                callback.from_user.id,
                wallet_error_message(language),
                reply_markup=welcome_keyboard(callback.from_user.id, settings),
            )

    @router.callback_query(F.data.startswith("wallet:method:"))
    async def wallet_method_selected(callback: CallbackQuery) -> None:
        language = await safe_user_language(store, callback.from_user.id)
        parts = callback.data.split(":", 3)
        if len(parts) != 4:
            await callback.answer(
                copy(language, "វិធីទូទាត់មិនត្រឹមត្រូវទេ។", "Invalid payment method."),
                show_alert=True,
            )
            return
        _, _, payment_method_text, amount = parts
        try:
            payment_method = payments.require_method(payment_method_text)
        except RuntimeError:
            await callback.answer(
                copy(language, "វិធីទូទាត់នេះមិនមានទេ។", "Payment method unavailable."),
                show_alert=True,
            )
            return
        await callback.answer()
        await clear_active_screen(callback.from_user.id)
        await delete_replaced_message(callback.message)
        try:
            await start_wallet_deposit(
                callback.from_user, amount, language, payment_method
            )
        except Exception:
            logger.exception(
                "Could not start wallet deposit for Telegram user %s",
                callback.from_user.id,
            )
            await bot.send_message(
                callback.from_user.id,
                wallet_error_message(language),
                reply_markup=welcome_keyboard(callback.from_user.id, settings),
            )

    @router.callback_query(F.data.startswith("wallet:check:"))
    async def wallet_check_payment(callback: CallbackQuery) -> None:
        deposit_id = callback.data.removeprefix("wallet:check:")
        language = await safe_user_language(store, callback.from_user.id)
        deposit = await store.wallet_deposit_for_user(deposit_id, callback.from_user.id)
        if not deposit:
            await callback.answer(
                copy(language, "រកមិនឃើញការបញ្ចូលប្រាក់នេះទេ។", "Deposit not found."),
                show_alert=True,
            )
            return
        try:
            payment = await payments.check_payment(
                str(deposit["transaction_id"]), deposit.get("payment_provider")
            )
            status = payment.get("status")
            if status == "paid":
                if Decimal(str(payment.get("amount"))) != Decimal(
                    str(deposit["amount"])
                ):
                    await callback.answer(
                        copy(
                            language,
                            "ចំនួនទឹកប្រាក់មិនត្រូវគ្នា។",
                            "Payment amount mismatch.",
                        ),
                        show_alert=True,
                    )
                    return
                completed = await complete_wallet_deposit(bot, store, deposit, language)
                await callback.answer(
                    (
                        copy(
                            language,
                            "បានបញ្ចូលប្រាក់ជោគជ័យ។",
                            "Wallet credited successfully.",
                        )
                        if completed
                        else copy(
                            language,
                            "ការបញ្ចូលប្រាក់នេះបានដំណើរការរួចហើយ។",
                            "This deposit was already processed.",
                        )
                    ),
                    show_alert=True,
                )
            elif status in {"expired", "failed"}:
                await expire_wallet_deposit(bot, store, deposit, status)
                await callback.answer(
                    copy(
                        language, "ការទូទាត់មិនបានបញ្ចប់។", "Payment was not completed."
                    ),
                    show_alert=True,
                )
            else:
                await callback.answer(
                    copy(
                        language,
                        "មិនទាន់ទទួលបានការទូទាត់ទេ។",
                        "Payment has not been received yet.",
                    ),
                    show_alert=True,
                )
        except Exception:
            logger.exception("Could not check wallet deposit %s", deposit_id)
            await callback.answer(
                copy(
                    language, "មិនអាចពិនិត្យការទូទាត់បានទេ។", "Could not check payment."
                ),
                show_alert=True,
            )

    @router.callback_query(F.data.startswith("wallet:cancel:"))
    async def cancel_wallet_deposit_callback(callback: CallbackQuery) -> None:
        deposit_id = callback.data.removeprefix("wallet:cancel:")
        language = await safe_user_language(store, callback.from_user.id)
        deposit = await store.wallet_deposit_for_user(
            deposit_id, callback.from_user.id
        )
        if not deposit:
            await callback.answer(
                copy(language, "រកមិនឃើញការបញ្ចូលប្រាក់នេះទេ។", "Deposit not found."),
                show_alert=True,
            )
            return
        if deposit["status"] != "pending":
            await callback.answer(
                copy(
                    language,
                    "ការបញ្ចូលប្រាក់នេះមិនអាចបោះបង់បានទេ។",
                    "This deposit can no longer be cancelled.",
                ),
                show_alert=True,
            )
            return
        if deposit.get("transaction_id"):
            try:
                payment = await payments.check_payment(
                    str(deposit["transaction_id"]), deposit.get("payment_provider")
                )
            except Exception:
                logger.exception(
                    "Could not verify payment before cancelling wallet deposit %s",
                    deposit_id,
                )
                await callback.answer(
                    copy(
                        language,
                        "មិនអាចផ្ទៀងផ្ទាត់ការទូទាត់បានទេ។ សូមសាកល្បងម្ដងទៀត។",
                        "Could not verify payment. Please try again.",
                    ),
                    show_alert=True,
                )
                return
            status = payment.get("status")
            if status == "paid":
                if Decimal(str(payment.get("amount"))) != Decimal(
                    str(deposit["amount"])
                ):
                    await callback.answer(
                        copy(
                            language,
                            "ចំនួនទឹកប្រាក់មិនត្រូវគ្នា។",
                            "Payment amount mismatch.",
                        ),
                        show_alert=True,
                    )
                    return
                completed = await complete_wallet_deposit(
                    bot, store, deposit, language
                )
                await callback.answer(
                    copy(
                        language,
                        "បានបញ្ចូលប្រាក់ជោគជ័យ។"
                        if completed
                        else "ការបញ្ចូលប្រាក់នេះបានដំណើរការរួចហើយ។",
                        "Wallet credited successfully."
                        if completed
                        else "This deposit was already processed.",
                    ),
                    show_alert=True,
                )
                return
            if status in {"expired", "failed"}:
                await expire_wallet_deposit(bot, store, deposit, status)
                await callback.answer(
                    copy(
                        language,
                        "ការទូទាត់មិនបានបញ្ចប់។",
                        "Payment was not completed.",
                    ),
                    show_alert=True,
                )
                return
        cancelled = await store.cancel_wallet_deposit(deposit_id)
        if not cancelled:
            current_deposit = await store.wallet_deposit_for_user(
                deposit_id, callback.from_user.id
            )
            if current_deposit and current_deposit["status"] == "paid":
                message = copy(
                    language,
                    "ការបញ្ចូលប្រាក់នេះបានដំណើរការរួចហើយ។",
                    "This deposit was already processed.",
                )
            else:
                message = copy(
                    language,
                    "ការបញ្ចូលប្រាក់នេះមិនអាចបោះបង់បានទេ។",
                    "This deposit can no longer be cancelled.",
                )
            await callback.answer(message, show_alert=True)
            return
        await close_wallet_deposit_message(bot, store, deposit_id)
        await callback.answer(
            copy(language, "បានបោះបង់ការបញ្ចូលប្រាក់។", "Deposit cancelled.")
        )
        await callback.message.answer(
            copy(language, "បានបោះបង់ការបញ្ចូលប្រាក់។", "Deposit cancelled."),
            reply_markup=welcome_keyboard(callback.from_user.id, settings),
        )

    @router.callback_query(F.data == "shop")
    async def shop(callback: CallbackQuery) -> None:
        await callback.answer()
        language = await safe_user_language(store, callback.from_user.id)
        await show_catalogs(callback.message, 0, language)

    @router.callback_query(F.data.startswith("shop:catalogs:"))
    async def shop_catalog_page(callback: CallbackQuery) -> None:
        await callback.answer()
        language = await safe_user_language(store, callback.from_user.id)
        await show_catalogs(
            callback.message, int(callback.data.rsplit(":", 1)[1]), language
        )

    @router.callback_query(F.data.startswith("shop:packages:"))
    async def shop_package_page(callback: CallbackQuery) -> None:
        await callback.answer()
        language = await safe_user_language(store, callback.from_user.id)
        category_id, page = callback.data[len("shop:packages:") :].rsplit(":", 1)
        is_reseller = await store.is_reseller(callback.from_user.id)
        await show_packages(
            callback.message, category_id, int(page), language, is_reseller
        )

    @router.callback_query(F.data.startswith("c:"))
    async def category(callback: CallbackQuery) -> None:
        await callback.answer()
        language = await safe_user_language(store, callback.from_user.id)
        category_id = callback.data[2:]
        is_reseller = await store.is_reseller(callback.from_user.id)
        await show_packages(callback.message, category_id, 0, language, is_reseller)

    @router.callback_query(F.data.startswith("p:"))
    async def product(callback: CallbackQuery) -> None:
        await callback.answer()
        language = await safe_user_language(store, callback.from_user.id)
        product_data = await store.product(callback.data[2:])
        if not product_data:
            await callback.message.edit_text(
                copy(
                    language,
                    "ផលិតផលនេះមិនមានទៀតទេ។",
                    "This product is no longer available.",
                ),
                reply_markup=keyboard([back_button("shop", language)]),
            )
            return
        is_reseller = await store.is_reseller(callback.from_user.id)
        await show_product(
            callback.message, product_data, 1, language, is_reseller=is_reseller
        )

    async def show_product(
        message: Message,
        product_data: dict[str, Any],
        quantity: int,
        language: str,
        edit_message: bool = True,
        is_reseller: bool = False,
    ) -> Message | None:
        stock = await store.available_stock(product_data["id"])
        max_quantity = quantity_limit(stock)
        quantity = min(max(quantity, 1), max_quantity) if max_quantity else 0
        category_data = await store.category(product_data["category_id"])
        unit_price = product_price(product_data, is_reseller)
        rows: list[list[InlineKeyboardButton]] = []
        if max_quantity:
            rows.append(
                [
                    InlineKeyboardButton(
                        text="➖" if quantity > 1 else "·",
                        callback_data=(
                            f"qty:{product_data['id']}:{quantity - 1}"
                            if quantity > 1
                            else "noop"
                        ),
                    ),
                    InlineKeyboardButton(
                        text=f"{copy(language, 'ចំនួន', 'Qty')}: {quantity}",
                        callback_data="noop",
                    ),
                    InlineKeyboardButton(
                        text="➕" if quantity < max_quantity else "·",
                        callback_data=(
                            f"qty:{product_data['id']}:{quantity + 1}"
                            if quantity < max_quantity
                            else "noop"
                        ),
                    ),
                ]
            )
            total = unit_price * quantity
            wallet_balance = Decimal(
                str(await store.wallet_balance(message.chat.id if message.chat else 0))
            )
            rows.extend(
                checkout_payment_rows(
                    product_data["id"],
                    quantity,
                    total,
                    wallet_balance,
                    language,
                    payments.available_methods,
                )
            )
        else:
            rows.append(
                [
                    InlineKeyboardButton(
                        text=copy(language, "អស់ស្តុក", "Out of stock"),
                        callback_data="noop",
                        style="danger",
                    )
                ]
            )
        rows.append(back_button(f"c:{product_data['category_id']}", language))
        category_title = (
            f"🛍 {html.escape(category_data['name'])}\n" if category_data else ""
        )
        description = description_preview(
            product_data.get("description") or "", language
        )
        unit_price_label = (
            copy(language, "តម្លៃឯកតា Reseller", "Reseller unit price")
            if is_reseller
            else copy(language, "តម្លៃឯកតា", "Unit price")
        )
        text = (
            f"{category_title}"
            f"🛒 <b>{copy(language, 'បញ្ជាក់ការបញ្ជាទិញ', 'Order details')}</b>\n"
            "───────────────────\n"
            f"• {copy(language, 'ផលិតផល', 'Product')}: {html.escape(product_data['name'])}\n"
            f"• {unit_price_label}: {money(unit_price)}\n"
            f"• {copy(language, 'ស្តុកមាន', 'Available stock')}: {stock}\n\n"
            f"<b>{copy(language, 'ព័ត៌មានលម្អិត', 'Product details')}</b>\n"
            f"{description}\n"
            "───────────────────"
        )
        markup = keyboard(rows)
        video_url = product_data.get("video_url")
        image_url = product_data.get("image_url")
        media_url = video_url or image_url
        sent_message: Message | None = None
        if media_url:
            reference = stored_telegram_content_reference(media_url)
            if reference:
                copied = await copy_telegram_content_reference(
                    bot,
                    message.chat.id if message.chat else message.from_user.id,
                    reference,
                )
                sent_message = await message.answer(
                    text, parse_mode="HTML", reply_markup=markup
                )
                if message.from_user:
                    await switch_active_screen(
                        message.from_user.id,
                        sent_message,
                        None if edit_message else message,
                    )
                    active_screen_auxiliary_message_ids[message.from_user.id] = [
                        item.message_id for item in copied
                    ]
                return sent_message
            media_urls = cloudinary_media_urls(media_url)
            media_method = (
                cloudinary_media_method(
                    media_urls[0], "video" if video_url else "photo"
                )
                if media_urls
                else "document"
            )
            if (
                edit_message
                and (message.photo or message.video)
                and len(media_urls) == 1
                and media_method in {"photo", "video"}
            ):
                media = (
                    InputMediaVideo(media=media_url, caption=text, parse_mode="HTML")
                    if media_method == "video"
                    else InputMediaPhoto(
                        media=media_url, caption=text, parse_mode="HTML"
                    )
                )
                await message.edit_media(
                    media=media,
                    reply_markup=markup,
                )
            else:
                sent_media = await send_cloudinary_media(
                    message,
                    media_url,
                    fallback="video" if video_url else "photo",
                    caption=text,
                    parse_mode="HTML",
                    reply_markup=markup,
                )
                if sent_media:
                    sent_message = sent_media[0]
                    if message.from_user and len(sent_media) > 1:
                        active_screen_auxiliary_message_ids[
                            message.from_user.id
                        ] = [item.message_id for item in sent_media[1:]]
                if edit_message:
                    await delete_replaced_message(message)
        elif edit_message and (message.photo or message.video):
            await message.edit_caption(
                caption=text, parse_mode="HTML", reply_markup=markup
            )
        elif not edit_message:
            sent_message = await message.answer(
                text, parse_mode="HTML", reply_markup=markup
            )
        else:
            await message.edit_text(text, parse_mode="HTML", reply_markup=markup)
        if sent_message and not edit_message and message.from_user:
            return await switch_active_screen(
                message.from_user.id, sent_message, message
            )
        return sent_message

    @router.callback_query(F.data.startswith("qty:"))
    async def change_quantity(callback: CallbackQuery) -> None:
        await callback.answer()
        language = await safe_user_language(store, callback.from_user.id)
        _, product_id, quantity_text = callback.data.split(":")
        product_data = await store.product(product_id)
        if not product_data:
            await callback.message.edit_text(
                copy(
                    language,
                    "ផលិតផលនេះមិនមានទៀតទេ។",
                    "This product is no longer available.",
                ),
                reply_markup=keyboard([back_button("shop", language)]),
            )
            return
        is_reseller = await store.is_reseller(callback.from_user.id)
        await show_product(
            callback.message,
            product_data,
            int(quantity_text),
            language,
            is_reseller=is_reseller,
        )

    @router.callback_query(F.data == "noop")
    async def no_action(callback: CallbackQuery) -> None:
        await callback.answer()

    @router.callback_query(F.data.startswith("b:"))
    async def buy(callback: CallbackQuery) -> None:
        language = await safe_user_language(store, callback.from_user.id)
        await callback.answer(
            copy(language, "កំពុងបង្កើតការទូទាត់...", "Creating payment...")
        )
        order = None
        try:
            parts = callback.data.split(":")
            if len(parts) == 4:
                payment_method = payments.require_method(parts[1])
                _, _, product_id, quantity_text = parts
            elif len(parts) == 3:
                payment_method = payments.default_method()
                _, product_id, quantity_text = parts
            else:
                raise RuntimeError("Invalid payment method callback")
            product_data = await store.product(product_id)
            if not product_data:
                raise RuntimeError("Product is no longer available")
            order = await store.reserve_order(
                callback.from_user.id, product_id, int(quantity_text)
            )
            payment = await payments.create_payment(
                str(order["total"]),
                str(order["id"]),
                payment_method,
            )
            payment_url = (
                aba_mobile_deeplink(payment["qr_string"])
                if payment_method == "aba"
                else None
            )
            open_url = (
                aba_mobile_redirect_url(settings.app_base_url or "", str(order["id"]))
                if payment_method == "aba"
                else payment.get("checkout_url")
            )
            await store.set_payment(
                order["id"],
                payment["transaction_id"],
                payment_url,
                payment_method,
            )
        except Exception:
            logger.exception(
                "Could not create checkout for Telegram user %s", callback.from_user.id
            )
            if order:
                await store.release_order(str(order["id"]))
            await edit_text_message(
                callback.message,
                copy(
                    language,
                    "មិនអាចបង្កើតការទូទាត់បានទេ។ សូមព្យាយាមម្ដងទៀត។",
                    "Could not create payment. Please try again.",
                ),
                reply_markup=welcome_keyboard(callback.from_user.id, settings),
            )
            return
        caption = payment_caption(
            str(product_data["name"]),
            int(quantity_text),
            order["total"],
            language,
        )
        payment_message = await bot.send_photo(
            callback.from_user.id,
            payment_qr(payment),
            caption=caption,
            parse_mode="HTML",
            reply_markup=payment_keyboard(
                str(order["id"]), open_url, language, payment_method
            ),
        )
        await delete_replaced_message(callback.message)
        await track_payment_message(
            bot, store, order["id"], callback.from_user.id, payment_message.message_id
        )

    @router.callback_query(F.data.startswith("bw:"))
    async def buy_with_wallet(callback: CallbackQuery) -> None:
        language = await safe_user_language(store, callback.from_user.id)
        await callback.answer(
            copy(language, "កំពុងបញ្ជាក់សមតុល្យ...", "Checking wallet balance...")
        )
        _, product_id, quantity_text = callback.data.split(":")
        try:
            order = await store.reserve_wallet_order(
                callback.from_user.id,
                product_id,
                int(quantity_text),
            )
            await delete_replaced_message(callback.message)
            await deliver_order(
                bot, store, settings, str(order["id"]), callback.from_user.id
            )
        except Exception:
            logger.exception(
                "Could not complete wallet purchase for Telegram user %s",
                callback.from_user.id,
            )
            await callback.message.answer(
                copy(
                    language,
                    "ការទិញដោយសមតុល្យមិនបានសម្រេចទេ។ សូមពិនិត្យសមតុល្យ/ស្តុក ឬទាក់ទងជំនួយ។",
                    "Wallet purchase failed. Check your balance/stock or contact support.",
                ),
                reply_markup=welcome_keyboard(callback.from_user.id, settings),
            )

    @router.callback_query(F.data.startswith("cancel:"))
    async def cancel_order(callback: CallbackQuery) -> None:
        await callback.answer()
        order_id = callback.data[7:]
        result = await (
            store.client.table("orders")
            .select("status,transaction_id,payment_provider")
            .eq("id", order_id)
            .eq("chat_id", callback.from_user.id)
            .maybe_single()
            .execute()
        )
        data = result.data
        language = await safe_user_language(store, callback.from_user.id)
        if not data:
            await callback.message.answer(
                copy(language, "រកមិនឃើញការបញ្ជាទិញទេ។", "Order not found.")
            )
            return
        if data["status"] != "pending":
            await callback.message.answer(
                copy(
                    language,
                    "ការបញ្ជាទិញនេះមិនអាចលុបចោលបានទេ។",
                    "This order can no longer be cancelled.",
                )
            )
            return
        if data.get("transaction_id"):
            try:
                payment_status = (
                    await payments.check_payment(
                        data["transaction_id"], data.get("payment_provider")
                    )
                ).get("status")
            except Exception:
                logger.exception(
                    "Could not verify payment before cancellation for order %s",
                    order_id,
                )
                await callback.message.answer(
                    copy(
                        language,
                        "មិនអាចផ្ទៀងផ្ទាត់ការទូទាត់បានទេ។ សូមសាកល្បងម្ដងទៀត។",
                        "Could not verify payment. Please try again.",
                    )
                )
                return
            if payment_status == "paid":
                await deliver_order(
                    bot, store, settings, order_id, callback.from_user.id
                )
                return
        await delete_payment_message(bot, store, order_id)
        await store.release_order(order_id)
        await callback.message.answer(
            copy(language, "បានលុបចោលការបញ្ជាទិញ។", "Order cancelled."),
            reply_markup=keyboard([back_button("home", language)]),
        )

    @router.callback_query(F.data.startswith("check_payment_"))
    async def check_order(callback: CallbackQuery) -> None:
        language = await safe_user_language(store, callback.from_user.id)
        await callback.answer(
            copy(language, "កំពុងពិនិត្យការទូទាត់…", "Checking payment…")
        )
        order_id = callback.data.removeprefix("check_payment_")
        try:
            result = await (
                store.client.table("orders")
                .select("transaction_id,chat_id,status,payment_provider")
                .eq("id", order_id)
                .eq("chat_id", callback.from_user.id)
                .maybe_single()
                .execute()
            )
            data = result.data
            if not data:
                await callback.message.answer(
                    copy(language, "រកមិនឃើញការបញ្ជាទិញទេ។", "Order not found.")
                )
                return
            if data["status"] == "paid":
                await deliver_order(
                    bot, store, settings, order_id, callback.from_user.id
                )
                return
            if data["status"] != "pending":
                await callback.message.answer(
                    copy(
                        language,
                        "ការបញ្ជាទិញនេះមិនសកម្មទៀតទេ។",
                        "This order is no longer active.",
                    )
                )
                return
            payment = await payments.check_payment(
                data["transaction_id"], data.get("payment_provider")
            )
            status = payment.get("status")
            if status == "paid":
                await deliver_order(
                    bot, store, settings, order_id, callback.from_user.id
                )
            elif status in {"expired", "failed"}:
                await expire_payment(
                    bot, store, order_id, callback.from_user.id, status
                )
            else:
                await callback.message.answer(
                    copy(
                        language,
                        "មិនទាន់ទទួលបានការទូទាត់ទេ។ សូមបញ្ចប់ការទូទាត់។",
                        "Payment has not arrived yet. Please complete your payment.",
                    )
                )
        except Exception:
            logger.exception("Payment check failed for order %s", order_id)
            await callback.message.answer(
                copy(
                    language,
                    "មិនអាចពិនិត្យការទូទាត់បានទេ។ សូមព្យាយាមម្ដងទៀតបន្តិចក្រោយ។",
                    "Could not check payment. Please try again shortly.",
                )
            )

    @router.callback_query(F.data == "history")
    async def history(callback: CallbackQuery) -> None:
        await callback.answer()
        language = await safe_user_language(store, callback.from_user.id)
        await show_order_history(callback.message, callback.from_user.id, 0, language)

    @router.callback_query(F.data.startswith("history:page:"))
    async def history_page(callback: CallbackQuery) -> None:
        language = await safe_user_language(store, callback.from_user.id)
        try:
            page = int(callback.data.removeprefix("history:page:"))
            if page < 0:
                raise ValueError
        except ValueError:
            await callback.answer(
                copy(language, "ទំព័រមិនត្រឹមត្រូវទេ។", "Invalid order history page."),
                show_alert=True,
            )
            return
        await callback.answer()
        await show_order_history(
            callback.message, callback.from_user.id, page, language
        )

    @router.callback_query(F.data == "support")
    async def support(callback: CallbackQuery) -> None:
        await callback.answer()
        language = await safe_user_language(store, callback.from_user.id)
        try:
            support_username = (await store.bot_settings(("support_username",))).get(
                "support_username", ""
            )
        except Exception:
            logger.exception("Could not load support username from bot settings")
            await callback.message.edit_text(
                copy(
                    language,
                    "មិនអាចទាញយកការកំណត់ជំនួយបានទេ។",
                    "Could not load support settings.",
                ),
                reply_markup=keyboard([back_button(language=language)]),
            )
            return
        if support_username:
            rows = [
                [
                    InlineKeyboardButton(
                        text=copy(
                            language, "បើកការជជែកជាមួយជំនួយ", "Chat with support"
                        ),
                        url=f"https://t.me/{support_username.lstrip('@')}",
                    )
                ],
                back_button(language=language),
            ]
            await callback.message.edit_text(
                copy(language, "សូមទាក់ទងក្រុមជំនួយ៖", "Contact support:"),
                reply_markup=keyboard(rows),
            )
        else:
            await callback.message.edit_text(
                copy(
                    language,
                    "សូមទាក់ទងអ្នកគ្រប់គ្រងហាង។",
                    "Please contact the store administrator.",
                ),
                reply_markup=keyboard([back_button(language=language)]),
            )

    @router.callback_query(F.data == "admin")
    async def admin(callback: CallbackQuery) -> None:
        await callback.answer()
        language = await safe_user_language(store, callback.from_user.id)
        if callback.from_user.id not in settings.admin_ids:
            await callback.message.edit_text(
                copy(
                    language,
                    "អ្នកមិនមានសិទ្ធិប្រើប្រាស់ទេ។",
                    "You are not authorized to use this.",
                ),
                reply_markup=keyboard([back_button(language=language)]),
            )
            return
        result = (
            await store.client.table("products")
            .select("id,name")
            .eq("active", True)
            .order("sort_order")
            .execute()
        )
        products = result.data or []
        stock_counts = await asyncio.gather(
            *(store.available_stock(item["id"]) for item in products)
        )
        lines = [
            f"• {html.escape(item['name'])}: {stock} {copy(language, 'នៅសល់', 'available')}"
            for item, stock in zip(products, stock_counts)
        ]
        total_stock = sum(stock_counts)
        rows = [
            [
                InlineKeyboardButton(
                    text=copy(
                        language,
                        "🗂 គ្រប់គ្រង Catalog និង Package",
                        "🗂 Manage catalogs and packages",
                    ),
                    callback_data="admin:catalogs",
                )
            ],
            [
                InlineKeyboardButton(
                    text=copy(language, "👥 គ្រប់គ្រង Reseller", "👥 Manage resellers"),
                    callback_data="admin:resellers",
                )
            ],
            [
                InlineKeyboardButton(
                    text=copy(
                        language, "📥 បញ្ចូលស្តុក (.txt)", "📥 Import stock (.txt)"
                    ),
                    callback_data="admin:upload",
                )
            ],
            [
                InlineKeyboardButton(
                    text=copy(language, "📣 Broadcast", "📣 Broadcast"),
                    callback_data="admin:broadcast",
                )
            ],
            [
                InlineKeyboardButton(
                    text=copy(
                        language, "🕒 Scheduled Broadcasts", "🕒 Scheduled Broadcasts"
                    ),
                    callback_data="admin:broadcast:scheduled",
                )
            ],
            [
                InlineKeyboardButton(
                    text=copy(language, "⚙️ Settings", "⚙️ Settings"),
                    callback_data="admin:settings",
                )
            ],
            [
                InlineKeyboardButton(
                    text=copy(language, "📊 ស្ថិតិការលក់", "📊 Sales summary"),
                    callback_data="admin:sales",
                )
            ],
            back_button(language=language),
        ]
        await callback.message.edit_text(
            f"⚙️ <b>{copy(language, 'ផ្ទាំងគ្រប់គ្រងហាង', 'Store admin')}</b>\n"
            f"📦 <b>{copy(language, 'ស្តុកដែលអាចលក់បាន៖', 'Available stock:')} {total_stock}</b>\n"
            + (
                "\n".join(lines)
                or copy(language, "មិនមាន Package សកម្មទេ។", "No active packages.")
            ),
            parse_mode="HTML",
            reply_markup=keyboard(rows),
        )

    @router.callback_query(F.data == "admin:broadcast")
    async def start_broadcast(callback: CallbackQuery) -> None:
        language = await safe_user_language(store, callback.from_user.id)
        if callback.from_user.id not in settings.admin_ids:
            await callback.answer(
                copy(language, "អ្នកមិនមានសិទ្ធិប្រើប្រាស់ទេ។", "Not authorized."),
                show_alert=True,
            )
            return
        if callback.from_user.id in broadcast_active_keys:
            await callback.answer(
                copy(
                    language,
                    "Broadcast កំពុងដំណើរការរួចហើយ។",
                    "A broadcast is already running for this admin.",
                ),
                show_alert=True,
            )
            return
        old_draft = broadcast_drafts.pop(callback.from_user.id, None)
        if old_draft:
            await delete_broadcast_preview(callback.from_user.id, old_draft)
            await delete_broadcast_source(
                bot, callback.from_user.id, old_draft["content"]
            )
        broadcast_drafts[callback.from_user.id] = {
            "stage": "content",
            "content": [],
            "buttons": [],
        }
        await callback.answer()
        await replace_callback_screen(
            callback,
            copy(
                language,
                "📣 <b>បង្កើត Broadcast</b>\n"
                "ផ្ញើអត្ថបទ Media/ឯកសារប្រភេទណាក៏បាន ឬ Album; ក៏អាចផ្ញើ Telegram post link (t.me/channel/123)។\n"
                "Telegram នឹង copy media ដោយផ្ទាល់ ដូច្នេះមិនចាំបាច់ download/upload ឬកំណត់ទំហំក្នុង bot ទេ។\n"
                "ពេលរួច សូមចុច Preview; អាចបន្ថែម URL Buttons ឬកំណត់ពេលផ្ញើបាន។",
                "📣 <b>Create a broadcast</b>\n"
                "Send text, any media/file type, or albums; you can also paste a Telegram post link (t.me/channel/123).\n"
                "Telegram copies the media directly, so the bot does not download/upload it or impose its own size limit.\n"
                "When finished, choose Preview; you can add URL buttons or schedule delivery.",
            ),
            broadcast_draft_keyboard("content", language),
            parse_mode="HTML",
        )

    @router.callback_query(F.data == "admin:broadcast:preview")
    async def broadcast_preview_callback(callback: CallbackQuery) -> None:
        language = await safe_user_language(store, callback.from_user.id)
        if callback.from_user.id not in settings.admin_ids:
            await callback.answer(
                copy(language, "អ្នកមិនមានសិទ្ធិប្រើប្រាស់ទេ។", "Not authorized."),
                show_alert=True,
            )
            return
        if callback.from_user.id not in broadcast_drafts:
            await callback.answer(
                copy(
                    language, "មិនមាន Broadcast draft ទេ។", "No active broadcast draft."
                ),
                show_alert=True,
            )
            return
        await callback.answer()
        await preview_broadcast(callback.from_user.id, language)

    @router.callback_query(F.data == "admin:broadcast:buttons")
    async def broadcast_buttons_callback(callback: CallbackQuery) -> None:
        language = await safe_user_language(store, callback.from_user.id)
        if callback.from_user.id not in settings.admin_ids:
            await callback.answer(
                copy(language, "អ្នកមិនមានសិទ្ធិប្រើប្រាស់ទេ។", "Not authorized."),
                show_alert=True,
            )
            return
        draft = broadcast_drafts.get(callback.from_user.id)
        if not draft:
            await callback.answer(
                copy(
                    language, "មិនមាន Broadcast draft ទេ។", "No active broadcast draft."
                ),
                show_alert=True,
            )
            return
        await delete_broadcast_preview(callback.from_user.id, draft)
        draft["stage"] = "buttons"
        await callback.answer()
        await replace_callback_screen(
            callback,
            copy(
                language,
                "ផ្ញើ URL Buttons មួយក្នុងមួយបន្ទាត់ តាមទម្រង់៖\n<code>ឈ្មោះប៊ូតុង | https://example.com</code>\nអតិបរមា 8 ប៊ូតុង។ ផ្ញើ <code>-</code> ដើម្បីលុប Buttons។",
                "Send URL buttons one per line:\n<code>Button label | https://example.com</code>\nUp to 8 buttons. Send <code>-</code> to remove buttons.",
            ),
            broadcast_draft_keyboard("buttons", language),
            parse_mode="HTML",
        )

    @router.callback_query(F.data == "admin:broadcast:continue")
    async def continue_broadcast(callback: CallbackQuery) -> None:
        language = await safe_user_language(store, callback.from_user.id)
        draft = broadcast_drafts.get(callback.from_user.id)
        if callback.from_user.id not in settings.admin_ids or not draft:
            await callback.answer(
                copy(
                    language,
                    "រកមិនឃើញ Broadcast draft ទេ។",
                    "Broadcast draft not found.",
                ),
                show_alert=True,
            )
            return
        await delete_broadcast_preview(callback.from_user.id, draft)
        draft["stage"] = "content"
        await callback.answer()
        await replace_callback_screen(
            callback,
            copy(
                language,
                "បន្តផ្ញើមាតិកា Broadcast ឬចុច Preview ពេលរួច។",
                "Send more broadcast content or choose Preview when finished.",
            ),
            broadcast_draft_keyboard("content", language),
        )

    @router.callback_query(F.data == "admin:broadcast:cancel")
    async def cancel_broadcast_draft(callback: CallbackQuery) -> None:
        language = await safe_user_language(store, callback.from_user.id)
        draft = broadcast_drafts.pop(callback.from_user.id, None)
        if callback.from_user.id not in settings.admin_ids:
            await callback.answer(
                copy(language, "អ្នកមិនមានសិទ្ធិប្រើប្រាស់ទេ។", "Not authorized."),
                show_alert=True,
            )
            return
        await callback.answer(copy(language, "បានបោះបង់", "Cancelled"))
        if draft:
            await delete_broadcast_preview(callback.from_user.id, draft)
            await delete_broadcast_source(bot, callback.from_user.id, draft["content"])
        await replace_callback_screen(
            callback,
            copy(language, "បានបោះបង់ Broadcast។", "Broadcast cancelled."),
            keyboard([back_button("admin", language)]),
        )

    @router.callback_query(F.data == "admin:broadcast:schedule")
    async def schedule_broadcast_callback(callback: CallbackQuery) -> None:
        language = await safe_user_language(store, callback.from_user.id)
        draft = broadcast_drafts.get(callback.from_user.id)
        if (
            callback.from_user.id not in settings.admin_ids
            or not draft
            or draft.get("stage") != "preview"
        ):
            await callback.answer(
                copy(
                    language,
                    "មិនអាចកំណត់ពេល Broadcast នេះបានទេ។",
                    "This broadcast cannot be scheduled.",
                ),
                show_alert=True,
            )
            return
        draft["stage"] = "schedule"
        await callback.answer()
        await replace_callback_screen(
            callback,
            copy(
                language,
                "បញ្ចូលថ្ងៃ និងម៉ោងតាមទម្រង់ <code>YYYY-MM-DD HH:MM</code> ម៉ោងភ្នំពេញ (Asia/Phnom_Penh)។",
                "Enter the date and time as <code>YYYY-MM-DD HH:MM</code> in Phnom Penh time (Asia/Phnom_Penh).",
            ),
            broadcast_draft_keyboard("schedule", language),
            parse_mode="HTML",
        )

    @router.callback_query(F.data == "admin:broadcast:send")
    async def send_broadcast_callback(callback: CallbackQuery) -> None:
        language = await safe_user_language(store, callback.from_user.id)
        user_id = callback.from_user.id
        draft = broadcast_drafts.get(user_id)
        if (
            user_id not in settings.admin_ids
            or not draft
            or draft.get("stage") != "preview"
        ):
            await callback.answer(
                copy(
                    language,
                    "មិនអាចចាប់ផ្តើម Broadcast នេះបានទេ។",
                    "This broadcast cannot be started.",
                ),
                show_alert=True,
            )
            return
        if user_id in broadcast_active_keys:
            await callback.answer(
                copy(
                    language,
                    "Broadcast កំពុងដំណើរការរួចហើយ។",
                    "A broadcast is already running.",
                ),
                show_alert=True,
            )
            return
        await callback.answer()
        await delete_broadcast_preview(user_id, draft)
        run_key = f"manual:{user_id}"
        cancel_event = asyncio.Event()
        broadcast_cancel_events[run_key] = cancel_event
        broadcast_active_keys[user_id] = run_key
        draft["stage"] = "sending"
        progress_message = await replace_callback_screen(
            callback,
            copy(
                language,
                "⏳ Broadcast កំពុងចាប់ផ្តើម...",
                "⏳ Broadcast is starting...",
            ),
            keyboard(
                [
                    [
                        InlineKeyboardButton(
                            text=copy(language, "⏹ បញ្ឈប់", "⏹ Stop"),
                            callback_data="admin:broadcast:stop",
                        )
                    ]
                ]
            ),
        )

        async def run_manual_broadcast() -> None:
            try:
                sent, failed, cancelled = await execute_broadcast(
                    bot,
                    store,
                    user_id,
                    draft["content"],
                    draft["buttons"],
                    progress_message,
                    cancel_event,
                )
                await delete_broadcast_source(bot, user_id, draft["content"])
                broadcast_drafts.pop(user_id, None)
                logger.info(
                    "Broadcast completed by admin %s: sent=%s failed=%s cancelled=%s",
                    user_id,
                    sent,
                    failed,
                    cancelled,
                )
            except Exception:
                logger.exception("Broadcast failed for admin %s", user_id)
                try:
                    draft["stage"] = "preview"
                    await preview_broadcast(user_id, language)
                except Exception:
                    logger.exception(
                        "Could not restore failed broadcast draft for admin %s", user_id
                    )
                    try:
                        await progress_message.edit_text(
                            copy(
                                language,
                                "❌ Broadcast មិនអាចបញ្ចប់បានទេ។ សូមពិនិត្យ Log។",
                                "❌ Broadcast could not complete. Check the application logs.",
                            )
                        )
                    except TelegramBadRequest:
                        logger.debug(
                            "Could not show broadcast failure to admin %s", user_id
                        )
            finally:
                broadcast_cancel_events.pop(run_key, None)
                if broadcast_active_keys.get(user_id) == run_key:
                    broadcast_active_keys.pop(user_id, None)
                broadcast_tasks.pop(run_key, None)

        task = asyncio.create_task(run_manual_broadcast())
        broadcast_tasks[run_key] = task

    @router.callback_query(F.data == "admin:broadcast:stop")
    async def stop_broadcast_callback(callback: CallbackQuery) -> None:
        language = await safe_user_language(store, callback.from_user.id)
        key = broadcast_active_keys.get(callback.from_user.id)
        event = broadcast_cancel_events.get(key or "")
        if callback.from_user.id not in settings.admin_ids or event is None:
            await callback.answer(
                copy(
                    language,
                    "រកមិនឃើញ Broadcast កំពុងដំណើរការទេ។",
                    "No running broadcast found.",
                ),
                show_alert=True,
            )
            return
        event.set()
        await callback.answer(
            copy(language, "កំពុងបញ្ឈប់ Broadcast...", "Stopping broadcast...")
        )

    @router.callback_query(F.data == "admin:broadcast:scheduled")
    async def show_scheduled_broadcasts(callback: CallbackQuery) -> None:
        language = await safe_user_language(store, callback.from_user.id)
        if callback.from_user.id not in settings.admin_ids:
            await callback.answer(
                copy(language, "អ្នកមិនមានសិទ្ធិប្រើប្រាស់ទេ។", "Not authorized."),
                show_alert=True,
            )
            return
        try:
            jobs = await store.scheduled_broadcasts(callback.from_user.id)
        except Exception:
            logger.exception(
                "Could not load scheduled broadcasts for admin %s",
                callback.from_user.id,
            )
            await callback.answer(
                copy(
                    language,
                    "មិនអាចទាញយក Schedule បានទេ។",
                    "Could not load scheduled broadcasts.",
                ),
                show_alert=True,
            )
            return
        rows = []
        lines = []
        for job in jobs:
            local_time = datetime.fromisoformat(
                str(job["scheduled_at"]).replace("Z", "+00:00")
            ).astimezone(PHNOM_PENH_TIMEZONE)
            lines.append(f"• {local_time.strftime('%Y-%m-%d %H:%M')} (Phnom Penh)")
            rows.append(
                [
                    InlineKeyboardButton(
                        text=f"❌ Cancel {local_time.strftime('%m-%d %H:%M')}",
                        callback_data=f"bcs:{job['id']}",
                    )
                ]
            )
        rows.append(back_button("admin", language))
        text = copy(
            language,
            "🕒 <b>Broadcast ដែលបានកំណត់ពេល</b>\n",
            "🕒 <b>Scheduled broadcasts</b>\n",
        )
        text += (
            "\n".join(lines)
            if lines
            else copy(language, "មិនមាន Schedule ទេ។", "No scheduled broadcasts.")
        )
        await callback.answer()
        await replace_callback_screen(callback, text, keyboard(rows), parse_mode="HTML")

    @router.callback_query(F.data.startswith("bcs:"))
    async def cancel_scheduled_broadcast_callback(callback: CallbackQuery) -> None:
        language = await safe_user_language(store, callback.from_user.id)
        if callback.from_user.id not in settings.admin_ids:
            await callback.answer(
                copy(language, "អ្នកមិនមានសិទ្ធិប្រើប្រាស់ទេ។", "Not authorized."),
                show_alert=True,
            )
            return
        job_id = callback.data.removeprefix("bcs:")
        try:
            job = await store.cancel_scheduled_broadcast(job_id, callback.from_user.id)
        except Exception:
            logger.exception("Could not cancel scheduled broadcast %s", job_id)
            await callback.answer(
                copy(
                    language,
                    "មិនអាចលុប Schedule បានទេ។",
                    "Could not cancel the scheduled broadcast.",
                ),
                show_alert=True,
            )
            return
        if not job:
            await callback.answer(
                copy(
                    language,
                    "Schedule នេះមិនមាន ឬបានចាប់ផ្តើមរួចហើយ។",
                    "Schedule not found or already started.",
                ),
                show_alert=True,
            )
            return
        await delete_broadcast_source(
            bot, callback.from_user.id, job.get("content") or []
        )
        await callback.answer(
            copy(language, "បានលុប Schedule រួចរាល់។", "Scheduled broadcast cancelled.")
        )
        await replace_callback_screen(
            callback,
            copy(
                language,
                "✅ បានលុប Schedule រួចរាល់។",
                "✅ Scheduled broadcast cancelled.",
            ),
            keyboard(
                [
                    [
                        InlineKeyboardButton(
                            text=copy(
                                language,
                                "🔄 ធ្វើបច្ចុប្បន្នភាពបញ្ជី",
                                "🔄 Refresh list",
                            ),
                            callback_data="admin:broadcast:scheduled",
                        )
                    ],
                    back_button("admin", language),
                ]
            ),
        )

    @router.callback_query(F.data == "admin:settings")
    async def admin_settings(callback: CallbackQuery) -> None:
        language = await safe_user_language(store, callback.from_user.id)
        if callback.from_user.id not in settings.admin_ids:
            await callback.answer(
                copy(language, "អ្នកមិនមានសិទ្ធិប្រើប្រាស់ទេ។", "Not authorized."),
                show_alert=True,
            )
            return
        pending_admin_media.pop(callback.from_user.id, None)
        pending = pending_admin_input.get(callback.from_user.id)
        if pending and pending[0] == "bot_setting":
            pending_admin_input.pop(callback.from_user.id, None)
        await callback.answer()
        await show_admin_settings(callback.message, language)

    @router.callback_query(F.data.startswith("admin:settings:section:"))
    async def admin_settings_section(callback: CallbackQuery) -> None:
        language = await safe_user_language(store, callback.from_user.id)
        if callback.from_user.id not in settings.admin_ids:
            await callback.answer(
                copy(language, "អ្នកមិនមានសិទ្ធិប្រើប្រាស់ទេ។", "Not authorized."),
                show_alert=True,
            )
            return
        section = callback.data.removeprefix("admin:settings:section:")
        if section not in {"media", *BOT_SETTING_SECTIONS}:
            await callback.answer(
                copy(
                    language,
                    "រកមិនឃើញផ្នែក Settings ទេ។",
                    "Settings section not found.",
                ),
                show_alert=True,
            )
            return
        await callback.answer()
        await show_admin_settings(callback.message, language, section)

    @router.callback_query(F.data.startswith("admin:setting:set:"))
    async def set_bot_setting_prompt(callback: CallbackQuery) -> None:
        language = await safe_user_language(store, callback.from_user.id)
        if callback.from_user.id not in settings.admin_ids:
            await callback.answer(
                copy(language, "អ្នកមិនមានសិទ្ធិប្រើប្រាស់ទេ។", "Not authorized."),
                show_alert=True,
            )
            return
        key = callback.data.removeprefix("admin:setting:set:")
        if key not in BOT_SETTING_LABELS:
            await callback.answer(
                copy(language, "Setting មិនត្រឹមត្រូវទេ។", "Invalid setting."),
                show_alert=True,
            )
            return
        pending_admin_input[callback.from_user.id] = ("bot_setting", key)
        await callback.answer()
        await replace_callback_screen(
            callback,
            copy(
                language,
                "ផ្ញើតម្លៃថ្មី។ ផ្ញើសញ្ញា - ដើម្បីលុបតម្លៃ។",
                "Send the new value. Send - to clear it.",
            ),
            keyboard(
                [
                    back_button(
                        f"admin:settings:section:{bot_setting_section(key)}", language
                    )
                ]
            ),
        )

    @router.callback_query(F.data.startswith("admin:setting:clear:"))
    async def clear_bot_setting(callback: CallbackQuery) -> None:
        global welcome_media_cache
        language = await safe_user_language(store, callback.from_user.id)
        if callback.from_user.id not in settings.admin_ids:
            await callback.answer(
                copy(language, "អ្នកមិនមានសិទ្ធិប្រើប្រាស់ទេ។", "Not authorized."),
                show_alert=True,
            )
            return
        key = callback.data.removeprefix("admin:setting:clear:")
        if key not in BOT_SETTING_LABELS and key not in {
            "welcome_photo_url",
            "welcome_video_url",
            "how_to_buy_video_url",
            "welcome_media",
        }:
            await callback.answer(
                copy(language, "Setting មិនត្រឹមត្រូវទេ។", "Invalid setting."),
                show_alert=True,
            )
            return
        try:
            if key == "welcome_media":
                await store.set_bot_setting("welcome_photo_url", "")
                await store.set_bot_setting("welcome_video_url", "")
            else:
                await store.set_bot_setting(key, "")
        except Exception:
            logger.exception("Could not clear bot setting %s", key)
            await callback.answer(
                copy(language, "មិនអាចរក្សាទុកបានទេ។", "Could not save setting."),
                show_alert=True,
            )
            return
        if key in {"welcome_media", "welcome_photo_url", "welcome_video_url"}:
            welcome_media_cache = None
        await callback.answer(copy(language, "បានលុបតម្លៃរួចរាល់។", "Setting cleared."))
        await show_admin_settings(
            callback.message,
            language,
            "media" if key == "welcome_media" else bot_setting_section(key),
        )

    @router.callback_query(F.data.startswith("admin:media:"))
    async def request_admin_media(callback: CallbackQuery) -> None:
        language = await safe_user_language(store, callback.from_user.id)
        if callback.from_user.id not in settings.admin_ids:
            await callback.answer(
                copy(language, "អ្នកមិនមានសិទ្ធិប្រើប្រាស់ទេ។", "Not authorized."),
                show_alert=True,
            )
            return
        kind = callback.data.removeprefix("admin:media:")
        if kind not in {"welcome", "tutorial"}:
            await callback.answer(
                copy(language, "ប្រភេទ media មិនត្រឹមត្រូវទេ។", "Invalid media type."),
                show_alert=True,
            )
            return
        pending_admin_media[callback.from_user.id] = {
            "kind": kind,
            "items": [],
        }
        await callback.answer()
        await replace_callback_screen(
            callback,
            copy(
                language,
                "ផ្ញើរូបភាព, វីដេអូ, GIF ឬឯកសារ។ Bot នឹង upload ឯកសារទៅ Cloudinary ហើយរក្សាទុក URL ក្នុង Supabase។ អាចផ្ញើ Telegram post link ជំនួសបាន។ ចុច Save media ពេលរួច។",
                "Send an image, video, GIF, or file. The bot uploads files to Cloudinary and saves the URL in Supabase. You may send a Telegram post link instead. Choose Save media when done.",
            ),
            keyboard([back_button("admin:settings:section:media", language)]),
        )

    @router.callback_query(F.data == "admin:media-save")
    async def save_admin_telegram_media(callback: CallbackQuery) -> None:
        global welcome_media_cache
        language = await safe_user_language(store, callback.from_user.id)
        if callback.from_user.id not in settings.admin_ids:
            await callback.answer()
            return
        pending = pending_admin_media.get(callback.from_user.id)
        if not pending or not pending["items"]:
            await callback.answer(
                copy(
                    language,
                    "សូមបន្ថែម Message ឬ Album មុនរក្សាទុក។",
                    "Add a message or album before saving.",
                ),
                show_alert=True,
            )
            return
        try:
            media_urls = await upload_telegram_media_items(
                bot, settings, pending["items"], "bot-media"
            )
            media_type = pending["items"][0].get("media_type", "document")
            if pending["kind"] == "tutorial":
                await store.set_bot_setting("how_to_buy_video_url", media_urls)
            else:
                if media_type == "video":
                    await store.set_bot_setting("welcome_video_url", media_urls)
                    await store.set_bot_setting("welcome_photo_url", "")
                else:
                    await store.set_bot_setting("welcome_photo_url", media_urls)
                    await store.set_bot_setting("welcome_video_url", "")
                welcome_media_cache = None
        except ValueError as exc:
            await callback.answer(str(exc), show_alert=True)
            return
        except MEDIA_SAVE_ERRORS:
            logger.exception(
                "Could not upload/save %s media for admin %s",
                pending["kind"],
                callback.from_user.id,
            )
            await callback.answer(
                copy(
                    language,
                    "រក្សាទុក Media មិនបានទេ។ សូមព្យាយាមម្ដងទៀត។",
                    "Could not save media. Please try again.",
                ),
                show_alert=True,
            )
            return
        pending_admin_media.pop(callback.from_user.id, None)
        await callback.answer(copy(language, "បានរក្សាទុក Media។", "Media saved."))
        await show_admin_settings(callback.message, language, "media")

    @router.callback_query(F.data == "admin:media-cancel")
    async def cancel_admin_media(callback: CallbackQuery) -> None:
        pending_admin_media.pop(callback.from_user.id, None)
        language = await safe_user_language(store, callback.from_user.id)
        await callback.answer(copy(language, "បានបោះបង់", "Cancelled"))
        await show_admin_settings(callback.message, language, "media")

    @router.callback_query(F.data == "admin:resellers")
    async def admin_resellers(callback: CallbackQuery) -> None:
        await callback.answer()
        language = await safe_user_language(store, callback.from_user.id)
        if callback.from_user.id not in settings.admin_ids:
            return
        await show_resellers(callback.message, language)

    @router.callback_query(F.data == "admin:reseller-add")
    async def add_reseller_prompt(callback: CallbackQuery) -> None:
        await callback.answer()
        language = await safe_user_language(store, callback.from_user.id)
        if callback.from_user.id not in settings.admin_ids:
            return
        pending_admin_input[callback.from_user.id] = ("reseller_add", None)
        await replace_callback_screen(
            callback,
            copy(
                language,
                "ផ្ញើ Telegram ID ដែលចង់ផ្តល់តម្លៃ Reseller៖",
                "Send the Telegram ID to grant reseller pricing:",
            ),
            keyboard([back_button("admin:cancel-input", language)]),
        )

    @router.callback_query(F.data.startswith("admin:reseller-remove:"))
    async def remove_reseller(callback: CallbackQuery) -> None:
        language = await safe_user_language(store, callback.from_user.id)
        if callback.from_user.id not in settings.admin_ids:
            await callback.answer()
            return
        try:
            telegram_id = int(callback.data.removeprefix("admin:reseller-remove:"))
        except ValueError:
            await callback.answer(
                copy(language, "Telegram ID មិនត្រឹមត្រូវទេ។", "Invalid Telegram ID."),
                show_alert=True,
            )
            return
        removed = await store.remove_reseller(telegram_id)
        await callback.answer(
            copy(language, "បានដក Reseller រួចរាល់។", "Reseller removed.")
            if removed
            else copy(language, "រកមិនឃើញ Reseller ទេ។", "Reseller not found.")
        )
        await show_resellers(callback.message, language)

    @router.callback_query(F.data == "admin:catalogs")
    async def admin_catalogs(callback: CallbackQuery) -> None:
        await callback.answer()
        language = await safe_user_language(store, callback.from_user.id)
        if callback.from_user.id not in settings.admin_ids:
            return
        result = (
            await store.client.table("categories")
            .select("id,name")
            .eq("active", True)
            .order("sort_order")
            .execute()
        )
        categories = result.data or []
        rows = [
            [
                InlineKeyboardButton(
                    text=f"🛍 {item['name']}", callback_data=f"ac:{item['id']}"
                )
            ]
            for item in categories
        ]
        rows.extend(
            [
                [
                    InlineKeyboardButton(
                        text=copy(language, "➕ បន្ថែម Catalog", "➕ Add catalog"),
                        callback_data="admin:category-add",
                    )
                ],
                back_button("admin", language),
            ]
        )
        await callback.message.edit_text(
            f"🗂 <b>{copy(language, 'គ្រប់គ្រង Catalog', 'Manage catalogs')} ({len(categories)})</b>\n"
            + (
                copy(
                    language,
                    "ជ្រើសរើស Catalog ដើម្បីមើល Package និងសកម្មភាព៖",
                    "Choose a catalog to view its packages and actions:",
                )
                if categories
                else copy(
                    language,
                    "មិនទាន់មាន Catalog ទេ។ ចុចបន្ថែម Catalog ដើម្បីចាប់ផ្តើម។",
                    "No catalogs yet. Add a catalog to get started.",
                )
            ),
            parse_mode="HTML",
            reply_markup=keyboard(rows),
        )

    @router.callback_query(F.data == "admin:category-add")
    async def add_category_prompt(callback: CallbackQuery) -> None:
        await callback.answer()
        language = await safe_user_language(store, callback.from_user.id)
        if callback.from_user.id not in settings.admin_ids:
            return
        pending_admin_input.pop(callback.from_user.id, None)
        admin_wizards[callback.from_user.id] = {
            "kind": "category",
            "action": "category_add",
            "target_id": None,
            "edit": False,
            "step": "name",
            "values": {"description": ""},
        }
        await show_admin_wizard(callback.message, callback.from_user.id, language)

    @router.callback_query(F.data.startswith("ac:"))
    async def admin_category(callback: CallbackQuery) -> None:
        await callback.answer()
        language = await safe_user_language(store, callback.from_user.id)
        if callback.from_user.id not in settings.admin_ids:
            return
        category_id = callback.data[3:]
        category_data = await store.category(category_id)
        if not category_data:
            await callback.message.edit_text(
                copy(language, "រកមិនឃើញ Catalog ទេ។", "Catalog not found."),
                reply_markup=keyboard([back_button("admin:catalogs", language)]),
            )
            return
        result = (
            await store.client.table("products")
            .select("id,name")
            .eq("category_id", category_id)
            .eq("active", True)
            .order("sort_order")
            .execute()
        )
        products = result.data or []
        stock_counts = await asyncio.gather(
            *(store.available_stock(item["id"]) for item in products)
        )
        rows = [
            [
                InlineKeyboardButton(
                    text=f"📦 {item['name'][:40]} · {stock} {copy(language, 'នៅសល់', 'available')}",
                    callback_data=f"ap:{item['id']}",
                    style=stock_button_style(stock),
                )
            ]
            for item, stock in zip(products, stock_counts)
        ]
        rows.extend(admin_category_actions(category_id, language))
        product_summary = (
            f"📦 {copy(language, 'Package សកម្ម៖', 'Active packages:')} {len(products)}"
            if products
            else copy(
                language,
                "មិនទាន់មាន Package ទេ។ ចុចបន្ថែម Package ដើម្បីចាប់ផ្តើម។",
                "No packages yet. Add a package to get started.",
            )
        )
        await callback.message.edit_text(
            f"🗂 <b>{html.escape(category_data['name'])}</b>\n"
            f"{html.escape(category_data.get('description') or '')}\n\n"
            f"{copy(language, 'សារក្រោយ Order៖', 'After-order message:')} "
            f"{'✅' if category_data.get('delivery_message') else '—'} · "
            f"{copy(language, 'Media ក្រោយ Order៖', 'After-order media:')} "
            f"{'✅' if category_data.get('delivery_media_url') else '—'}\n\n"
            f"{product_summary}",
            parse_mode="HTML",
            reply_markup=keyboard(rows),
        )

    @router.callback_query(F.data.startswith("ap:"))
    async def admin_product(callback: CallbackQuery) -> None:
        await callback.answer()
        language = await safe_user_language(store, callback.from_user.id)
        if callback.from_user.id not in settings.admin_ids:
            return
        product_data = await store.product(callback.data[3:])
        if not product_data:
            await callback.message.edit_text(
                copy(language, "រកមិនឃើញ Package ទេ។", "Package not found."),
                reply_markup=keyboard([back_button("admin:catalogs", language)]),
            )
            return
        stock = await store.available_stock(product_data["id"])
        rows = admin_product_actions(
            product_data["id"], product_data["category_id"], language
        )
        await callback.message.edit_text(
            f"📦 <b>{html.escape(product_data['name'])}</b>\n"
            f"ID: <code>{html.escape(product_data['id'])}</code>\n"
            f"{copy(language, 'តម្លៃអតិថិជន៖', 'Customer price:')} {money(product_data['price'])}\n"
            f"{copy(language, 'តម្លៃ Reseller៖', 'Reseller price:')} {money(product_data['reseller_price'])}\n"
            f"{copy(language, 'ស្តុកនៅសល់៖', 'Stock remaining:')} {stock}\n\n"
            f"{html.escape(product_data.get('description') or '')}\n\n"
            f"{copy(language, 'Catalog កំណត់សារ និង Media ក្រោយ Order សម្រាប់ Package ទាំងអស់។', 'After-order message and media are configured once for the whole catalog.')}",
            parse_mode="HTML",
            reply_markup=keyboard(rows),
        )

    @router.message(
        F.photo | F.video | F.animation | F.document,
        F.caption.startswith(("setphoto", "setvideo", "setmedia")),
    )
    async def set_product_media(message: Message) -> None:
        if not message.from_user or message.from_user.id not in settings.admin_ids:
            return
        language = await safe_user_language(store, message.from_user.id)
        parts = (message.caption or "").strip().split(maxsplit=1)
        if (
            not parts
            or parts[0] not in {"setphoto", "setvideo", "setmedia"}
            or len(parts) != 2
            or not parts[1].strip()
        ):
            await send_replacing_screen(
                message,
                message.from_user.id,
                copy(
                    language,
                    "ទម្រង់មិនត្រឹមត្រូវ។ សូមប្រើ setmedia <product_id> ជាមួយរូបភាព ឬវីដេអូ។",
                    "Invalid format. Use setmedia <product_id> with an image or video.",
                ),
            )
            return
        product_id = parts[1].strip()
        media_item = telegram_media_upload_item(message)
        if not media_item:
            await send_replacing_screen(
                message,
                message.from_user.id,
                copy(
                    language,
                    "សូមផ្ញើឯកសារ Media ដែលគាំទ្រ។",
                    "Send a supported media file.",
                ),
            )
            return
        is_video = media_item["media_type"] == "video"
        try:
            media_url = await upload_cloudinary_file(
                bot,
                settings,
                media_item["file_id"],
                "products",
                resource_type="auto",
                file_name=media_item["file_name"],
            )
            update_media = (
                store.update_product_video if is_video else store.update_product_image
            )
            updated = await update_media(product_id, media_url)
        except MEDIA_SAVE_ERRORS:
            logger.exception("Could not update product media %s", product_id)
            await send_replacing_screen(
                message,
                message.from_user.id,
                copy(
                    language,
                    "មិនអាចធ្វើបច្ចុប្បន្នភាពរូបភាព/វីដេអូ Package បានទេ។",
                    "Could not update the package image/video.",
                ),
            )
            return
        if not updated:
            await send_replacing_screen(
                message,
                message.from_user.id,
                copy(
                    language,
                    f"រកមិនឃើញ Package ID {html.escape(product_id)} ទេ។",
                    f"Package ID {html.escape(product_id)} not found.",
                ),
            )
            return
        await send_replacing_screen(
            message,
            message.from_user.id,
            copy(
                language,
                f"✅ បានបច្ចុប្បន្នភាព {'វីដេអូ' if is_video else 'រូបភាព'} ជោគជ័យ។",
                f"✅ {'Video' if is_video else 'Image'} updated successfully.",
            ),
            parse_mode="HTML",
        )

    @router.message(F.photo | F.video)
    async def receive_wizard_media(message: Message) -> None:
        if not message.from_user or message.from_user.id not in settings.admin_ids:
            return
        language = await safe_user_language(store, message.from_user.id)
        if await handle_broadcast_input(message, language):
            return
        if await capture_pending_admin_media(message, language):
            return
        await capture_wizard_telegram_media(message, language)

    @router.callback_query(F.data.startswith("admin:product-add:"))
    async def add_product_prompt(callback: CallbackQuery) -> None:
        await callback.answer()
        language = await safe_user_language(store, callback.from_user.id)
        if callback.from_user.id not in settings.admin_ids:
            return
        category_id = callback.data.removeprefix("admin:product-add:")
        pending_admin_input.pop(callback.from_user.id, None)
        admin_wizards[callback.from_user.id] = {
            "kind": "product",
            "action": "product_add",
            "target_id": None,
            "category_id": category_id,
            "edit": False,
            "step": "media",
            "values": {"description": ""},
        }
        await show_admin_wizard(callback.message, callback.from_user.id, language)

    @router.callback_query(F.data.startswith("admin:category-edit:"))
    async def edit_category_prompt(callback: CallbackQuery) -> None:
        await callback.answer()
        language = await safe_user_language(store, callback.from_user.id)
        if callback.from_user.id not in settings.admin_ids:
            return
        category_id = callback.data.removeprefix("admin:category-edit:")
        category = await store.category(category_id)
        if not category:
            await callback.message.edit_text(
                copy(language, "រកមិនឃើញ Catalog ទេ។", "Catalog not found."),
                reply_markup=keyboard([back_button("admin:catalogs", language)]),
            )
            return
        pending_admin_input.pop(callback.from_user.id, None)
        admin_wizards[callback.from_user.id] = {
            "kind": "category",
            "action": "category_edit",
            "target_id": category_id,
            "edit": True,
            "step": "name",
            "values": {
                "name": category["name"],
                "description": category.get("description") or "",
                "delivery_message": category.get("delivery_message") or "",
                "delivery_media_url": category.get("delivery_media_url"),
            },
        }
        await show_admin_wizard(callback.message, callback.from_user.id, language)

    @router.callback_query(F.data.startswith("admin:product-edit:"))
    async def edit_product_prompt(callback: CallbackQuery) -> None:
        await callback.answer()
        language = await safe_user_language(store, callback.from_user.id)
        if callback.from_user.id not in settings.admin_ids:
            return
        product_id = callback.data.removeprefix("admin:product-edit:")
        product = await store.product(product_id)
        if not product:
            await callback.message.edit_text(
                copy(language, "រកមិនឃើញ Package ទេ។", "Package not found."),
                reply_markup=keyboard([back_button("admin:catalogs", language)]),
            )
            return
        pending_admin_input.pop(callback.from_user.id, None)
        admin_wizards[callback.from_user.id] = {
            "kind": "product",
            "action": "product_edit",
            "target_id": product_id,
            "category_id": product["category_id"],
            "edit": True,
            "step": "media",
            "values": {
                "name": product["name"],
                "description": product.get("description") or "",
                "price": str(product["price"]),
                "reseller_price": str(
                    product.get("reseller_price") or product["price"]
                ),
                "image_url": product.get("image_url"),
                "video_url": product.get("video_url"),
            },
        }
        await show_admin_wizard(callback.message, callback.from_user.id, language)

    @router.callback_query(F.data.startswith("admin:category-disable:"))
    async def confirm_category_disable(callback: CallbackQuery) -> None:
        await callback.answer()
        language = await safe_user_language(store, callback.from_user.id)
        if callback.from_user.id not in settings.admin_ids:
            return
        category_id = callback.data.removeprefix("admin:category-disable:")
        rows = [
            [
                InlineKeyboardButton(
                    text=copy(language, "✅ បិទ Catalog", "✅ Disable catalog"),
                    callback_data=f"adc:{category_id}",
                )
            ],
            back_button(f"ac:{category_id}", language),
        ]
        await callback.message.edit_text(
            copy(
                language,
                "បិទ Catalog នេះ និងផលិតផលសកម្មរបស់វា?",
                "Disable this catalog and all its active packages?",
            ),
            reply_markup=keyboard(rows),
        )

    @router.callback_query(F.data.startswith("adc:"))
    async def disable_category(callback: CallbackQuery) -> None:
        await callback.answer()
        language = await safe_user_language(store, callback.from_user.id)
        if callback.from_user.id not in settings.admin_ids:
            return
        category_id = callback.data[4:]
        await store.client.table("categories").update({"active": False}).eq(
            "id", category_id
        ).execute()
        await store.client.table("products").update({"active": False}).eq(
            "category_id", category_id
        ).execute()
        await callback.message.edit_text(
            copy(
                language,
                "បានបិទ Catalog និងផលិតផលរបស់វា។",
                "Catalog and its packages disabled.",
            ),
            reply_markup=keyboard([back_button("admin:catalogs", language)]),
        )

    @router.callback_query(F.data.startswith("admin:product-disable:"))
    async def confirm_product_disable(callback: CallbackQuery) -> None:
        await callback.answer()
        language = await safe_user_language(store, callback.from_user.id)
        if callback.from_user.id not in settings.admin_ids:
            return
        product_id = callback.data.removeprefix("admin:product-disable:")
        rows = [
            [
                InlineKeyboardButton(
                    text=copy(language, "✅ បិទ Package", "✅ Disable package"),
                    callback_data=f"adp:{product_id}",
                )
            ],
            back_button(f"ap:{product_id}", language),
        ]
        await callback.message.edit_text(
            copy(language, "បិទ Package នេះ?", "Disable this package?"),
            reply_markup=keyboard(rows),
        )

    @router.callback_query(F.data.startswith("adp:"))
    async def disable_product(callback: CallbackQuery) -> None:
        await callback.answer()
        language = await safe_user_language(store, callback.from_user.id)
        if callback.from_user.id not in settings.admin_ids:
            return
        product_id = callback.data[4:]
        await store.client.table("products").update({"active": False}).eq(
            "id", product_id
        ).execute()
        await callback.message.edit_text(
            copy(language, "បានបិទ Package។", "Package disabled."),
            reply_markup=keyboard([back_button("admin:catalogs", language)]),
        )

    @router.callback_query(F.data == "admin:wizard-media-save")
    async def save_wizard_telegram_media(callback: CallbackQuery) -> None:
        language = await safe_user_language(store, callback.from_user.id)
        if callback.from_user.id not in settings.admin_ids:
            await callback.answer()
            return
        wizard = admin_wizards.get(callback.from_user.id)
        step = wizard.get("step") if wizard else None
        items = wizard.get("telegram_media_items", []) if wizard else []
        if (
            not wizard
            or step not in {"media", "delivery_media"}
            or not items
        ):
            await callback.answer(
                copy(
                    language,
                    "សូមបន្ថែម Message ឬ Album មុនរក្សាទុក។",
                    "Add a message or album before saving.",
                ),
                show_alert=True,
            )
            if wizard:
                await show_admin_wizard(
                    callback.message, callback.from_user.id, language
                )
            return
        try:
            folder = "categories" if step == "delivery_media" else "products"
            media_urls = await upload_telegram_media_items(
                bot, settings, items, folder
            )
        except MEDIA_UPLOAD_ERRORS:
            logger.exception(
                "Could not upload wizard media for admin %s",
                callback.from_user.id,
            )
            await callback.answer(
                copy(
                    language,
                    "មិនអាច upload Media ទៅ Cloudinary បានទេ។ សូមពិនិត្យ Cloudinary settings ហើយព្យាយាមម្ដងទៀត។",
                    "Could not upload media to Cloudinary. Check Cloudinary settings and try again.",
                ),
                show_alert=True,
            )
            return
        values = wizard["values"]
        media_type = items[0].get("media_type", "document")
        if media_type == "link":
            media_type = "telegram"
        if step == "media":
            values["media_url"] = media_urls
            values["media_type"] = media_type
        else:
            values[f"{step}_url"] = media_urls
        wizard.pop("telegram_media_items", None)
        steps = ADMIN_WIZARD_STEPS[wizard["kind"]]
        step_index = steps.index(step)
        wizard["step"] = steps[step_index + 1] if step_index + 1 < len(steps) else None
        await callback.answer(copy(language, "បានរក្សាទុក Media។", "Media saved."))
        await show_admin_wizard(callback.message, callback.from_user.id, language)

    @router.callback_query(F.data.startswith("admin:wizard-skip:"))
    async def skip_admin_wizard_step(callback: CallbackQuery) -> None:
        language = await safe_user_language(store, callback.from_user.id)
        if callback.from_user.id not in settings.admin_ids:
            await callback.answer()
            return
        wizard = admin_wizards.get(callback.from_user.id)
        if not wizard:
            await callback.answer(
                copy(language, "Wizard មិនសកម្មទេ។", "No active wizard."),
                show_alert=True,
            )
            return
        step = wizard.get("step")
        requested_step = callback.data.removeprefix("admin:wizard-skip:")
        if step != requested_step:
            await callback.answer(
                copy(
                    language,
                    "ទម្រង់បានធ្វើបច្ចុប្បន្នភាពហើយ។ សូមបន្តពីជំហានបច្ចុប្បន្ន។",
                    "The form was updated. Continue from the current step.",
                )
            )
            await show_admin_wizard(callback.message, callback.from_user.id, language)
            return
        optional_steps = {
            "media",
            "description",
            "reseller_price",
            "delivery_message",
            "delivery_media",
        }
        if not step or (step not in optional_steps and not wizard["edit"]):
            await callback.answer(
                copy(
                    language, "ជំហាននេះមិនអាចរំលងបានទេ។", "This step cannot be skipped."
                ),
                show_alert=True,
            )
            return
        if step == "reseller_price" and not wizard["edit"]:
            wizard["values"]["reseller_price"] = wizard["values"].get("price")
        steps = ADMIN_WIZARD_STEPS[wizard["kind"]]
        current_index = steps.index(step)
        wizard["step"] = (
            steps[current_index + 1] if current_index + 1 < len(steps) else None
        )
        await callback.answer()
        await show_admin_wizard(callback.message, callback.from_user.id, language)

    @router.callback_query(F.data.startswith("admin:wizard-clear:"))
    async def clear_admin_wizard_media(callback: CallbackQuery) -> None:
        language = await safe_user_language(store, callback.from_user.id)
        if callback.from_user.id not in settings.admin_ids:
            await callback.answer()
            return
        wizard = admin_wizards.get(callback.from_user.id)
        step = callback.data.removeprefix("admin:wizard-clear:")
        fields = {"delivery_media": "delivery_media_url"}
        if (
            not wizard
            or wizard["kind"] != "category"
            or not wizard["edit"]
            or step not in fields
        ):
            await callback.answer(
                copy(language, "មិនអាចលុប Media នេះបានទេ។", "This media cannot be cleared."),
                show_alert=True,
            )
            return
        if wizard.get("step") != step:
            await callback.answer(
                copy(
                    language,
                    "ទម្រង់បានធ្វើបច្ចុប្បន្នភាពហើយ។ សូមបន្តពីជំហានបច្ចុប្បន្ន។",
                    "The form was updated. Continue from the current step.",
                )
            )
            await show_admin_wizard(callback.message, callback.from_user.id, language)
            return
        url_field = fields[step]
        wizard["values"][url_field] = None
        steps = ADMIN_WIZARD_STEPS[wizard["kind"]]
        current_index = steps.index(step)
        wizard["step"] = (
            steps[current_index + 1] if current_index + 1 < len(steps) else None
        )
        await callback.answer(copy(language, "បានលុប Media នេះ។", "Media cleared."))
        await show_admin_wizard(callback.message, callback.from_user.id, language)

    @router.callback_query(F.data == "admin:wizard-cancel")
    async def cancel_admin_wizard(callback: CallbackQuery) -> None:
        language = await safe_user_language(store, callback.from_user.id)
        if callback.from_user.id not in settings.admin_ids:
            await callback.answer()
            return
        wizard = admin_wizards.pop(callback.from_user.id, None)
        await callback.answer(copy(language, "បានបោះបង់", "Cancelled"))
        target = "admin:catalogs"
        if wizard and wizard["kind"] == "product":
            target = (
                f"ap:{wizard['target_id']}"
                if wizard["action"] == "product_edit"
                else f"ac:{wizard['category_id']}"
            )
        elif wizard and wizard["action"] == "category_edit":
            target = f"ac:{wizard['target_id']}"
        await callback.message.edit_text(
            copy(language, "បានបោះបង់ការកែប្រែ។", "Changes cancelled."),
            reply_markup=keyboard([back_button(target, language)]),
        )

    @router.callback_query(F.data == "admin:wizard-save")
    async def save_admin_wizard(callback: CallbackQuery) -> None:
        language = await safe_user_language(store, callback.from_user.id)
        if callback.from_user.id not in settings.admin_ids:
            await callback.answer()
            return
        wizard = admin_wizards.get(callback.from_user.id)
        if not wizard or wizard.get("step") is not None:
            await callback.answer(
                copy(
                    language,
                    "Wizard មិនទាន់រួចរាល់សម្រាប់រក្សាទុកទេ។",
                    "The wizard is not ready to save.",
                ),
                show_alert=True,
            )
            return
        try:
            target = await finish_admin_wizard(callback.from_user.id, language)
        except InputValidationError as exc:
            await callback.answer(str(exc), show_alert=True)
            return
        except Exception:
            logger.exception(
                "Admin wizard save failed for user %s", callback.from_user.id
            )
            await callback.answer(
                copy(
                    language,
                    "រក្សាទុកមិនបានទេ។ សូមព្យាយាមម្ដងទៀត។",
                    "Could not save. Please try again.",
                ),
                show_alert=True,
            )
            return
        admin_wizards.pop(callback.from_user.id, None)
        await callback.answer(
            copy(language, "បានរក្សាទុកដោយជោគជ័យ។", "Saved successfully.")
        )
        await callback.message.edit_text(
            copy(
                language,
                "✅ បានរក្សាទុក Package/Catalog ដោយជោគជ័យ។",
                "✅ Package/catalog saved successfully.",
            ),
            reply_markup=keyboard([back_button(target, language)]),
        )

    @router.callback_query(F.data == "admin:cancel-input")
    async def cancel_admin_input(callback: CallbackQuery) -> None:
        language = await safe_user_language(store, callback.from_user.id)
        pending = pending_admin_input.pop(callback.from_user.id, None)
        media_pending = pending_admin_media.pop(callback.from_user.id, None)
        await callback.answer(copy(language, "បានបោះបង់", "Cancelled"))
        target = (
            f"ap:{pending[1]}"
            if pending and pending[0] in {"stock_add", "stock_remove"}
            else (
                "admin:resellers"
                if pending and pending[0] == "reseller_add"
                else (
                    f"admin:settings:section:{bot_setting_section(pending[1])}"
                    if pending and pending[0] == "bot_setting" and pending[1]
                    else (
                        "admin:settings:section:media"
                        if media_pending
                        else "admin:catalogs"
                    )
                )
            )
        )
        await replace_callback_screen(
            callback,
            copy(language, "បានបោះបង់។", "Cancelled."),
            keyboard([back_button(target, language)]),
        )

    @router.callback_query(F.data.startswith("admin:stock-add:"))
    async def add_stock_prompt(callback: CallbackQuery) -> None:
        await callback.answer()
        language = await safe_user_language(store, callback.from_user.id)
        if callback.from_user.id not in settings.admin_ids:
            return
        product_id = callback.data.removeprefix("admin:stock-add:")
        pending_admin_input[callback.from_user.id] = ("stock_add", product_id)
        await replace_callback_screen(
            callback,
            copy(
                language,
                "ផ្ញើ Digital Key ឬ Code មួយក្នុងមួយបន្ទាត់៖",
                "Send one digital key or code per line:",
            ),
            keyboard([back_button("admin:cancel-input", language)]),
        )

    @router.callback_query(F.data.startswith("admin:stock-remove:"))
    async def remove_stock_prompt(callback: CallbackQuery) -> None:
        await callback.answer()
        language = await safe_user_language(store, callback.from_user.id)
        if callback.from_user.id not in settings.admin_ids:
            return
        product_id = callback.data.removeprefix("admin:stock-remove:")
        pending_admin_input[callback.from_user.id] = ("stock_remove", product_id)
        await replace_callback_screen(
            callback,
            copy(
                language,
                "ផ្ញើ Digital Key ឬ Code ដែលចង់ដក មួយក្នុងមួយបន្ទាត់។ ដកបានតែធាតុដែលមិនទាន់កក់ ឬលក់៖",
                "Send the exact digital keys or codes to remove, one per line. Only items that are neither reserved nor sold can be removed.",
            ),
            keyboard([back_button("admin:cancel-input", language)]),
        )

    @router.message(F.text)
    async def receive_admin_input(message: Message) -> None:
        global welcome_media_cache
        if not message.from_user:
            return
        text = (message.text or "").strip()
        if text.casefold().startswith("/start"):
            language = user_language_cache.get(
                message.from_user.id,
                normalize_language(message.from_user.language_code),
            )
            if await handle_broadcast_input(message, language):
                return
            pending_wallet_amount.discard(message.from_user.id)
            pending_admin_media.pop(message.from_user.id, None)
            if (
                pending_admin_input.get(message.from_user.id, (None, None))[0]
                == "bot_setting"
            ):
                pending_admin_input.pop(message.from_user.id, None)
            product_id = package_id_from_start(text)
            if product_id:
                product_data = await store.product(product_id)
                if product_data:
                    await safe_upsert_user(
                        store,
                        message.from_user.id,
                        message.from_user.username,
                        message.from_user.first_name,
                        language,
                    )
                    is_reseller = await store.is_reseller(message.from_user.id)
                    await show_product(
                        message,
                        product_data,
                        1,
                        language,
                        edit_message=False,
                        is_reseller=is_reseller,
                    )
                    return
                logger.info(
                    "Stock notification link references unavailable product %s",
                    product_id,
                )
            await send_welcome(message, store, settings, language, immediate=True)
            return
        language = await safe_user_language(store, message.from_user.id)
        if await handle_broadcast_input(message, language):
            return
        if await capture_pending_admin_media(message, language):
            return
        if await capture_wizard_telegram_media(message, language):
            return
        customer_buttons = {
            "👤 account",
            "account",
            "💰 add funds",
            "add funds",
            "🛍 digital products",
            "digital products",
            "🎮 games service",
            "games service",
            "🛒 my orders",
            "my orders",
            "orders",
            "🎥 how to buy",
            "how to buy",
            "🔗 join group",
            "join group",
        }
        if (
            message.from_user.id in pending_wallet_amount
            and text.casefold() not in customer_buttons
        ):
            try:
                parse_wallet_deposit_amount(text, language)
            except ValueError as exc:
                prompt_message = active_screen_messages.get(message.from_user.id)
                if prompt_message:
                    updated = await prompt_message.edit_text(
                        f"⚠️ {str(exc)}\n\n{wallet_custom_amount_prompt(language)}",
                        reply_markup=prompt_message.reply_markup,
                    )
                    active_screen_messages[message.from_user.id] = (
                        updated or prompt_message
                    )
                    await delete_replaced_message(message)
                else:
                    await send_replacing_screen(
                        message,
                        message.from_user.id,
                        f"⚠️ {str(exc)}\n\n{wallet_custom_amount_prompt(language)}",
                    )
                return
            if len(payments.available_methods) > 1:
                await send_replacing_screen(
                    message,
                    message.from_user.id,
                    copy(
                        language,
                        "សូមជ្រើសរើសវិធីទូទាត់សម្រាប់ការបញ្ចូលប្រាក់។",
                        "Choose a payment method for this deposit.",
                    ),
                    reply_markup=wallet_payment_method_keyboard(
                        text, payments.available_methods, language
                    ),
                )
                pending_wallet_amount.discard(message.from_user.id)
                await delete_replaced_message(message)
                return
            try:
                await start_wallet_deposit(
                    message.from_user,
                    text,
                    language,
                    payments.default_method(),
                )
            except Exception:
                logger.exception(
                    "Could not start custom wallet deposit for Telegram user %s",
                    message.from_user.id,
                )
                await send_replacing_screen(
                    message,
                    message.from_user.id,
                    wallet_error_message(language),
                )
                return
            pending_wallet_amount.discard(message.from_user.id)
            await clear_active_screen(message.from_user.id)
            await delete_replaced_message(message)
            return
        pending_wallet_amount.discard(message.from_user.id)
        pending_setting = pending_admin_input.get(message.from_user.id)
        has_pending_bot_setting = (
            message.from_user.id in settings.admin_ids
            and pending_setting is not None
            and pending_setting[0] == "bot_setting"
        )
        if not has_pending_bot_setting and await show_customer_action(
            message, message.from_user, text, language
        ):
            return
        if message.from_user.id not in settings.admin_ids:
            await send_welcome(message, store, settings, language)
            return
        wizard = admin_wizards.get(message.from_user.id)
        if wizard:
            step = wizard.get("step")
            values = wizard["values"]
            try:
                if step in {"media", "delivery_media"}:
                    raise ValueError(
                        copy(
                            language,
                            "សូមផ្ញើរូបភាព/វីដេអូ ឬចុចរំលង។",
                            "Send a photo/video or use Skip.",
                        )
                    )
                if step == "name":
                    if not text:
                        raise ValueError(
                            copy(
                                language,
                                "ឈ្មោះមិនអាចទទេបានទេ។",
                                "Name cannot be empty.",
                            )
                        )
                    values["name"] = text
                elif step == "description":
                    values["description"] = text
                elif step == "delivery_message":
                    if text != "-" and len(text) > 4096:
                        raise ValueError(
                            copy(
                                language,
                                "សារក្រោយ Order មិនអាចលើស 4096 តួអក្សរបានទេ។",
                                "After-order messages cannot exceed 4096 characters.",
                            )
                        )
                    values["delivery_message"] = "" if text == "-" else text
                elif step == "price":
                    try:
                        price = Decimal(text)
                    except InvalidOperation:
                        raise ValueError(
                            copy(language, "តម្លៃមិនត្រឹមត្រូវទេ។", "Invalid price.")
                        ) from None
                    if not price.is_finite() or price <= 0:
                        raise ValueError(
                            copy(
                                language,
                                "តម្លៃត្រូវតែធំជាង 0។",
                                "Price must be greater than 0.",
                            )
                        )
                    values["price"] = str(price)
                elif step == "reseller_price":
                    try:
                        reseller_price = Decimal(text)
                    except InvalidOperation:
                        raise ValueError(
                            copy(
                                language,
                                "សូមបញ្ចូលតម្លៃ Reseller ត្រឹមត្រូវ។",
                                "Enter a valid reseller price.",
                            )
                        ) from None
                    if (
                        not reseller_price.is_finite()
                        or reseller_price <= 0
                    ):
                        raise ValueError(
                            copy(
                                language,
                                "តម្លៃ Reseller ត្រូវតែធំជាង 0។",
                                "Reseller price must be greater than 0.",
                            )
                        )
                    values["reseller_price"] = str(reseller_price)
                else:
                    raise ValueError(
                        copy(language, "Wizard មិនសកម្មទេ។", "No active wizard step.")
                    )
                steps = ADMIN_WIZARD_STEPS[wizard["kind"]]
                current_index = steps.index(step)
                wizard["step"] = (
                    steps[current_index + 1] if current_index + 1 < len(steps) else None
                )
            except ValueError as exc:
                await show_admin_wizard(
                    message,
                    message.from_user.id,
                    language,
                    edit_message=False,
                    notice=str(exc),
                )
                return
            await show_admin_wizard(
                message, message.from_user.id, language, edit_message=False
            )
            return
        if text.casefold() in {"🛍 buy now", "buy now"}:
            await show_catalogs(message, 0, language, edit_message=False)
            return
        try:
            stock_add = parse_stock_add(text, language)
            if stock_add:
                product_id, credentials = stock_add
                added = await store.import_stock(
                    product_id, credentials, settings.stock_encryption_key
                )
                await notify_stock_added(bot, store, product_id, added)
                await send_replacing_screen(
                    message,
                    message.from_user.id,
                    copy(
                        language,
                        f"បានបញ្ចូលស្តុកថ្មីចំនួន {added}។",
                        f"Added {added} new stock item(s).",
                    ),
                )
                return
        except InputValidationError as exc:
            await send_replacing_screen(
                message, message.from_user.id, html.escape(str(exc))
            )
            return
        except ValueError:
            logger.exception(
                "Direct stock import failed for admin %s", message.from_user.id
            )
            await send_replacing_screen(
                message,
                message.from_user.id,
                copy(
                    language,
                    "មិនអាចបញ្ចូលស្តុកបានទេ។ សូមពិនិត្យ Product ID និងស្តុក រួចព្យាយាមម្ដងទៀត។",
                    "Could not add stock. Check the product ID and stock data, then try again.",
                ),
            )
            return
        except Exception:
            logger.exception(
                "Direct stock import failed for admin %s", message.from_user.id
            )
            await send_replacing_screen(
                message,
                message.from_user.id,
                copy(
                    language,
                    "មិនអាចបញ្ចូលស្តុកបានទេ។ សូមពិនិត្យ Product ID រួចព្យាយាមម្ដងទៀត។",
                    "Could not add stock. Check the product ID and try again.",
                ),
            )
            return
        pending = pending_admin_input.get(message.from_user.id)
        if not pending:
            if text.casefold() in {"⚙️ admin", "admin"}:
                await send_replacing_screen(
                    message,
                    message.from_user.id,
                    copy(
                        language,
                        "⚙️ បើកផ្ទាំងគ្រប់គ្រងហាង៖",
                        "⚙️ Open store administration:",
                    ),
                    reply_markup=keyboard(
                        [
                            [
                                InlineKeyboardButton(
                                    text=copy(
                                        language, "⚙️ គ្រប់គ្រងហាង", "⚙️ Store admin"
                                    ),
                                    callback_data="admin",
                                )
                            ]
                        ]
                    ),
                )
            else:
                await send_welcome(message, store, settings, language)
            return
        action, target_id = pending
        try:
            if action == "bot_setting":
                if target_id not in BOT_SETTING_LABELS:
                    raise InputValidationError(
                        copy(language, "Setting មិនត្រឹមត្រូវទេ។", "Invalid setting.")
                    )
                value = "" if text == "-" else text.strip()
                if target_id == "support_username" and value:
                    value = value.removeprefix("@")
                    if not value.isascii() or not value.replace("_", "").isalnum():
                        raise InputValidationError(
                            copy(
                                language,
                                "សូមបញ្ចូល Telegram username ដូចជា @support។",
                                "Enter a Telegram username such as @support.",
                            )
                        )
                elif (
                    target_id
                    in {
                        "how_to_buy_caption",
                        "how_to_buy_caption_km",
                        "how_to_buy_caption_en",
                    }
                    and len(value) > 1024
                ):
                    raise InputValidationError(
                        copy(
                            language,
                            "Caption មិនអាចលើស 1024 តួអក្សរបានទេ។",
                            "Captions cannot exceed 1024 characters.",
                        )
                    )
                elif (
                    target_id
                    in {"stock_notification_chat_ids", "order_notification_chat_ids"}
                    and len(value) > 2000
                ):
                    raise InputValidationError(
                        copy(language, "តម្លៃវែងពេក។", "The value is too long.")
                    )
                elif target_id == "install_group_url" and value:
                    parsed_url = urlsplit(value)
                    if (
                        parsed_url.scheme != "https"
                        or not parsed_url.netloc
                        or len(value) > 2048
                    ):
                        raise InputValidationError(
                            copy(
                                language,
                                "សូមបញ្ចូល HTTPS URL ត្រឹមត្រូវ។",
                                "Enter a valid HTTPS URL.",
                            )
                        )
                await store.set_bot_setting(target_id, value)
                if target_id in {"welcome_photo_url", "welcome_video_url"}:
                    welcome_media_cache = None
            elif action == "reseller_add":
                if not text.isdecimal():
                    raise InputValidationError(
                        copy(
                            language,
                            "សូមបញ្ចូល Telegram ID ជាលេខវិជ្ជមាន។",
                            "Enter a positive numeric Telegram ID.",
                        )
                    )
                await store.add_reseller(int(text))
            elif action == "stock_add":
                credentials = [
                    line.strip() for line in text.splitlines() if line.strip()
                ]
                if not credentials:
                    raise InputValidationError(
                        copy(
                            language,
                            "សូមបញ្ចូល Digital Key ឬ Code យ៉ាងហោចណាស់មួយ។",
                            "Enter at least one digital key or code.",
                        )
                    )
                added = await store.import_stock(
                    target_id, credentials, settings.stock_encryption_key
                )
                await notify_stock_added(bot, store, target_id, added)
            elif action == "stock_remove":
                credentials = [
                    line.strip() for line in text.splitlines() if line.strip()
                ]
                if not credentials:
                    raise InputValidationError(
                        copy(
                            language,
                            "សូមបញ្ចូល Digital Key ឬ Code យ៉ាងហោចណាស់មួយ។",
                            "Enter at least one digital key or code.",
                        )
                    )
                removed = await store.remove_stock(
                    target_id, credentials, settings.stock_encryption_key
                )
            else:
                return
        except InputValidationError as exc:
            await update_active_screen(
                message.from_user.id,
                copy(
                    language,
                    f"⚠️ ព័ត៌មានមិនត្រឹមត្រូវ៖ {html.escape(str(exc))}\n\nសូមផ្ញើម្តងទៀត។",
                    f"⚠️ Invalid input: {html.escape(str(exc))}\n\nPlease send it again.",
                ),
                input_message=message,
                reply_markup=keyboard([back_button("admin:cancel-input", language)]),
            )
            return
        except Exception:
            logger.exception(
                "Admin catalog update failed for user %s", message.from_user.id
            )
            await update_active_screen(
                message.from_user.id,
                copy(
                    language,
                    "⚠️ មិនអាចរក្សាទុកបានទេ។ សូមផ្ញើតម្លៃម្តងទៀត។",
                    "⚠️ Could not save the changes. Please send the value again.",
                ),
                input_message=message,
                reply_markup=keyboard([back_button("admin:cancel-input", language)]),
            )
            return
        pending_admin_input.pop(message.from_user.id, None)
        if action == "bot_setting":
            await update_active_screen(
                message.from_user.id,
                copy(
                    language,
                    "✅ បានរក្សាទុក Bot setting ជោគជ័យ។",
                    "✅ Bot setting saved.",
                ),
                input_message=message,
                reply_markup=keyboard(
                    [
                        back_button(
                            f"admin:settings:section:{bot_setting_section(target_id)}",
                            language,
                        )
                    ]
                ),
            )
            return
        if action in {"stock_add", "stock_remove"}:
            if action == "stock_add":
                result_message = copy(
                    language,
                    f"បានបញ្ចូលស្តុកថ្មីចំនួន {added}។",
                    f"Added {added} new stock item(s).",
                )
            else:
                result_message = copy(
                    language,
                    f"បានដកស្តុកដែលមិនទាន់លក់ចំនួន {removed}។",
                    f"Removed {removed} unsold stock item(s).",
                )
            await update_active_screen(
                message.from_user.id,
                result_message,
                input_message=message,
                reply_markup=keyboard([back_button(f"ap:{target_id}", language)]),
            )
            return
        await update_active_screen(
            message.from_user.id,
            copy(
                language,
                "បានផ្តល់សិទ្ធិ Reseller ដោយជោគជ័យ។",
                "Reseller pricing enabled successfully.",
            ),
            input_message=message,
            reply_markup=keyboard([back_button("admin:resellers", language)]),
        )

    @router.callback_query(F.data == "admin:upload")
    async def choose_stock_product(callback: CallbackQuery) -> None:
        await callback.answer()
        language = await safe_user_language(store, callback.from_user.id)
        if callback.from_user.id not in settings.admin_ids:
            return
        result = (
            await store.client.table("categories")
            .select("id,name")
            .eq("active", True)
            .order("sort_order")
            .execute()
        )
        categories = result.data or []
        rows = [
            [
                InlineKeyboardButton(
                    text=f"🗂 {item['name']}",
                    callback_data=f"admin:upload:catalog:{item['id']}",
                )
            ]
            for item in categories
        ]
        rows.append(back_button("admin", language))
        await callback.message.edit_text(
            copy(
                language,
                "ជ្រើសរើស Catalog សម្រាប់បញ្ចូលស្តុក៖",
                "Choose a catalog for the stock import:",
            ),
            reply_markup=keyboard(rows),
        )

    @router.callback_query(F.data.startswith("admin:upload:catalog:"))
    async def choose_stock_package(callback: CallbackQuery) -> None:
        await callback.answer()
        language = await safe_user_language(store, callback.from_user.id)
        if callback.from_user.id not in settings.admin_ids:
            return
        category_id = callback.data.removeprefix("admin:upload:catalog:")
        result = (
            await store.client.table("products")
            .select("id,name")
            .eq("category_id", category_id)
            .eq("active", True)
            .order("sort_order")
            .execute()
        )
        products = result.data or []
        rows = [
            [InlineKeyboardButton(text=item["name"], callback_data=f"au:{item['id']}")]
            for item in products
        ]
        rows.append(back_button("admin:upload", language))
        await callback.message.edit_text(
            copy(
                language,
                "ជ្រើសរើស Package សម្រាប់បញ្ចូលស្តុក៖",
                "Choose a package for the stock import:",
            ),
            reply_markup=keyboard(rows),
        )

    @router.callback_query(F.data.startswith("au:"))
    async def request_stock_file(callback: CallbackQuery) -> None:
        await callback.answer()
        language = await safe_user_language(store, callback.from_user.id)
        if callback.from_user.id not in settings.admin_ids:
            return
        pending_stock_upload[callback.from_user.id] = callback.data[3:]
        await replace_callback_screen(
            callback,
            copy(
                language,
                "សូមផ្ញើឯកសារ .txt ដែលមាន Digital Key ឬ Code មួយក្នុងមួយជួរ។",
                "Send a .txt file with one digital key or code per line.",
            ),
            keyboard([back_button("admin:cancel-upload", language)]),
        )

    @router.callback_query(F.data == "admin:cancel-upload")
    async def cancel_stock_file(callback: CallbackQuery) -> None:
        pending_stock_upload.pop(callback.from_user.id, None)
        language = await safe_user_language(store, callback.from_user.id)
        await callback.answer(copy(language, "បានបោះបង់", "Cancelled"))
        await replace_callback_screen(
            callback,
            copy(language, "បានបោះបង់ការបញ្ចូលស្តុក។", "Stock import cancelled."),
            keyboard([back_button("admin", language)]),
        )

    @router.message(F.document)
    async def import_stock_file(message: Message) -> None:
        if not message.from_user or message.from_user.id not in settings.admin_ids:
            return
        language = await safe_user_language(store, message.from_user.id)
        if await handle_broadcast_input(message, language):
            return
        if await capture_pending_admin_media(message, language):
            return
        if await capture_wizard_telegram_media(message, language):
            return
        product_id = pending_stock_upload.get(message.from_user.id)
        if not product_id:
            return
        document = message.document
        if (
            not document.file_name
            or not document.file_name.lower().endswith(".txt")
            or (document.file_size or 0) > 1_000_000
        ):
            await update_active_screen(
                message.from_user.id,
                copy(
                    language,
                    "⚠️ សូមប្រើឯកសារ .txt ទំហំមិនលើស 1 MB។",
                    "⚠️ Use a .txt file no larger than 1 MB.",
                ),
                input_message=message,
                reply_markup=keyboard([back_button("admin:cancel-upload", language)]),
            )
            return
        try:
            buffer = io.BytesIO()
            await bot.download(document.file_id, destination=buffer)
            credentials = [
                line.strip()
                for line in buffer.getvalue().decode("utf-8").splitlines()
                if line.strip()
            ]
            if not credentials:
                raise InputValidationError(
                    copy(
                        language,
                        "ឯកសារត្រូវមាន Digital Key ឬ Code យ៉ាងហោចណាស់មួយ។",
                        "The file must contain at least one digital key or code.",
                    )
                )
            added = await store.import_stock(
                product_id, credentials, settings.stock_encryption_key
            )
        except (UnicodeDecodeError, InputValidationError):
            await update_active_screen(
                message.from_user.id,
                copy(
                    language,
                    "⚠️ ឯកសារមិនត្រឹមត្រូវ ឬមិនអាចបញ្ចូលស្តុកបានទេ។",
                    "⚠️ The file is invalid or could not be imported.",
                ),
                input_message=message,
                reply_markup=keyboard([back_button("admin:cancel-upload", language)]),
            )
            return
        except ValueError:
            logger.exception("Stock import failed for product %s", product_id)
            await update_active_screen(
                message.from_user.id,
                copy(
                    language,
                    "⚠️ មិនអាចបញ្ចូលស្តុកបានទេ។ សូមព្យាយាមម្ដងទៀត។",
                    "⚠️ Could not import stock. Please try again.",
                ),
                input_message=message,
                reply_markup=keyboard([back_button("admin:cancel-upload", language)]),
            )
            return
        except Exception:
            logger.exception("Stock import failed for product %s", product_id)
            await update_active_screen(
                message.from_user.id,
                copy(
                    language,
                    "⚠️ មិនអាចបញ្ចូលស្តុកបានទេ។ សូមព្យាយាមម្ដងទៀត។",
                    "⚠️ Could not import stock. Please try again.",
                ),
                input_message=message,
                reply_markup=keyboard([back_button("admin:cancel-upload", language)]),
            )
            return
        pending_stock_upload.pop(message.from_user.id, None)
        await notify_stock_added(bot, store, product_id, added)
        await update_active_screen(
            message.from_user.id,
            copy(
                language,
                f"បានបញ្ចូលស្តុកថ្មីចំនួន {added}។",
                f"Added {added} new stock item(s).",
            ),
            input_message=message,
            reply_markup=keyboard([back_button("admin", language)]),
        )

    @router.message(
        F.content_type.in_(
            {
                "audio",
                "voice",
                "animation",
                "video_note",
                "sticker",
                "contact",
                "dice",
                "game",
                "poll",
                "venue",
                "location",
                "live_photo",
            }
        )
    )
    async def receive_other_broadcast_media(message: Message) -> None:
        if not message.from_user or message.from_user.id not in settings.admin_ids:
            return
        language = await safe_user_language(store, message.from_user.id)
        if await handle_broadcast_input(message, language):
            return
        if await capture_pending_admin_media(message, language):
            return
        await capture_wizard_telegram_media(message, language)

    @router.message()
    async def receive_unlisted_broadcast_content(message: Message) -> None:
        if not message.from_user or message.from_user.id not in settings.admin_ids:
            return
        language = await safe_user_language(store, message.from_user.id)
        if await handle_broadcast_input(message, language):
            return
        if await capture_pending_admin_media(message, language):
            return
        await capture_wizard_telegram_media(message, language)

    @router.callback_query(F.data == "admin:sales")
    async def sales_summary(callback: CallbackQuery) -> None:
        await callback.answer()
        language = await safe_user_language(store, callback.from_user.id)
        if callback.from_user.id not in settings.admin_ids:
            return
        summary = await store.sales_summary()
        text = copy(
            language,
            f"ស្ថិតិការលក់\nការបញ្ជាទិញដែលបានបង់៖ {summary['paid_orders']}\nចំណូលសរុប៖ {money(summary['total_revenue'])}\nចំណូលថ្ងៃនេះ៖ {money(summary['today_revenue'])}",
            f"Sales summary\nPaid orders: {summary['paid_orders']}\nTotal revenue: {money(summary['total_revenue'])}\nToday's revenue: {money(summary['today_revenue'])}",
        )
        await callback.message.edit_text(
            text, reply_markup=keyboard([back_button("admin", language)])
        )

    dispatcher.include_router(router)


async def reconcile_payments(
    bot: Bot, store: Store, payments: KHQRPayGateway, settings: Settings
) -> None:
    wallet_schema_warning_logged = False
    while True:
        try:
            for order in await store.pending_orders():
                try:
                    payment = await payments.check_payment(
                        order["transaction_id"], order.get("payment_provider")
                    )
                    status = payment.get("status")
                    if status == "paid":
                        await deliver_order(
                            bot, store, settings, order["id"], int(order["chat_id"])
                        )
                    elif status in {"expired", "failed"}:
                        await expire_payment(
                            bot, store, order["id"], int(order["chat_id"]), status
                        )
                except Exception:
                    logger.exception("Reconciliation failed for order %s", order["id"])
            for order in await store.undelivered_orders():
                try:
                    await deliver_order(
                        bot, store, settings, order["id"], int(order["chat_id"])
                    )
                except Exception:
                    logger.exception("Delivery retry failed for order %s", order["id"])
        except Exception:
            logger.exception("Could not reconcile pending orders")

        try:
            deposits = await store.pending_wallet_deposits()
        except Exception as exc:
            if is_wallet_schema_missing(exc):
                if not wallet_schema_warning_logged:
                    logger.error(
                        "Wallet database tables are missing; run the latest schema.sql in Supabase SQL Editor."
                    )
                wallet_schema_warning_logged = True
            else:
                logger.exception("Could not load pending wallet deposits")
        else:
            wallet_schema_warning_logged = False
            for deposit in deposits:
                try:
                    payment = await payments.check_payment(
                        deposit["transaction_id"], deposit.get("payment_provider")
                    )
                    status = payment.get("status")
                    if status == "paid":
                        if Decimal(str(payment.get("amount"))) != Decimal(
                            str(deposit["amount"])
                        ):
                            logger.error(
                                "KHQRPay amount mismatch for wallet deposit %s",
                                deposit["id"],
                            )
                            continue
                        await complete_wallet_deposit(bot, store, deposit)
                    elif status in {"expired", "failed"}:
                        await expire_wallet_deposit(bot, store, deposit, status)
                except Exception:
                    logger.exception(
                        "Wallet deposit reconciliation failed for %s", deposit["id"]
                    )
        await asyncio.sleep(settings.poll_interval_seconds)


async def reconcile_broadcasts(bot: Bot, store: Store) -> None:
    while True:
        try:
            jobs = await store.due_broadcast_jobs(
                datetime.now(PHNOM_PENH_TIMEZONE).astimezone(timezone.utc).isoformat()
            )
        except Exception:
            logger.exception("Could not load scheduled broadcast jobs")
            await asyncio.sleep(10)
            continue

        for job in jobs:
            admin_id = int(job["admin_id"])
            if admin_id in broadcast_active_keys:
                continue
            try:
                claimed = await store.claim_broadcast_job(str(job["id"]))
            except Exception:
                logger.exception("Could not claim scheduled broadcast %s", job["id"])
                continue
            if not claimed:
                continue

            job_id = str(job["id"])
            event = asyncio.Event()
            broadcast_cancel_events[job_id] = event
            broadcast_active_keys[admin_id] = job_id
            try:
                progress_message = await bot.send_message(
                    admin_id,
                    "⏳ Scheduled broadcast is starting...",
                )
                sent, failed, cancelled = await execute_broadcast(
                    bot,
                    store,
                    admin_id,
                    job.get("content") or [],
                    job.get("buttons") or [],
                    progress_message,
                    event,
                )
                status = "cancelled" if cancelled else "sent"
                await store.finish_broadcast_job(job_id, status, sent, failed)
                await delete_broadcast_source(bot, admin_id, job.get("content") or [])
            except Exception:
                logger.exception("Scheduled broadcast %s failed", job_id)
                try:
                    await store.finish_broadcast_job(job_id, "failed")
                    await bot.send_message(
                        admin_id,
                        f"❌ Scheduled broadcast {job_id} failed. Check the application logs.",
                    )
                except Exception:
                    logger.exception(
                        "Could not report scheduled broadcast failure %s", job_id
                    )
            finally:
                broadcast_cancel_events.pop(job_id, None)
                if broadcast_active_keys.get(admin_id) == job_id:
                    broadcast_active_keys.pop(admin_id, None)
        await asyncio.sleep(10)
