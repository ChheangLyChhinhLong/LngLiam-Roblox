import asyncio
import base64
import html
import io
import logging
from decimal import Decimal, InvalidOperation
from typing import Any
from urllib.parse import urlsplit

import qrcode
import cloudinary.uploader
from aiogram import Bot, Dispatcher, F, Router
from aiogram.exceptions import TelegramBadRequest
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

from app.config import Settings
from app.payments import KHPayClient, aba_mobile_deeplink, aba_mobile_redirect_url
from app.security import decrypt_stock
from app.store import Store

logger = logging.getLogger(__name__)
router = Router()
pending_stock_upload: dict[int, str] = {}
pending_admin_input: dict[int, tuple[str, str | None]] = {}
admin_wizards: dict[int, dict[str, Any]] = {}
welcome_message_ids: dict[int, int] = {}
payment_message_ids: dict[str, tuple[int, int]] = {}
CATALOG_PAGE_SIZE = 8
ADMIN_WIZARD_STEPS = {
    "product": ("media", "name", "description", "price", "reseller_price"),
    "category": ("name", "description"),
}


def keyboard(rows: list[list[InlineKeyboardButton]]) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=rows)


def paginate(items: list[Any], page: int) -> tuple[list[Any], int, int]:
    page_count = max(1, (len(items) + CATALOG_PAGE_SIZE - 1) // CATALOG_PAGE_SIZE)
    page = max(0, min(page, page_count - 1))
    start = page * CATALOG_PAGE_SIZE
    return items[start : start + CATALOG_PAGE_SIZE], page, page_count


def pagination_row(page: int, page_count: int, callback_prefix: str, language: str) -> list[InlineKeyboardButton]:
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
    row.append(InlineKeyboardButton(text=f"{page + 1} / {page_count}", callback_data="noop"))
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
        return await message.answer(text, parse_mode=parse_mode, reply_markup=reply_markup)
    if message.text is None:
        try:
            await message.delete()
        except TelegramBadRequest:
            logger.debug("Could not delete old photo message %s", message.message_id)
        return await message.answer(text, parse_mode=parse_mode, reply_markup=reply_markup)
    return await message.edit_text(text, parse_mode=parse_mode, reply_markup=reply_markup)


async def delete_replaced_message(message: Message) -> None:
    try:
        await message.delete()
    except TelegramBadRequest:
        logger.debug("Could not delete replaced message %s", message.message_id)


def money(amount: Any) -> str:
    return f"${Decimal(str(amount)):.2f}"


def payment_caption(product_name: str, quantity: int, amount: Any, transaction_ref: str, language: str = "en") -> str:
    total = f"{Decimal(str(amount)):.2f}"
    return (
        f"💳 <b>{copy(language, 'ទូទាត់ $' + total, 'Pay $' + total)}</b>\n\n"
        f"🛍 <b>{copy(language, 'ផលិតផល៖', 'Product:')}</b> {html.escape(product_name)} x{quantity}\n"
        f"💰 <b>{copy(language, 'សរុប៖', 'Total:')}</b> ${total}\n"
        f"🟢 <b>{copy(language, 'នៅសល់ត្រូវបង់៖', 'Remaining to pay:')}</b> ${total}\n"
        f"🏷 <b>{copy(language, 'លេខយោង៖', 'Ref:')}</b> <code>{html.escape(transaction_ref)}</code>\n"
        f"⏱ <b>{copy(language, 'ស្កេន KHQR ដើម្បីបញ្ចប់ការទូទាត់។', 'Scan KHQR to complete payment.')}</b>\n"
        f"⏰ <b>{copy(language, 'អ្នកមានពេល 4 នាទីដើម្បីទូទាត់។ QR code នឹងផុតកំណត់ដោយស្វ័យប្រវត្តិ។', 'You have 4 minutes to pay. The QR refreshes itself.')}</b>"
    )


def normalize_language(language: str | None) -> str:
    code = (language or "km").strip().lower()
    if code in {"km", "km-kh", "kh", "khmer"}:
        return "km"
    if code.startswith("en"):
        return "en"
    return "km"


def copy(language: str | None, khmer: str, english: str) -> str:
    return english if normalize_language(language) == "en" else khmer


def welcome_text(user: Any, language: str = "km", categories: list[dict[str, Any]] | None = None) -> str:
    username = f"@{html.escape(user.username)}" if user.username else "មិនមាន"
    category_rows = []
    for index, item in enumerate(categories or []):
        branch = "└─" if index == len(categories or []) - 1 else "├─"
        category_name = html.escape(str(item.get("name") or ""))
        description = html.escape(str(item.get("description") or "").strip())
        description_text = f" ({description})" if description else ""
        category_rows.append(f"{branch} 🛍 <b>{category_name}</b>{description_text}")
    category_list = "\n".join(category_rows) or copy(
        language,
        "└─ មិនទាន់មាន Catalog ទេ",
        "└─ No catalogs available yet",
    )
    if language == "en":
        username = f"@{html.escape(user.username)}" if user.username else "Not set"
        return (
            f"🙏🏻 <b>Welcome, {html.escape(user.first_name)}!</b> ⚡️\n\n"
            "<b>Account information</b>\n"
            f"├─ 🆔 <b>ID</b>: <code>{user.id}</code>\n"
            f"└─ 👤 <b>Name</b>: {username}\n\n"
            f"🛍 <b>Store categories</b>\n{category_list}\n\n"
            "💬 <b>Choose an option below to get started:</b>"
        )
    return (
        f"🙏🏻 <b>សូមស្វាគមន៍ {html.escape(user.first_name)}!</b> ⚡️\n\n"
        "<b>ព័ត៌មានគណនី</b>\n"
        f"├─ 🆔 <b>ID</b>: <code>{user.id}</code>\n"
        f"└─ 👤 <b>ឈ្មោះ</b>: {username}\n\n"
        f"🛍 <b>ប្រភេទផលិតផលក្នុងហាង</b>\n{category_list}\n\n"
        "💬 <b>សូមចុចប៊ូតុងខាងក្រោមដើម្បីចាប់ផ្តើម៖</b>"
    )


def quantity_limit(stock: int) -> int:
    return max(0, min(3, stock))


def parse_product_caption(caption: str, language: str = "km") -> tuple[str, str, str, str]:
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
        raise ValueError(copy(language, "តម្លៃមិនត្រឹមត្រូវទេ។", "The price is invalid.")) from None
    if not price.is_finite() or price <= 0:
        raise ValueError(copy(language, "តម្លៃត្រូវតែធំជាង 0។", "The price must be greater than 0."))
    reseller_price = price
    description = ""
    if len(fields) == 4:
        try:
            reseller_price = Decimal(fields[2])
        except InvalidOperation:
            raise ValueError(copy(language, "តម្លៃ Reseller មិនត្រឹមត្រូវទេ។", "The reseller price is invalid.")) from None
        if not reseller_price.is_finite() or reseller_price <= 0 or reseller_price >= price:
            raise ValueError(
                copy(
                    language,
                    "តម្លៃ Reseller ត្រូវតែធំជាង 0 និងទាបជាងតម្លៃធម្មតា។",
                    "The reseller price must be greater than 0 and lower than the regular price.",
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
        raise ValueError(copy(language, "សូមប្រើទម្រង់ addstock <product_id> | key1, key2។", "Use the format addstock <product_id> | key1, key2."))
    credentials = [item.strip() for item in stock_text.replace("\n", ",").split(",") if item.strip()]
    if not credentials:
        raise ValueError(copy(language, "សូមបញ្ចូល License Key យ៉ាងហោចណាស់មួយ។", "Enter at least one license key."))
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
    image_data = payment.get("qr_image_base64") or payment.get("image_base64") or payment.get("qr_image")
    if image_data:
        if image_data.startswith("data:image"):
            image_data = image_data.split(",", 1)[-1]
        return BufferedInputFile(base64.b64decode(image_data), filename="payment-qr.png")
    payload = (
        payment.get("qr_payload")
        or payment.get("qr_string")
        or payment.get("qr_code")
        or payment.get("qr_data")
        or payment.get("qr")
        or payment.get("khqr")
    )
    if not payload:
        raise ValueError("KHPAY response is missing QR data")
    image = qrcode.make(str(payload))
    output = io.BytesIO()
    image.save(output, format="PNG")
    return BufferedInputFile(output.getvalue(), filename="payment-qr.png")


async def upload_product_image(bot: Bot, settings: Settings, file_id: str) -> str:
    return await upload_product_media(bot, settings, file_id, "image")


async def upload_product_video(bot: Bot, settings: Settings, file_id: str) -> str:
    return await upload_product_media(bot, settings, file_id, "video")


async def upload_product_media(bot: Bot, settings: Settings, file_id: str, media_type: str) -> str:
    if not all((settings.cloudinary_cloud_name, settings.cloudinary_api_key, settings.cloudinary_api_secret)):
        raise RuntimeError("Cloudinary credentials are not configured")
    if media_type not in {"image", "video"}:
        raise ValueError("Unsupported product media type")
    media_stream = io.BytesIO()
    await bot.download(file_id, destination=media_stream)
    media_stream.seek(0)
    result = await asyncio.to_thread(
        cloudinary.uploader.upload,
        media_stream,
        cloud_name=settings.cloudinary_cloud_name,
        api_key=settings.cloudinary_api_key,
        api_secret=settings.cloudinary_api_secret,
        resource_type=media_type,
        folder="products",
    )
    secure_url = result.get("secure_url")
    if not isinstance(secure_url, str) or not secure_url.startswith("https://"):
        raise ValueError(f"Cloudinary did not return a secure HTTPS {media_type} URL")
    return secure_url


def menu(settings: Settings, user_id: int, language: str = "km") -> InlineKeyboardMarkup:
    labels = {
        "shop": copy(language, "🛍 ហាងលក់", "🛍 Shop"),
        "orders": copy(language, "🛒 ការបញ្ជាទិញ", "🛒 My orders"),
        "guide": copy(language, "🎥 របៀបទិញ", "🎥 How to buy"),
        "language": copy(language, "🌐 ភាសា", "🌐 Language"),
        "support": copy(language, "💬 ជំនួយ", "💬 Support"),
    }
    rows = [
        [InlineKeyboardButton(text=labels["shop"], callback_data="shop")],
        [InlineKeyboardButton(text=labels["orders"], callback_data="history")],
        [InlineKeyboardButton(text=labels["guide"], callback_data="how_to_buy")],
        [
            InlineKeyboardButton(text=labels["language"], callback_data="language"),
            InlineKeyboardButton(text=labels["support"], callback_data="support"),
        ],
    ]
    if user_id in settings.admin_ids:
        rows.append(
            [InlineKeyboardButton(text=copy(language, "⚙️ គ្រប់គ្រងហាង", "⚙️ Store admin"), callback_data="admin")]
        )
    return keyboard(rows)


def welcome_keyboard(user_id: int, settings: Settings) -> ReplyKeyboardMarkup:
    rows = []
    if user_id in settings.admin_ids:
        rows.append([KeyboardButton(text="⚙️ ADMIN")])
    rows.append([KeyboardButton(text="🛍 BUY NOW")])
    return ReplyKeyboardMarkup(keyboard=rows, resize_keyboard=True, is_persistent=True)


def back_button(target: str = "home", language: str = "km") -> list[InlineKeyboardButton]:
    return [
        InlineKeyboardButton(
            text=copy(language, "⬅️ ត្រឡប់", "⬅️ Back"),
            callback_data=target,
        )
    ]


def admin_category_actions(category_id: str, language: str = "km") -> list[list[InlineKeyboardButton]]:
    return [
        [InlineKeyboardButton(text=copy(language, "➕ បន្ថែម Package", "➕ Add package"), callback_data=f"admin:product-add:{category_id}")],
        [
            InlineKeyboardButton(text=copy(language, "✏️ កែ Catalog", "✏️ Edit catalog"), callback_data=f"admin:category-edit:{category_id}"),
            InlineKeyboardButton(text=copy(language, "🗑 បិទ Catalog", "🗑 Disable catalog"), callback_data=f"admin:category-disable:{category_id}"),
        ],
        back_button("admin:catalogs", language),
    ]


def admin_product_actions(product_id: str, category_id: str, language: str = "km") -> list[list[InlineKeyboardButton]]:
    return [
        [
            InlineKeyboardButton(text=copy(language, "✏️ កែ Package", "✏️ Edit package"), callback_data=f"admin:product-edit:{product_id}"),
            InlineKeyboardButton(text=copy(language, "🗑 បិទ Package", "🗑 Disable package"), callback_data=f"admin:product-disable:{product_id}"),
        ],
        [
            InlineKeyboardButton(text=copy(language, "➕ បន្ថែមស្តុក", "➕ Add stock"), callback_data=f"admin:stock-add:{product_id}"),
            InlineKeyboardButton(text=copy(language, "➖ ដកស្តុក", "➖ Remove stock"), callback_data=f"admin:stock-remove:{product_id}"),
        ],
        [InlineKeyboardButton(text=copy(language, "📥 បញ្ចូលស្តុក (.txt)", "📥 Import stock (.txt)"), callback_data=f"au:{product_id}")],
        back_button(f"ac:{category_id}", language),
    ]


def payment_keyboard(
    order_id: str,
    open_url: str,
    language: str = "km",
) -> InlineKeyboardMarkup:
    return keyboard(
        [
            [InlineKeyboardButton(text=copy(language, "🏦 បើកក្នុង ABA Mobile", "🏦 Open in ABA Mobile"), url=open_url)],
            [InlineKeyboardButton(text=copy(language, "✅ ខ្ញុំបានទូទាត់ · ពិនិត្យ", "✅ I have paid · Check"), callback_data=f"check_payment_{order_id}")],
        ]
    )


async def safe_user_language(store: Store, telegram_id: int) -> str:
    try:
        return normalize_language(await store.user_language(telegram_id))
    except Exception:
        logger.exception("Could not load language preference for Telegram user %s", telegram_id)
        return "km"


async def safe_upsert_user(store: Store, telegram_id: int, username: str | None, first_name: str, language: str) -> None:
    try:
        await store.upsert_user(telegram_id, username, first_name, language)
    except Exception:
        logger.exception("Could not save profile for Telegram user %s", telegram_id)


async def notify_stock_added(bot: Bot, store: Store, chat_ids: str, product_id: str, added: int) -> None:
    if added < 1:
        return
    try:
        product = await store.product(product_id)
        if not product:
            logger.warning("Skipping stock notification for unavailable product %s", product_id)
            return
        users = await store.stock_notification_users()
        bot_username = (await bot.get_me()).username
    except Exception:
        logger.exception("Could not prepare stock notification for product %s", product_id)
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
        if language == "en":
            text = f"📦 New stock is available!\n🛍 {product['name']}\n✅ New quantity: {added}"
            button_text = "🛍 View product"
        elif language == "km":
            text = f"📦 មានស្តុកថ្មីហើយ!\n🛍 {product['name']}\n✅ ចំនួនថ្មី៖ {added}"
            button_text = "🛍 មើល Package"
        else:
            text = (
                f"📦 មានស្តុកថ្មី / New stock is available\n"
                f"🛍 {product['name']}\n✅ ចំនួនថ្មី / New quantity: {added}"
            )
            button_text = "🛍 មើល Package / View product"
        keyboard_markup = keyboard(
            [
                [
                    InlineKeyboardButton(
                        text=button_text,
                        url=f"https://t.me/{bot_username.lstrip('@')}",
                    )
                ]
            ]
        )
        try:
            await bot.send_message(chat_id, text, reply_markup=keyboard_markup)
            sent += 1
        except Exception:
            failed += 1
            logger.debug("Could not send stock notification to %s", chat_id, exc_info=True)
        if index % 20 == 0:
            await asyncio.sleep(1)
    logger.info(
        "Stock notification for product %s: sent to %s chats, failed for %s",
        product_id,
        sent,
        failed,
    )


async def send_welcome(message: Message, store: Store, settings: Settings) -> None:
    user = message.from_user
    if not user:
        return
    language = await safe_user_language(store, user.id)
    await safe_upsert_user(store, user.id, user.username, user.first_name, language)
    try:
        categories = await store.categories()
    except Exception:
        logger.exception("Could not load categories for user %s", user.id)
        categories = []
    previous_message_id = welcome_message_ids.get(user.id)
    if previous_message_id:
        try:
            await message.bot.delete_message(chat_id=user.id, message_id=previous_message_id)
        except TelegramBadRequest:
            logger.debug("Could not delete previous welcome message %s", previous_message_id)
    text = welcome_text(user, language, categories)
    settings_data = {}
    load_settings = getattr(store, "bot_settings", None)
    if load_settings:
        try:
            settings_data = await load_settings(("welcome_video_url", "welcome_photo_url"))
        except Exception:
            logger.exception("Could not load welcome media settings")
    reply_markup = welcome_keyboard(user.id, settings)
    video_url = settings_data.get("welcome_video_url")
    photo_url = settings_data.get("welcome_photo_url")
    if video_url:
        welcome_message = await message.answer_video(
            video=video_url,
            caption=text,
            parse_mode="HTML",
            reply_markup=reply_markup,
        )
    elif photo_url:
        welcome_message = await message.answer_photo(
            photo=photo_url,
            caption=text,
            parse_mode="HTML",
            reply_markup=reply_markup,
        )
    else:
        welcome_message = await message.answer(
            text,
            parse_mode="HTML",
            reply_markup=reply_markup,
        )
    welcome_message_ids[user.id] = welcome_message.message_id


async def deliver_order(bot: Bot, store: Store, settings: Settings, order_id: str, chat_id: int) -> None:
    await delete_payment_message(bot, store, order_id)
    install_url = getattr(settings, "install_group_url", "")
    if hasattr(store, "bot_settings"):
        try:
            configured_links = await store.bot_settings(("channel_install_link",))
        except Exception:
            logger.exception("Could not load installation link for order %s", order_id)
        else:
            install_url = configured_links.get("channel_install_link") or install_url
    parsed_install_url = urlsplit(install_url)
    if parsed_install_url.scheme != "https" or not parsed_install_url.netloc:
        raise RuntimeError("A valid HTTPS installation group URL is required to deliver orders")
    credentials = await store.fulfill_order(order_id)
    if not credentials:
        return
    keys = [decrypt_stock(value, settings.stock_encryption_key) for value in credentials]
    key_lines = "\n".join(f"- <code>{html.escape(value)}</code>" for value in keys)
    text = (
        "✅ ទំនិញបានជោគជ័យ\n\n"
        f"🔑 បញ្ជី Key របស់អ្នក [ សរុប {len(keys)} Key ]\n"
        f"{key_lines}\n\n"
        "🙏 អរគុណសម្រាប់ការទិញទំនិញ 🙏\n\n"
        "📲 ចុចលីងខាងក្រោមដើម្បីដំឡើង 📲"
    )
    delivery_rows = [
        [
            InlineKeyboardButton(
                text=f"📋 Copy Key {index}",
                copy_text=CopyTextButton(text=value),
            )
        ]
        for index, value in enumerate(keys, start=1)
    ]
    delivery_rows.append([InlineKeyboardButton(text="👉 ចូលក្រុមដំឡើង", url=install_url)])
    await bot.send_message(
        chat_id,
        text,
        parse_mode="HTML",
        reply_markup=keyboard(delivery_rows),
    )
    await store.mark_delivered(order_id)


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
    payments: KHPayClient,
    settings: Settings,
) -> None:
    async def show_catalogs(message: Message, page: int, language: str, edit_message: bool = True) -> None:
        categories = await store.categories()
        if not categories:
            await edit_text_message(
                message,
                copy(
                    language,
                    "បច្ចុប្បន្នមិនមានប្រភេទទំនិញទេ។ សូមពិនិត្យម្ដងទៀតពេលក្រោយ។",
                    "There are no product categories yet. Please check back later.",
                ),
                reply_markup=keyboard([back_button(language=language)]),
                edit_message=edit_message,
            )
            return
        visible_categories, page, page_count = paginate(categories, page)
        rows = [
            [InlineKeyboardButton(text=f"🛍 {item['name']}", callback_data=f"c:{item['id']}")]
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
        await edit_text_message(
            message,
            text,
            parse_mode="HTML",
            reply_markup=keyboard(rows),
            edit_message=edit_message,
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
                copy(language, "ប្រភេទផលិតផលនេះមិនមានទៀតទេ។", "This category is no longer available."),
                reply_markup=keyboard([back_button("shop", language)]),
            )
            return
        products = await store.products(category_id)
        if not products:
            await edit_text_message(
                message,
                f"🛍 <b>{html.escape(category_data['name'])}</b>\n"
                "───────────────────\n"
                + copy(language, "ប្រភេទនេះមិនទាន់មាន Package ទេ។", "There are no packages in this category yet."),
                parse_mode="HTML",
                reply_markup=keyboard([back_button("shop", language)]),
            )
            return
        visible_products, page, page_count = paginate(products, page)
        stock_counts = await asyncio.gather(*(store.available_stock(item["id"]) for item in visible_products))
        rows = []
        for item, stock in zip(visible_products, stock_counts):
            stock_label = str(stock) if stock else copy(language, "អស់ស្តុក", "Out")
            price = product_price(item, is_reseller)
            rows.append(
                [
                    InlineKeyboardButton(
                        text=f"📦 {item['name']} • {money(price)} ({stock_label})",
                        callback_data=f"p:{item['id']}",
                    )
                ]
            )
        navigation = pagination_row(page, page_count, f"shop:packages:{category_id}", language)
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
        await edit_text_message(message, text, parse_mode="HTML", reply_markup=keyboard(rows))

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
    ) -> None:
        async def render(text: str, **kwargs: Any) -> None:
            if edit_message:
                await message.edit_text(text, **kwargs)
            else:
                await message.answer(text, **kwargs)

        wizard = admin_wizards[user_id]
        kind = wizard["kind"]
        steps = ADMIN_WIZARD_STEPS[kind]
        step = wizard.get("step")
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
                values = wizard["values"]
                summary = (
                    f"📦 <b>{html.escape(values['name'])}</b>\n"
                    f"{copy(language, 'ព័ត៌មានលម្អិត៖', 'Details:')} {html.escape(values.get('description') or '-')}\n"
                    f"{copy(language, 'តម្លៃអតិថិជន៖', 'Customer price:')} {money(values['price'])}\n"
                    f"{copy(language, 'តម្លៃ Reseller៖', 'Reseller price:')} {money(values['reseller_price'])}\n"
                    f"{copy(language, 'Media៖', 'Media:')} "
                    f"{'✅' if values.get('image_url') or values.get('video_url') or values.get('media_file_id') else '—'}"
                )
            else:
                values = wizard["values"]
                summary = (
                    f"🗂 <b>{html.escape(values['name'])}</b>\n"
                    f"{copy(language, 'ព័ត៌មានលម្អិត៖', 'Details:')} {html.escape(values.get('description') or '-')}"
                )
            await render(
                f"{title}\n───────────────────\n{summary}",
                parse_mode="HTML",
                reply_markup=keyboard(rows),
            )
            return

        step_number = steps.index(step) + 1
        step_labels = {
            "media": copy(language, "រូបភាព/វីដេអូ Package", "Package photo/video"),
            "name": copy(language, "ឈ្មោះ", "Name"),
            "description": copy(language, "ព័ត៌មានលម្អិត", "Details"),
            "price": copy(language, "តម្លៃទូទៅ", "Customer price"),
            "reseller_price": copy(language, "តម្លៃ Reseller", "Reseller price"),
        }
        prompts = {
            "media": copy(language, "សូមផ្ញើរូបភាព ឬវីដេអូ Package។", "Send a package photo or video."),
            "name": copy(language, "សូមបញ្ចូលឈ្មោះផលិតផល។", "Enter the product name."),
            "description": copy(language, "សូមបញ្ចូលព័ត៌មានលម្អិត។", "Enter the details."),
            "price": copy(language, "សូមបញ្ចូលតម្លៃអតិថិជន (USD)។", "Enter the customer price (USD)."),
            "reseller_price": copy(language, "សូមបញ្ចូលតម្លៃ Reseller (USD) ដែលទាបជាងតម្លៃទូទៅ។", "Enter a reseller price (USD) lower than the customer price."),
        }
        wizard["step_index"] = step_number - 1
        rows: list[list[InlineKeyboardButton]] = []
        optional = step in {"media", "description", "reseller_price"}
        can_skip = (wizard["edit"] or optional) and not (
            step == "reseller_price" and wizard.get("must_set_reseller_price")
        )
        if can_skip:
            rows.append(
                [
                    InlineKeyboardButton(
                        text=copy(language, "⏩ រំលង / ទុកដដែល", "⏩ Skip / Keep current"),
                        callback_data=f"admin:wizard-skip:{step}",
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
            f"🧭 <b>{copy(language, 'ជំហាន', 'Step')} {step_number}/{len(steps)} · {step_labels[step]}</b>\n"
            "───────────────────\n"
            f"{prompts[step]}",
            parse_mode="HTML",
            reply_markup=keyboard(rows),
        )

    async def finish_admin_wizard(user_id: int, language: str) -> str:
        wizard = admin_wizards[user_id]
        values = wizard["values"]
        kind = wizard["kind"]
        if kind == "product":
            if not values.get("name") or not values.get("price"):
                raise ValueError(copy(language, "ឈ្មោះ និងតម្លៃទូទៅត្រូវតែបញ្ចូល។", "Product name and customer price are required."))
            price = Decimal(str(values["price"]))
            reseller_price = Decimal(str(values.get("reseller_price") or price))
            if (
                not price.is_finite()
                or price <= 0
                or not reseller_price.is_finite()
                or reseller_price <= 0
                or reseller_price > price
            ):
                raise ValueError(copy(language, "តម្លៃមិនត្រឹមត្រូវទេ។ តម្លៃ Reseller ត្រូវមិនលើសតម្លៃអតិថិជន។", "Invalid prices. Reseller price must not exceed the customer price."))

            media_url = None
            media_type = None
            if values.get("media_file_id"):
                media_type = values["media_type"]
                media_url = await upload_product_media(bot, settings, values["media_file_id"], media_type)
            if wizard["action"] == "product_add":
                if wizard.get("category_id") is None:
                    raise ValueError(copy(language, "សូមជ្រើសរើស Catalog ជាមុនសិន។", "Choose a catalog first."))
                await store.create_product(
                    wizard["category_id"],
                    values["name"],
                    str(price),
                    values.get("description", ""),
                    image_url=media_url if media_type == "image" else None,
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
                image_url=media_url if media_type == "image" else None,
                video_url=media_url if media_type == "video" else None,
                update_media=media_type is not None,
            )
            return f"ap:{wizard['target_id']}"

        if not values.get("name"):
            raise ValueError(copy(language, "ឈ្មោះ Catalog ត្រូវតែបញ្ចូល។", "Catalog name is required."))
        category_values = {
            "name": values["name"],
            "description": values.get("description", ""),
        }
        if wizard["action"] == "category_add":
            await store.client.table("categories").insert(category_values).execute()
            return "admin:catalogs"
        await store.client.table("categories").update(category_values).eq("id", wizard["target_id"]).execute()
        return f"ac:{wizard['target_id']}"

    @router.callback_query(F.data == "home")
    async def home(callback: CallbackQuery) -> None:
        await callback.answer()
        language = await safe_user_language(store, callback.from_user.id)
        categories = await store.categories()
        await delete_replaced_message(callback.message)
        welcome_message = await bot.send_message(
            callback.from_user.id,
            welcome_text(callback.from_user, language, categories),
            parse_mode="HTML",
            reply_markup=welcome_keyboard(callback.from_user.id, settings),
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
        try:
            await store.set_user_language(callback.from_user.id, language)
        except Exception:
            logger.exception("Could not save language preference for Telegram user %s", callback.from_user.id)
        await safe_upsert_user(
            store,
            callback.from_user.id,
            callback.from_user.username,
            callback.from_user.first_name,
            language,
        )
        await callback.answer()
        categories = await store.categories()
        await callback.message.edit_text(
            welcome_text(callback.from_user, language, categories),
            parse_mode="HTML",
            reply_markup=menu(settings, callback.from_user.id, language),
        )

    @router.callback_query(F.data.in_({"how_to_buy", "guide"}))
    async def buying_guide(callback: CallbackQuery) -> None:
        await callback.answer()
        language = await safe_user_language(store, callback.from_user.id)
        back_to_menu = keyboard(
            [[InlineKeyboardButton(text=copy(language, "⬅️ ត្រឡប់ទៅម៉ឺនុយ", "⬅️ Back to menu"), callback_data="home")]]
        )
        try:
            settings_data = await store.bot_settings(
                ("how_to_buy_video_url", "how_to_buy_caption", "how_to_buy_caption_km", "how_to_buy_caption_en")
            )
        except Exception:
            logger.exception("Could not load tutorial settings")
            await edit_text_message(
                callback.message,
                copy(language, "មិនអាចទាញយកវីដេអូណែនាំបានទេ។", "Could not load the tutorial video."),
                reply_markup=back_to_menu,
            )
            return
        video = settings_data.get("how_to_buy_video_url")
        if not video:
            await edit_text_message(
                callback.message,
                copy(language, "មិនទាន់មានវីដេអូណែនាំទេ។", "The tutorial video is not configured yet."),
                reply_markup=back_to_menu,
            )
            return
        caption = (
            settings_data.get(f"how_to_buy_caption_{language}") or settings_data.get("how_to_buy_caption", "")
        ).strip()[:1024]
        await callback.message.answer_video(
            video=video,
            caption=caption or None,
            reply_markup=back_to_menu,
        )
        await delete_replaced_message(callback.message)

    @router.callback_query(F.data == "shop")
    async def shop(callback: CallbackQuery) -> None:
        await callback.answer()
        language = await safe_user_language(store, callback.from_user.id)
        await show_catalogs(callback.message, 0, language)

    @router.callback_query(F.data.startswith("shop:catalogs:"))
    async def shop_catalog_page(callback: CallbackQuery) -> None:
        await callback.answer()
        language = await safe_user_language(store, callback.from_user.id)
        await show_catalogs(callback.message, int(callback.data.rsplit(":", 1)[1]), language)

    @router.callback_query(F.data.startswith("shop:packages:"))
    async def shop_package_page(callback: CallbackQuery) -> None:
        await callback.answer()
        language = await safe_user_language(store, callback.from_user.id)
        category_id, page = callback.data[len("shop:packages:") :].rsplit(":", 1)
        is_reseller = await store.is_reseller(callback.from_user.id)
        await show_packages(callback.message, category_id, int(page), language, is_reseller)

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
                copy(language, "ផលិតផលនេះមិនមានទៀតទេ។", "This product is no longer available."),
                reply_markup=keyboard([back_button("shop", language)]),
            )
            return
        is_reseller = await store.is_reseller(callback.from_user.id)
        await show_product(callback.message, product_data, 1, language, is_reseller=is_reseller)

    async def show_product(
        message: Message,
        product_data: dict[str, Any],
        quantity: int,
        language: str,
        edit_message: bool = True,
        is_reseller: bool = False,
    ) -> None:
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
                        callback_data=f"qty:{product_data['id']}:{quantity - 1}" if quantity > 1 else "noop",
                    ),
                    InlineKeyboardButton(
                        text=f"{copy(language, 'ចំនួន', 'Qty')}: {quantity}",
                        callback_data="noop",
                    ),
                    InlineKeyboardButton(
                        text="➕" if quantity < max_quantity else "·",
                        callback_data=f"qty:{product_data['id']}:{quantity + 1}" if quantity < max_quantity else "noop",
                    ),
                ]
            )
            total = unit_price * quantity
            rows.append(
                [
                    InlineKeyboardButton(
                        text=f"💳 {copy(language, 'បង់ប្រាក់', 'Pay')} • {money(total)}",
                        callback_data=f"b:{product_data['id']}:{quantity}",
                    )
                ]
            )
        else:
            rows.append([InlineKeyboardButton(text=copy(language, "អស់ស្តុក", "Out of stock"), callback_data="noop")])
        rows.append(back_button(f"c:{product_data['category_id']}", language))
        category_title = f"🛍 {html.escape(category_data['name'])}\n" if category_data else ""
        description = description_preview(product_data.get("description") or "", language)
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
        if media_url:
            if edit_message and (message.photo or message.video):
                media = (
                    InputMediaVideo(media=media_url, caption=text, parse_mode="HTML")
                    if video_url
                    else InputMediaPhoto(media=media_url, caption=text, parse_mode="HTML")
                )
                await message.edit_media(
                    media=media,
                    reply_markup=markup,
                )
            elif video_url:
                await message.answer_video(
                    video=video_url,
                    caption=text,
                    parse_mode="HTML",
                    reply_markup=markup,
                )
                if edit_message:
                    await delete_replaced_message(message)
            else:
                await message.answer_photo(
                    photo=image_url,
                    caption=text,
                    parse_mode="HTML",
                    reply_markup=markup,
                )
                if edit_message:
                    try:
                        await message.delete()
                    except TelegramBadRequest:
                        logger.debug("Could not delete previous product message %s", message.message_id)
        elif edit_message and (message.photo or message.video):
            await message.edit_caption(caption=text, parse_mode="HTML", reply_markup=markup)
        elif not edit_message:
            await message.answer(text, parse_mode="HTML", reply_markup=markup)
        else:
            await message.edit_text(text, parse_mode="HTML", reply_markup=markup)

    @router.callback_query(F.data.startswith("qty:"))
    async def change_quantity(callback: CallbackQuery) -> None:
        await callback.answer()
        language = await safe_user_language(store, callback.from_user.id)
        _, product_id, quantity_text = callback.data.split(":")
        product_data = await store.product(product_id)
        if not product_data:
            await callback.message.edit_text(
                copy(language, "ផលិតផលនេះមិនមានទៀតទេ។", "This product is no longer available."),
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
        await callback.answer(copy(language, "កំពុងបង្កើតការទូទាត់...", "Creating payment..."))
        _, product_id, quantity_text = callback.data.split(":")
        order = None
        try:
            product_data = await store.product(product_id)
            if not product_data:
                raise RuntimeError("Product is no longer available")
            order = await store.reserve_order(callback.from_user.id, product_id, int(quantity_text))
            payment = await payments.create_payment(
                str(order["total"]),
                str(order["id"]),
                callback.from_user.id,
            )
            aba_deeplink = aba_mobile_deeplink(payment["qr_string"])
            open_url = aba_mobile_redirect_url(settings.app_base_url or "", str(order["id"]))
            await store.set_payment(order["id"], payment["transaction_id"], aba_deeplink)
        except Exception:
            logger.exception("Could not create checkout for Telegram user %s", callback.from_user.id)
            if order:
                await store.release_order(str(order["id"]))
            await edit_text_message(
                callback.message,
                copy(language, "មិនអាចបង្កើតការទូទាត់បានទេ។ សូមព្យាយាមម្ដងទៀត។", "Could not create payment. Please try again."),
                reply_markup=menu(settings, callback.from_user.id, language),
            )
            return
        caption = payment_caption(
            str(product_data["name"]),
            int(quantity_text),
            order["total"],
            str(payment["transaction_id"]),
            language,
        )
        payment_message = await bot.send_photo(
            callback.from_user.id,
            payment_qr(payment),
            caption=caption,
            parse_mode="HTML",
            reply_markup=payment_keyboard(str(order["id"]), open_url, language),
        )
        await delete_replaced_message(callback.message)
        await track_payment_message(bot, store, order["id"], callback.from_user.id, payment_message.message_id)

    @router.callback_query(F.data.startswith("cancel:"))
    async def cancel_order(callback: CallbackQuery) -> None:
        await callback.answer()
        order_id = callback.data[7:]
        result = await (
            store.client.table("orders")
            .select("status,transaction_id")
            .eq("id", order_id)
            .eq("chat_id", callback.from_user.id)
            .maybe_single()
            .execute()
        )
        data = result.data
        language = await safe_user_language(store, callback.from_user.id)
        if not data:
            await callback.message.answer(copy(language, "រកមិនឃើញការបញ្ជាទិញទេ។", "Order not found."))
            return
        if data["status"] != "pending":
            await callback.message.answer(
                copy(language, "ការបញ្ជាទិញនេះមិនអាចលុបចោលបានទេ។", "This order can no longer be cancelled.")
            )
            return
        if data.get("transaction_id"):
            try:
                payment_status = (await payments.check_payment(data["transaction_id"])).get("status")
            except Exception:
                logger.exception("Could not verify payment before cancellation for order %s", order_id)
                await callback.message.answer(
                    copy(language, "មិនអាចផ្ទៀងផ្ទាត់ការទូទាត់បានទេ។ សូមសាកល្បងម្ដងទៀត។", "Could not verify payment. Please try again.")
                )
                return
            if payment_status == "paid":
                await deliver_order(bot, store, settings, order_id, callback.from_user.id)
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
        await callback.answer(copy(language, "កំពុងពិនិត្យការទូទាត់…", "Checking payment…"))
        order_id = callback.data.removeprefix("check_payment_")
        try:
            result = await (
                store.client.table("orders")
                .select("transaction_id,chat_id,status")
                .eq("id", order_id)
                .eq("chat_id", callback.from_user.id)
                .maybe_single()
                .execute()
            )
            data = result.data
            if not data:
                await callback.message.answer(copy(language, "រកមិនឃើញការបញ្ជាទិញទេ។", "Order not found."))
                return
            if data["status"] == "paid":
                await deliver_order(bot, store, settings, order_id, callback.from_user.id)
                return
            if data["status"] != "pending":
                await callback.message.answer(
                    copy(language, "ការបញ្ជាទិញនេះមិនសកម្មទៀតទេ។", "This order is no longer active.")
                )
                return
            payment = await payments.check_payment(data["transaction_id"])
            status = payment.get("status")
            if status == "paid":
                await deliver_order(bot, store, settings, order_id, callback.from_user.id)
            elif status in {"expired", "failed"}:
                await expire_payment(bot, store, order_id, callback.from_user.id, status)
            else:
                await callback.message.answer(
                    copy(language, "មិនទាន់ទទួលបានការទូទាត់ទេ។ សូមបញ្ចប់ការទូទាត់តាម KHPAY។", "Payment has not arrived yet. Please complete payment with KHPAY.")
                )
        except Exception:
            logger.exception("Payment check failed for order %s", order_id)
            await callback.message.answer(
                copy(language, "មិនអាចពិនិត្យការទូទាត់បានទេ។ សូមព្យាយាមម្ដងទៀតបន្តិចក្រោយ។", "Could not check payment. Please try again shortly.")
            )

    @router.callback_query(F.data == "history")
    async def history(callback: CallbackQuery) -> None:
        await callback.answer()
        language = await safe_user_language(store, callback.from_user.id)
        orders = await store.order_history(callback.from_user.id)
        if not orders:
            text = copy(language, "អ្នកមិនទាន់មានការបញ្ជាទិញទេ។", "You have no orders yet.")
        else:
            status_labels = {
                "pending": copy(language, "កំពុងរង់ចាំ", "Pending"),
                "paid": copy(language, "បានទូទាត់", "Paid"),
                "expired": copy(language, "ផុតកំណត់", "Expired"),
                "failed": copy(language, "បរាជ័យ", "Failed"),
                "cancelled": copy(language, "បានលុបចោល", "Cancelled"),
            }
            text = copy(language, "ការបញ្ជាទិញថ្មីៗ៖\n", "Recent orders:\n") + "\n".join(
                f"• {item['id'][:8]} · {money(item['total'])} · {status_labels.get(item['status'], item['status'])}"
                for item in orders
            )
        await callback.message.edit_text(text, reply_markup=keyboard([back_button(language=language)]))

    @router.callback_query(F.data == "support")
    async def support(callback: CallbackQuery) -> None:
        await callback.answer()
        language = await safe_user_language(store, callback.from_user.id)
        if settings.support_username:
            rows = [
                [
                    InlineKeyboardButton(
                        text=copy(language, "បើកការជជែកជាមួយជំនួយ", "Chat with support"),
                        url=f"https://t.me/{settings.support_username.lstrip('@')}",
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
                copy(language, "សូមទាក់ទងអ្នកគ្រប់គ្រងហាង។", "Please contact the store administrator."),
                reply_markup=keyboard([back_button(language=language)]),
            )

    @router.callback_query(F.data == "admin")
    async def admin(callback: CallbackQuery) -> None:
        await callback.answer()
        language = await safe_user_language(store, callback.from_user.id)
        if callback.from_user.id not in settings.admin_ids:
            await callback.message.edit_text(copy(language, "អ្នកមិនមានសិទ្ធិប្រើប្រាស់ទេ។", "You are not authorized to use this."), reply_markup=keyboard([back_button(language=language)]))
            return
        result = await store.client.table("products").select("id,name").eq("active", True).order("sort_order").execute()
        products = result.data or []
        stock_counts = await asyncio.gather(*(store.available_stock(item["id"]) for item in products))
        lines = [
            f"• {html.escape(item['name'])}: {stock} {copy(language, 'នៅសល់', 'available')}"
            for item, stock in zip(products, stock_counts)
        ]
        total_stock = sum(stock_counts)
        rows = [
            [InlineKeyboardButton(text=copy(language, "🗂 គ្រប់គ្រង Catalog និង Package", "🗂 Manage catalogs and packages"), callback_data="admin:catalogs")],
            [InlineKeyboardButton(text=copy(language, "👥 គ្រប់គ្រង Reseller", "👥 Manage resellers"), callback_data="admin:resellers")],
            [InlineKeyboardButton(text=copy(language, "📥 បញ្ចូលស្តុក (.txt)", "📥 Import stock (.txt)"), callback_data="admin:upload")],
            [InlineKeyboardButton(text=copy(language, "📊 ស្ថិតិការលក់", "📊 Sales summary"), callback_data="admin:sales")],
            back_button(language=language),
        ]
        await callback.message.edit_text(
            f"⚙️ <b>{copy(language, 'ផ្ទាំងគ្រប់គ្រងហាង', 'Store admin')}</b>\n"
            f"📦 <b>{copy(language, 'ស្តុកដែលអាចលក់បាន៖', 'Available stock:')} {total_stock}</b>\n"
            + ("\n".join(lines) or copy(language, "មិនមាន Package សកម្មទេ។", "No active packages.")),
            parse_mode="HTML",
            reply_markup=keyboard(rows),
        )

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
        await callback.message.edit_text(
            copy(
                language,
                "ផ្ញើ Telegram ID ដែលចង់ផ្តល់តម្លៃ Reseller៖",
                "Send the Telegram ID to grant reseller pricing:",
            ),
            reply_markup=keyboard([back_button("admin:cancel-input", language)]),
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
            await callback.answer(copy(language, "Telegram ID មិនត្រឹមត្រូវទេ។", "Invalid Telegram ID."), show_alert=True)
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
        result = await store.client.table("categories").select("id,name").eq("active", True).order("sort_order").execute()
        categories = result.data or []
        rows = [
            [InlineKeyboardButton(text=f"🛍 {item['name']}", callback_data=f"ac:{item['id']}")]
            for item in categories
        ]
        rows.extend(
            [
                [InlineKeyboardButton(text=copy(language, "➕ បន្ថែម Catalog", "➕ Add catalog"), callback_data="admin:category-add")],
                back_button("admin", language),
            ]
        )
        await callback.message.edit_text(
            f"🗂 <b>{copy(language, 'គ្រប់គ្រង Catalog', 'Manage catalogs')} ({len(categories)})</b>\n"
            + (
                copy(language, "ជ្រើសរើស Catalog ដើម្បីមើល Package និងសកម្មភាព៖", "Choose a catalog to view its packages and actions:")
                if categories
                else copy(language, "មិនទាន់មាន Catalog ទេ។ ចុចបន្ថែម Catalog ដើម្បីចាប់ផ្តើម។", "No catalogs yet. Add a catalog to get started.")
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
            await callback.message.edit_text(copy(language, "រកមិនឃើញ Catalog ទេ។", "Catalog not found."), reply_markup=keyboard([back_button("admin:catalogs", language)]))
            return
        result = await store.client.table("products").select("id,name").eq("category_id", category_id).eq("active", True).order("sort_order").execute()
        products = result.data or []
        stock_counts = await asyncio.gather(*(store.available_stock(item["id"]) for item in products))
        rows = [
            [
                InlineKeyboardButton(
                    text=f"📦 {item['name'][:40]} · {stock} {copy(language, 'នៅសល់', 'available')}",
                    callback_data=f"ap:{item['id']}",
                )
            ]
            for item, stock in zip(products, stock_counts)
        ]
        rows.extend(admin_category_actions(category_id, language))
        product_summary = (
            f"📦 {copy(language, 'Package សកម្ម៖', 'Active packages:')} {len(products)}"
            if products
            else copy(language, "មិនទាន់មាន Package ទេ។ ចុចបន្ថែម Package ដើម្បីចាប់ផ្តើម។", "No packages yet. Add a package to get started.")
        )
        await callback.message.edit_text(
            f"🗂 <b>{html.escape(category_data['name'])}</b>\n"
            f"{html.escape(category_data.get('description') or '')}\n\n{product_summary}",
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
            await callback.message.edit_text(copy(language, "រកមិនឃើញ Package ទេ។", "Package not found."), reply_markup=keyboard([back_button("admin:catalogs", language)]))
            return
        stock = await store.available_stock(product_data["id"])
        rows = admin_product_actions(product_data["id"], product_data["category_id"], language)
        await callback.message.edit_text(
            f"📦 <b>{html.escape(product_data['name'])}</b>\n"
            f"ID: <code>{html.escape(product_data['id'])}</code>\n"
            f"{copy(language, 'តម្លៃអតិថិជន៖', 'Customer price:')} {money(product_data['price'])}\n"
            f"{copy(language, 'តម្លៃ Reseller៖', 'Reseller price:')} {money(product_data['reseller_price'])}\n"
            f"{copy(language, 'ស្តុកនៅសល់៖', 'Stock remaining:')} {stock}\n\n"
            f"{html.escape(product_data.get('description') or '')}",
            parse_mode="HTML",
            reply_markup=keyboard(rows),
        )

    @router.message(F.photo | F.video, F.caption.startswith(("setphoto", "setvideo", "setmedia")))
    async def set_product_media(message: Message) -> None:
        if not message.from_user or message.from_user.id not in settings.admin_ids:
            return
        language = await safe_user_language(store, message.from_user.id)
        parts = (message.caption or "").strip().split(maxsplit=1)
        if not parts or parts[0] not in {"setphoto", "setvideo", "setmedia"} or len(parts) != 2 or not parts[1].strip():
            await message.answer(copy(language, "ទម្រង់មិនត្រឹមត្រូវ។ សូមប្រើ setmedia <product_id> ជាមួយរូបភាព ឬវីដេអូ។", "Invalid format. Use setmedia <product_id> with an image or video."))
            return
        product_id = parts[1].strip()
        is_video = message.video is not None
        try:
            file_id = message.video.file_id if is_video else message.photo[-1].file_id
            media_url = await upload_product_media(bot, settings, file_id, "video" if is_video else "image")
            update_media = store.update_product_video if is_video else store.update_product_image
            updated = await update_media(product_id, media_url)
        except Exception:
            logger.exception("Could not update product media %s", product_id)
            await message.answer(copy(language, "មិនអាចធ្វើបច្ចុប្បន្នភាពរូបភាព/វីដេអូ Package បានទេ។", "Could not update the package image/video."))
            return
        if not updated:
            await message.answer(copy(language, f"រកមិនឃើញ Package ID {html.escape(product_id)} ទេ។", f"Package ID {html.escape(product_id)} not found."))
            return
        await message.answer(
            copy(language, f"✅ បាន Upload {'វីដេអូ' if is_video else 'រូបភាព'} ទៅ Cloudinary និងបច្ចុប្បន្នភាព Package ID <code>{html.escape(product_id)}</code> ជោគជ័យ!", f"✅ {'Video' if is_video else 'Image'} uploaded to Cloudinary and package ID <code>{html.escape(product_id)}</code> updated."),
            parse_mode="HTML",
        )

    @router.message(F.photo | F.video)
    async def receive_wizard_media(message: Message) -> None:
        if not message.from_user or message.from_user.id not in settings.admin_ids:
            return
        wizard = admin_wizards.get(message.from_user.id)
        if not wizard:
            return
        language = await safe_user_language(store, message.from_user.id)
        if wizard.get("step") != "media":
            await show_admin_wizard(message, message.from_user.id, language, edit_message=False)
            return
        is_video = message.video is not None
        media = message.video if is_video else message.photo[-1]
        wizard["values"]["media_file_id"] = media.file_id
        wizard["values"]["media_type"] = "video" if is_video else "image"
        steps = ADMIN_WIZARD_STEPS[wizard["kind"]]
        wizard["step"] = steps[1]
        await show_admin_wizard(message, message.from_user.id, language, edit_message=False)

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
            "values": {"name": category["name"], "description": category.get("description") or ""},
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
                "reseller_price": str(product.get("reseller_price") or product["price"]),
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
            [InlineKeyboardButton(text=copy(language, "✅ បិទ Catalog", "✅ Disable catalog"), callback_data=f"adc:{category_id}")],
            back_button(f"ac:{category_id}", language),
        ]
        await callback.message.edit_text(copy(language, "បិទ Catalog នេះ និងផលិតផលសកម្មរបស់វា?", "Disable this catalog and all its active packages?"), reply_markup=keyboard(rows))

    @router.callback_query(F.data.startswith("adc:"))
    async def disable_category(callback: CallbackQuery) -> None:
        await callback.answer()
        language = await safe_user_language(store, callback.from_user.id)
        if callback.from_user.id not in settings.admin_ids:
            return
        category_id = callback.data[4:]
        await store.client.table("categories").update({"active": False}).eq("id", category_id).execute()
        await store.client.table("products").update({"active": False}).eq("category_id", category_id).execute()
        await callback.message.edit_text(copy(language, "បានបិទ Catalog និងផលិតផលរបស់វា។", "Catalog and its packages disabled."), reply_markup=keyboard([back_button("admin:catalogs", language)]))

    @router.callback_query(F.data.startswith("admin:product-disable:"))
    async def confirm_product_disable(callback: CallbackQuery) -> None:
        await callback.answer()
        language = await safe_user_language(store, callback.from_user.id)
        if callback.from_user.id not in settings.admin_ids:
            return
        product_id = callback.data.removeprefix("admin:product-disable:")
        rows = [
            [InlineKeyboardButton(text=copy(language, "✅ បិទ Package", "✅ Disable package"), callback_data=f"adp:{product_id}")],
            back_button(f"ap:{product_id}", language),
        ]
        await callback.message.edit_text(copy(language, "បិទ Package នេះ?", "Disable this package?"), reply_markup=keyboard(rows))

    @router.callback_query(F.data.startswith("adp:"))
    async def disable_product(callback: CallbackQuery) -> None:
        await callback.answer()
        language = await safe_user_language(store, callback.from_user.id)
        if callback.from_user.id not in settings.admin_ids:
            return
        product_id = callback.data[4:]
        await store.client.table("products").update({"active": False}).eq("id", product_id).execute()
        await callback.message.edit_text(copy(language, "បានបិទ Package។", "Package disabled."), reply_markup=keyboard([back_button("admin:catalogs", language)]))

    @router.callback_query(F.data.startswith("admin:wizard-skip:"))
    async def skip_admin_wizard_step(callback: CallbackQuery) -> None:
        language = await safe_user_language(store, callback.from_user.id)
        if callback.from_user.id not in settings.admin_ids:
            await callback.answer()
            return
        wizard = admin_wizards.get(callback.from_user.id)
        if not wizard:
            await callback.answer(copy(language, "Wizard មិនសកម្មទេ។", "No active wizard."), show_alert=True)
            return
        step = wizard.get("step")
        requested_step = callback.data.removeprefix("admin:wizard-skip:")
        if step != requested_step:
            await callback.answer(copy(language, "ជំហាននេះលែងសកម្មហើយ។", "This step is no longer active."), show_alert=True)
            return
        if not step or (step not in {"media", "description", "reseller_price"} and not wizard["edit"]):
            await callback.answer(copy(language, "ជំហាននេះមិនអាចរំលងបានទេ។", "This step cannot be skipped."), show_alert=True)
            return
        if step == "reseller_price" and wizard.get("must_set_reseller_price"):
            await callback.answer(
                copy(language, "សូមបញ្ចូលតម្លៃ Reseller ថ្មី ព្រោះតម្លៃទូទៅត្រូវបានបន្ថយ។", "Enter a new reseller price because the customer price was reduced."),
                show_alert=True,
            )
            return
        if step == "reseller_price" and not wizard["edit"]:
            wizard["values"]["reseller_price"] = wizard["values"].get("price")
        steps = ADMIN_WIZARD_STEPS[wizard["kind"]]
        current_index = steps.index(step)
        wizard["step"] = steps[current_index + 1] if current_index + 1 < len(steps) else None
        await callback.answer()
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
            await callback.answer(copy(language, "Wizard មិនទាន់រួចរាល់សម្រាប់រក្សាទុកទេ។", "The wizard is not ready to save."), show_alert=True)
            return
        try:
            target = await finish_admin_wizard(callback.from_user.id, language)
        except ValueError as exc:
            await callback.answer(str(exc), show_alert=True)
            return
        except Exception:
            logger.exception("Admin wizard save failed for user %s", callback.from_user.id)
            await callback.answer(
                copy(language, "រក្សាទុកមិនបានទេ។ សូមពិនិត្យ Cloudinary/Supabase រួចព្យាយាមម្ដងទៀត។", "Could not save. Check Cloudinary/Supabase and try again."),
                show_alert=True,
            )
            return
        admin_wizards.pop(callback.from_user.id, None)
        await callback.answer(copy(language, "បានរក្សាទុកដោយជោគជ័យ។", "Saved successfully."))
        await callback.message.edit_text(
            copy(language, "✅ បានរក្សាទុក Package/Catalog ដោយជោគជ័យ។", "✅ Package/catalog saved successfully."),
            reply_markup=keyboard([back_button(target, language)]),
        )

    @router.callback_query(F.data == "admin:cancel-input")
    async def cancel_admin_input(callback: CallbackQuery) -> None:
        language = await safe_user_language(store, callback.from_user.id)
        pending = pending_admin_input.pop(callback.from_user.id, None)
        await callback.answer(copy(language, "បានបោះបង់", "Cancelled"))
        target = (
            f"ap:{pending[1]}"
            if pending and pending[0] in {"stock_add", "stock_remove"}
            else "admin:resellers"
            if pending and pending[0] == "reseller_add"
            else "admin:catalogs"
        )
        await callback.message.edit_text(copy(language, "បានបោះបង់។", "Cancelled."), reply_markup=keyboard([back_button(target, language)]))

    @router.callback_query(F.data.startswith("admin:stock-add:"))
    async def add_stock_prompt(callback: CallbackQuery) -> None:
        await callback.answer()
        language = await safe_user_language(store, callback.from_user.id)
        if callback.from_user.id not in settings.admin_ids:
            return
        product_id = callback.data.removeprefix("admin:stock-add:")
        pending_admin_input[callback.from_user.id] = ("stock_add", product_id)
        await callback.message.answer(
            copy(language, "ផ្ញើ License Key មួយក្នុងមួយបន្ទាត់៖", "Send one license key per line:"),
            reply_markup=keyboard([back_button("admin:cancel-input", language)]),
        )

    @router.callback_query(F.data.startswith("admin:stock-remove:"))
    async def remove_stock_prompt(callback: CallbackQuery) -> None:
        await callback.answer()
        language = await safe_user_language(store, callback.from_user.id)
        if callback.from_user.id not in settings.admin_ids:
            return
        product_id = callback.data.removeprefix("admin:stock-remove:")
        pending_admin_input[callback.from_user.id] = ("stock_remove", product_id)
        await callback.message.answer(
            copy(language, "ផ្ញើ License Key ដែលចង់ដក មួយក្នុងមួយបន្ទាត់។ ដកបានតែ Key ដែលមិនទាន់កក់ ឬលក់៖", "Send the exact license keys to remove, one per line. Only keys that are neither reserved nor sold can be removed."),
            reply_markup=keyboard([back_button("admin:cancel-input", language)]),
        )

    @router.message(F.text)
    async def receive_admin_input(message: Message) -> None:
        if not message.from_user:
            return
        text = (message.text or "").strip()
        language = await safe_user_language(store, message.from_user.id)
        if message.from_user.id not in settings.admin_ids:
            if text.casefold() in {"🛍 buy now", "buy now"}:
                await show_catalogs(message, 0, language, edit_message=False)
            else:
                await send_welcome(message, store, settings)
            return
        wizard = admin_wizards.get(message.from_user.id)
        if wizard:
            step = wizard.get("step")
            values = wizard["values"]
            try:
                if step == "media":
                    raise ValueError(copy(language, "សូមផ្ញើរូបភាព/វីដេអូ ឬចុចរំលង។", "Send a photo/video or use Skip."))
                if step == "name":
                    if not text:
                        raise ValueError(copy(language, "ឈ្មោះមិនអាចទទេបានទេ។", "Name cannot be empty."))
                    values["name"] = text
                elif step == "description":
                    values["description"] = text
                elif step == "price":
                    try:
                        price = Decimal(text)
                    except InvalidOperation:
                        raise ValueError(copy(language, "តម្លៃមិនត្រឹមត្រូវទេ។", "Invalid price.")) from None
                    if not price.is_finite() or price <= 0:
                        raise ValueError(copy(language, "តម្លៃត្រូវតែធំជាង 0។", "Price must be greater than 0."))
                    values["price"] = str(price)
                    if wizard["edit"] and Decimal(str(values.get("reseller_price", "0"))) >= price:
                        wizard["must_set_reseller_price"] = True
                elif step == "reseller_price":
                    try:
                        reseller_price = Decimal(text)
                        customer_price = Decimal(str(values["price"]))
                    except (InvalidOperation, KeyError):
                        raise ValueError(copy(language, "សូមបញ្ចូលតម្លៃ Reseller ត្រឹមត្រូវ។", "Enter a valid reseller price.")) from None
                    if not reseller_price.is_finite() or reseller_price <= 0 or reseller_price >= customer_price:
                        raise ValueError(
                            copy(
                                language,
                                "តម្លៃ Reseller ត្រូវតែធំជាង 0 និងទាបជាងតម្លៃទូទៅ។",
                                "Reseller price must be greater than 0 and lower than the customer price.",
                            )
                        )
                    values["reseller_price"] = str(reseller_price)
                    wizard.pop("must_set_reseller_price", None)
                else:
                    raise ValueError(copy(language, "Wizard មិនសកម្មទេ។", "No active wizard step."))
                steps = ADMIN_WIZARD_STEPS[wizard["kind"]]
                current_index = steps.index(step)
                wizard["step"] = steps[current_index + 1] if current_index + 1 < len(steps) else None
            except ValueError as exc:
                await message.answer(
                    html.escape(str(exc)),
                    reply_markup=keyboard(
                        [[InlineKeyboardButton(text=copy(language, "❌ បោះបង់", "❌ Cancel"), callback_data="admin:wizard-cancel")]]
                    ),
                )
                return
            await show_admin_wizard(message, message.from_user.id, language, edit_message=False)
            return
        if text.casefold() in {"🛍 buy now", "buy now"}:
            await show_catalogs(message, 0, language, edit_message=False)
            return
        try:
            stock_add = parse_stock_add(text, language)
            if stock_add:
                product_id, credentials = stock_add
                added = await store.import_stock(product_id, credentials, settings.stock_encryption_key)
                await notify_stock_added(bot, store, settings.stock_notification_chat_ids, product_id, added)
                await message.answer(copy(language, f"បានបញ្ចូលស្តុកថ្មីចំនួន {added}។", f"Added {added} new stock item(s)."))
                return
        except ValueError as exc:
            await message.answer(html.escape(str(exc)))
            return
        except Exception:
            logger.exception("Direct stock import failed for admin %s", message.from_user.id)
            await message.answer(copy(language, "មិនអាចបញ្ចូលស្តុកបានទេ។ សូមពិនិត្យ Product ID រួចព្យាយាមម្ដងទៀត។", "Could not add stock. Check the product ID and try again."))
            return
        pending = pending_admin_input.get(message.from_user.id)
        if not pending:
            if text.casefold() in {"⚙️ admin", "admin"}:
                await message.answer(
                    copy(language, "⚙️ បើកផ្ទាំងគ្រប់គ្រងហាង៖", "⚙️ Open store administration:"),
                    reply_markup=keyboard(
                        [[InlineKeyboardButton(text=copy(language, "⚙️ គ្រប់គ្រងហាង", "⚙️ Store admin"), callback_data="admin")]]
                    ),
                )
            else:
                await send_welcome(message, store, settings)
            return
        action, target_id = pending
        try:
            if action == "reseller_add":
                if not text.isdecimal():
                    raise ValueError(copy(language, "សូមបញ្ចូល Telegram ID ជាលេខវិជ្ជមាន។", "Enter a positive numeric Telegram ID."))
                await store.add_reseller(int(text))
            elif action == "stock_add":
                credentials = [line.strip() for line in text.splitlines() if line.strip()]
                if not credentials:
                    raise ValueError(copy(language, "សូមបញ្ចូល License Key យ៉ាងហោចណាស់មួយ។", "Enter at least one license key."))
                added = await store.import_stock(target_id, credentials, settings.stock_encryption_key)
                await notify_stock_added(bot, store, settings.stock_notification_chat_ids, target_id, added)
            elif action == "stock_remove":
                credentials = [line.strip() for line in text.splitlines() if line.strip()]
                if not credentials:
                    raise ValueError(copy(language, "សូមបញ្ចូល License Key យ៉ាងហោចណាស់មួយ។", "Enter at least one license key."))
                removed = await store.remove_stock(target_id, credentials, settings.stock_encryption_key)
            else:
                return
        except (InvalidOperation, ValueError) as exc:
            await message.answer(
                copy(language, f"ព័ត៌មានមិនត្រឹមត្រូវ៖ {html.escape(str(exc))}", f"Invalid input: {html.escape(str(exc))}"),
                reply_markup=keyboard([back_button("admin:cancel-input", language)]),
            )
            return
        except Exception:
            logger.exception("Admin catalog update failed for user %s", message.from_user.id)
            await message.answer(copy(language, "មិនអាចរក្សាទុកបានទេ។ សូមព្យាយាមម្ដងទៀត។", "Could not save the changes. Please try again."))
            return
        pending_admin_input.pop(message.from_user.id, None)
        if action in {"stock_add", "stock_remove"}:
            if action == "stock_add":
                result_message = copy(language, f"បានបញ្ចូលស្តុកថ្មីចំនួន {added}។", f"Added {added} new stock item(s).")
            else:
                result_message = copy(language, f"បានដកស្តុកដែលមិនទាន់លក់ចំនួន {removed}។", f"Removed {removed} unsold stock item(s).")
            await message.answer(
                result_message,
                reply_markup=keyboard([back_button(f"ap:{target_id}", language)]),
            )
            return
        await message.answer(
            copy(language, "បានផ្តល់សិទ្ធិ Reseller ដោយជោគជ័យ។", "Reseller pricing enabled successfully."),
            reply_markup=keyboard([back_button("admin:resellers", language)]),
        )

    @router.callback_query(F.data == "admin:upload")
    async def choose_stock_product(callback: CallbackQuery) -> None:
        await callback.answer()
        language = await safe_user_language(store, callback.from_user.id)
        if callback.from_user.id not in settings.admin_ids:
            return
        result = await store.client.table("products").select("id,name").eq("active", True).order("sort_order").execute()
        rows = [[InlineKeyboardButton(text=item["name"], callback_data=f"au:{item['id']}")] for item in result.data]
        rows.append(back_button("admin", language))
        await callback.message.edit_text(copy(language, "ជ្រើសរើសផលិតផលសម្រាប់បញ្ចូលស្តុក៖", "Choose a product for the stock import:"), reply_markup=keyboard(rows))

    @router.callback_query(F.data.startswith("au:"))
    async def request_stock_file(callback: CallbackQuery) -> None:
        await callback.answer()
        language = await safe_user_language(store, callback.from_user.id)
        if callback.from_user.id not in settings.admin_ids:
            return
        pending_stock_upload[callback.from_user.id] = callback.data[3:]
        await callback.message.answer(
            copy(language, "សូមផ្ញើឯកសារ .txt ដែលមាន License Key មួយក្នុងមួយជួរ។", "Send a .txt file with one license key per line."),
            reply_markup=keyboard([back_button("admin:cancel-upload", language)]),
        )

    @router.callback_query(F.data == "admin:cancel-upload")
    async def cancel_stock_file(callback: CallbackQuery) -> None:
        pending_stock_upload.pop(callback.from_user.id, None)
        language = await safe_user_language(store, callback.from_user.id)
        await callback.answer(copy(language, "បានបោះបង់", "Cancelled"))
        await callback.message.edit_text(copy(language, "បានបោះបង់ការបញ្ចូលស្តុក។", "Stock import cancelled."), reply_markup=keyboard([back_button("admin", language)]))

    @router.message(F.document)
    async def import_stock_file(message: Message) -> None:
        if not message.from_user or message.from_user.id not in settings.admin_ids:
            return
        language = await safe_user_language(store, message.from_user.id)
        product_id = pending_stock_upload.get(message.from_user.id)
        if not product_id:
            return
        document = message.document
        if not document.file_name or not document.file_name.lower().endswith(".txt") or (document.file_size or 0) > 1_000_000:
            await message.answer(copy(language, "សូមប្រើឯកសារ .txt ទំហំមិនលើស 1 MB។", "Use a .txt file no larger than 1 MB."), reply_markup=keyboard([back_button("admin:cancel-upload", language)]))
            return
        try:
            buffer = io.BytesIO()
            await bot.download(document.file_id, destination=buffer)
            credentials = [line.strip() for line in buffer.getvalue().decode("utf-8").splitlines() if line.strip()]
            if not credentials:
                raise ValueError(copy(language, "ឯកសារត្រូវមាន License Key យ៉ាងហោចណាស់មួយ។", "The file must contain at least one license key."))
            added = await store.import_stock(product_id, credentials, settings.stock_encryption_key)
        except (UnicodeDecodeError, ValueError) as exc:
            await message.answer(copy(language, f"ឯកសារមិនត្រឹមត្រូវ៖ {html.escape(str(exc))}", f"Invalid file: {html.escape(str(exc))}"), reply_markup=keyboard([back_button("admin:cancel-upload", language)]))
            return
        except Exception:
            logger.exception("Stock import failed for product %s", product_id)
            await message.answer(copy(language, "មិនអាចបញ្ចូលស្តុកបានទេ។ សូមព្យាយាមម្ដងទៀត។", "Could not import stock. Please try again."))
            return
        pending_stock_upload.pop(message.from_user.id, None)
        await notify_stock_added(bot, store, settings.stock_notification_chat_ids, product_id, added)
        await message.answer(copy(language, f"បានបញ្ចូលស្តុកថ្មីចំនួន {added}។", f"Added {added} new stock item(s)."), reply_markup=keyboard([back_button("admin", language)]))

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
        await callback.message.edit_text(text, reply_markup=keyboard([back_button("admin", language)]))

    dispatcher.include_router(router)


async def reconcile_payments(bot: Bot, store: Store, payments: KHPayClient, settings: Settings) -> None:
    while True:
        try:
            for order in await store.pending_orders():
                try:
                    payment = await payments.check_payment(order["transaction_id"])
                    status = payment.get("status")
                    if status == "paid":
                        await deliver_order(bot, store, settings, order["id"], int(order["chat_id"]))
                    elif status in {"expired", "failed"}:
                        await expire_payment(bot, store, order["id"], int(order["chat_id"]), status)
                except Exception:
                    logger.exception("Reconciliation failed for order %s", order["id"])
            for order in await store.undelivered_orders():
                try:
                    await deliver_order(bot, store, settings, order["id"], int(order["chat_id"]))
                except Exception:
                    logger.exception("Delivery retry failed for order %s", order["id"])
        except Exception:
            logger.exception("Could not load pending orders")
        await asyncio.sleep(settings.poll_interval_seconds)
