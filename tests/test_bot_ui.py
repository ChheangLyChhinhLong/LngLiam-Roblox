import asyncio
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

from cryptography.fernet import Fernet
import pytest

from app.bot import (
    admin_category_actions,
    admin_product_actions,
    account_text,
    broadcast_destinations,
    broadcast_draft_keyboard,
    copy_broadcast_content,
    bot_setting_section,
    copy,
    complete_wallet_deposit,
    delete_replaced_message,
    deliver_order,
    description_preview,
    execute_broadcast,
    edit_text_message,
    expire_payment,
    is_wallet_schema_missing,
    menu,
    normalize_language,
    order_history_keyboard,
    order_history_text,
    parse_broadcast_buttons,
    parse_wallet_deposit_amount,
    notify_order_success,
    notify_stock_added,
    package_id_from_start,
    paginate,
    pagination_row,
    payment_caption,
    payment_keyboard,
    parse_product_caption,
    parse_stock_add,
    product_price,
    quantity_limit,
    safe_user_language,
    send_replacing_screen,
    send_welcome,
    stock_notification_url,
    track_payment_message,
    update_active_screen,
    upload_product_image,
    upload_product_video,
    welcome_keyboard,
    welcome_text,
    wallet_error_message,
    wallet_deposit_keyboard,
)
import app.bot as bot_module
from app.security import decrypt_stock, encrypt_stock, stock_fingerprint
from app.store import Store


def test_welcome_text_escapes_profile_data_and_handles_missing_username():
    user = SimpleNamespace(id=12345, first_name="<Jing>", username=None)

    text = welcome_text(user)

    assert "&lt;Jing&gt;" in text
    assert "LngLiam Roblox" in text
    assert "ID: 12345" in text
    assert "└ ឈ្មោះ: &lt;Jing&gt;" in text
    assert "ទិញ Robux និងសេវាកម្ម Roblox" in text
    assert "Account" not in text


def test_english_welcome_promotes_the_shop_and_account_screen_is_separate():
    user = SimpleNamespace(id=12345, first_name="Jing", last_name="Long", username="jing")

    text = welcome_text(user, "en")
    account = account_text(user, "2.50", "en", is_reseller=True)

    assert "Welcome to LngLiam Roblox!" in text
    assert "ID: 12345" in text
    assert "Name: Jing Long" in text
    assert "quickly, easily, and securely" in text
    assert "Choose a service from the menu below" in text
    assert "Account" in account
    assert "Full name: Jing Long" in account
    assert "Username: @jing" in account
    assert "Account type: Reseller" in account
    assert "<code>12345</code>" in account
    assert "$2.50" in account


def test_account_text_handles_missing_username_and_last_name():
    user = SimpleNamespace(id=12345, first_name="Jing", username=None)

    account = account_text(user, "0.00", "en")

    assert "Full name: Jing" in account
    assert "Username: Not set" in account
    assert "Account type: Customer" in account
    assert "Balance: <b>$0.00</b>" in account


def test_order_history_shows_empty_message_or_order_details():
    assert order_history_text([], "km") == "អ្នកមិនទាន់មានការបញ្ជាទិញទេ។"
    assert order_history_text([], "en") == "You have no orders yet."

    text = order_history_text(
        [
            {
                "id": "order-123456",
                "product_name": "Robux <Gift>",
                "quantity": 2,
                "total": "5.00",
                "status": "paid",
                "transaction_id": "txn-456",
                "created_at": "2026-10-20T09:35:00+07:00",
            }
        ],
        "en",
    )

    assert "2026-10-20 02:35 UTC" in text
    assert "Robux &lt;Gift&gt; ×2" in text
    assert "$5.00" in text
    assert "txn-456" in text
    assert "Paid" in text


def test_order_history_keyboard_has_previous_and_next_pagination_buttons():
    markup = order_history_keyboard(1, True, "en")

    assert [button.text for button in markup.inline_keyboard[0]] == ["⏮️ Prev", "Next ⏭️"]
    assert [button.callback_data for button in markup.inline_keyboard[0]] == [
        "history:page:0",
        "history:page:2",
    ]

    first_page = order_history_keyboard(0, False, "en")
    assert len(first_page.inline_keyboard) == 1
    assert first_page.inline_keyboard[0][0].callback_data == "home"


def test_broadcast_button_parser_accepts_multiple_http_links_and_rejects_invalid_urls():
    buttons = parse_broadcast_buttons(
        "Shop | https://example.com/shop\nSupport | https://t.me/example"
    )

    assert buttons == [
        {"text": "Shop", "url": "https://example.com/shop"},
        {"text": "Support", "url": "https://t.me/example"},
    ]
    assert parse_broadcast_buttons("-") == []
    with pytest.raises(ValueError):
        parse_broadcast_buttons("Bad | javascript:alert(1)")
    with pytest.raises(ValueError):
        parse_broadcast_buttons("\n".join(f"Link {index} | https://example.com/{index}" for index in range(9)))


def test_broadcast_preview_keyboard_contains_send_now_and_schedule_actions():
    markup = broadcast_draft_keyboard("preview", "en")

    buttons = [button for row in markup.inline_keyboard for button in row]
    assert buttons[0].text == "📣 Send now"
    assert buttons[0].callback_data == "admin:broadcast:send"
    assert buttons[1].text == "🕒 Schedule"
    assert buttons[1].callback_data == "admin:broadcast:schedule"


def test_broadcast_destinations_deduplicates_users_and_notification_chats():
    async def run_test():
        store = SimpleNamespace(
            stock_notification_users=AsyncMock(
                return_value=[
                    {"telegram_id": 123, "language": "km"},
                    {"telegram_id": 456, "language": "en"},
                ]
            ),
            bot_settings=AsyncMock(
                return_value={
                    "stock_notification_chat_ids": "123,-1001,@shop",
                    "order_notification_chat_ids": "-1001,@orders",
                }
            ),
        )

        settings = SimpleNamespace(
            stock_notification_chat_ids="-1001,@legacy",
            order_notification_chat_ids="@orders",
        )
        assert await broadcast_destinations(store, settings) == [
            123,
            456,
            -1001,
            "@shop",
            "@legacy",
            "@orders",
        ]

    asyncio.run(run_test())


def test_copy_broadcast_content_preserves_albums_and_adds_url_buttons():
    async def run_test():
        bot = SimpleNamespace(
            copy_messages=AsyncMock(
                return_value=[
                    SimpleNamespace(message_id=101),
                    SimpleNamespace(message_id=102),
                    SimpleNamespace(message_id=103),
                    SimpleNamespace(message_id=104),
                ]
            ),
            send_message=AsyncMock(return_value=SimpleNamespace(message_id=105)),
        )
        content = [
            {"message_id": 13, "media_group_id": None},
            {"message_id": 12, "media_group_id": "album"},
            {"message_id": 11, "media_group_id": "album"},
            {"message_id": 10, "media_group_id": None},
        ]
        buttons = [{"text": "Shop", "url": "https://example.com"}]

        copied = await copy_broadcast_content(bot, 123, 999, content, buttons)

        assert copied == [101, 102, 103, 104, 105]
        bot.copy_messages.assert_awaited_once_with(
            chat_id=123,
            from_chat_id=999,
            message_ids=[10, 11, 12, 13],
        )
        assert bot.send_message.await_args.kwargs["reply_markup"].inline_keyboard[0][0].url == "https://example.com"

    asyncio.run(run_test())


def test_execute_broadcast_reports_success_and_respects_cancel_request():
    async def run_test():
        bot = SimpleNamespace(
            copy_messages=AsyncMock(return_value=[SimpleNamespace(message_id=20)]),
            send_message=AsyncMock(return_value=SimpleNamespace(message_id=21)),
        )
        store = SimpleNamespace(
            stock_notification_users=AsyncMock(
                return_value=[{"telegram_id": 123, "language": "km"}]
            ),
            bot_settings=AsyncMock(return_value={}),
        )
        progress = SimpleNamespace(edit_text=AsyncMock())
        event = asyncio.Event()

        result = await execute_broadcast(
            bot,
            store,
            999,
            [{"message_id": 10, "media_group_id": None}],
            [],
            progress,
            event,
        )

        assert result == (1, 0, False)
        bot.copy_messages.assert_awaited_once_with(
            chat_id=123,
            from_chat_id=999,
            message_ids=[10],
        )
        assert "Sent / បានផ្ញើ: 1" in progress.edit_text.await_args.args[0]

        event.set()
        cancelled_result = await execute_broadcast(
            bot,
            store,
            999,
            [{"message_id": 10, "media_group_id": None}],
            [],
            progress,
            event,
        )
        assert cancelled_result == (0, 0, True)

    asyncio.run(run_test())


def test_language_helpers_normalize_supported_locale_variants():
    assert normalize_language("en-US") == "en"
    assert normalize_language("km-KH") == "km"
    assert normalize_language("fr") == "km"
    assert copy("en-US", "សូមស្វាគមន៍", "Welcome") == "Welcome"
    assert copy("km-KH", "សូមស្វាគមន៍", "Welcome") == "សូមស្វាគមន៍"

    async def run_test():
        store = SimpleNamespace(user_language=AsyncMock(return_value="en-US"))

        assert await safe_user_language(store, 42) == "en"

    asyncio.run(run_test())


def test_bot_setting_section_resolves_all_settings_to_their_screen():
    assert bot_setting_section("welcome_photo_url") == "media"
    assert bot_setting_section("welcome_video_url") == "media"
    assert bot_setting_section("how_to_buy_video_url") == "media"
    assert bot_setting_section("how_to_buy_caption") == "captions"
    assert bot_setting_section("how_to_buy_caption_km") == "captions"
    assert bot_setting_section("how_to_buy_caption_en") == "captions"
    assert "channel_install_link" not in bot_module.BOT_SETTING_LABELS
    assert bot_setting_section("support_username") == "shop"
    assert bot_setting_section("stock_notification_chat_ids") == "notifications"
    assert bot_setting_section("order_notification_chat_ids") == "notifications"


def test_welcome_sends_the_new_shop_keyboard_and_saves_profile():
    async def run_test():
        bot_module.welcome_message_ids.clear()
        bot_module.menu_keyboard_messages.clear()
        bot_module.active_screen_messages.clear()
        user = SimpleNamespace(id=12345, first_name="Jing", username="jing")
        sent_message = SimpleNamespace(message_id=789)
        message = SimpleNamespace(from_user=user, answer=AsyncMock(return_value=sent_message), bot=SimpleNamespace())
        store = SimpleNamespace(
            user_language=AsyncMock(return_value="km"),
            upsert_user=AsyncMock(),
            categories=AsyncMock(return_value=[{"name": "Catalog", "description": ""}]),
        )
        settings = SimpleNamespace(admin_ids=set())

        await send_welcome(message, store, settings)

        store.upsert_user.assert_awaited_once_with(12345, "jing", "Jing", "km")
        message.answer.assert_awaited_once()
        sent_text = message.answer.call_args.args[0]
        sent_markup = message.answer.call_args.kwargs["reply_markup"]
        assert "សូមស្វាគមន៍មកកាន់ LngLiam Roblox!" in sent_text
        assert "ID: 12345" in sent_text
        assert "ឈ្មោះ: Jing" in sent_text
        assert [button.text for button in sent_markup.keyboard[0]] == ["👤 Account", "💰 Add funds"]
        assert sent_markup.keyboard[1][0].text == "🎮 Games Service"
        assert [button.text for button in sent_markup.keyboard[2]] == ["🛒 My Orders", "🎥 How to Buy"]
        assert all("Roblox" not in button.text for row in sent_markup.keyboard for button in row)
        assert isinstance(sent_markup, bot_module.ReplyKeyboardMarkup)
        assert bot_module.welcome_message_ids[user.id] == sent_message.message_id
        assert bot_module.menu_keyboard_messages[user.id] is sent_message

    asyncio.run(run_test())


def test_welcome_uses_configured_photo_preview():
    async def run_test():
        bot_module.welcome_message_ids.pop(12345, None)
        user = SimpleNamespace(id=12345, first_name="Jing", username="jing")
        sent_message = SimpleNamespace(message_id=790)
        message = SimpleNamespace(
            from_user=user,
            answer=AsyncMock(),
            answer_photo=AsyncMock(return_value=sent_message),
            bot=SimpleNamespace(),
        )
        store = SimpleNamespace(
            user_language=AsyncMock(return_value="km"),
            upsert_user=AsyncMock(),
            categories=AsyncMock(return_value=[]),
            bot_settings=AsyncMock(return_value={"welcome_photo_url": "https://images.example/welcome.png"}),
        )

        await send_welcome(message, store, SimpleNamespace(admin_ids=set()))

        message.answer_photo.assert_awaited_once()
        assert message.answer_photo.await_args.kwargs["photo"] == "https://images.example/welcome.png"
        assert message.answer_photo.await_args.kwargs["reply_markup"].keyboard[0][0].text == "👤 Account"
        message.answer.assert_not_awaited()

    asyncio.run(run_test())


def test_welcome_keeps_categories_out_of_the_main_promo_copy():
    user = SimpleNamespace(id=12345, first_name="Jing", username="jing")

    text = welcome_text(
        user,
        categories=[
            {"name": "Gift Cards", "description": "Instant delivery"},
            {"name": "VPN & Tools", "description": "Secure access"},
        ],
    )

    assert "Gift Cards" not in text
    assert "VPN" not in text


def test_quantity_limit_matches_available_stock_and_order_cap():
    assert quantity_limit(0) == 0
    assert quantity_limit(1) == 1
    assert quantity_limit(2) == 2
    assert quantity_limit(8) == 3


def test_product_description_is_limited_to_three_lines():
    preview = description_preview("First\nSecond\nThird\nFourth", "en")

    assert preview.splitlines() == ["First", "Second", "Third", "• ..."]


def test_parse_package_photo_caption():
    assert parse_product_caption("សាកល្បង | 0.01 | ស្ដុកសាកល្បង") == (
        "សាកល្បង",
        "0.01",
        "0.01",
        "ស្ដុកសាកល្បង",
    )


def test_parse_package_caption_without_media_or_optional_details():
    assert parse_product_caption("Key 1h | 0.50") == ("Key 1h", "0.50", "0.50", "")


def test_parse_package_caption_with_reseller_price_and_details():
    assert parse_product_caption("Key 1h | 0.50 | 0.35 | One-hour key") == (
        "Key 1h",
        "0.50",
        "0.35",
        "One-hour key",
    )


def test_parse_package_caption_rejects_reseller_price_not_below_customer_price():
    for caption in ("Key 1h | 0.50 | 0.50 | Details", "Key 1h | 0.50 | 0.60 | Details"):
        try:
            parse_product_caption(caption, "en")
        except ValueError as exc:
            assert "reseller price" in str(exc)
        else:
            raise AssertionError("Expected invalid reseller pricing to be rejected")


def test_product_price_uses_reseller_price_only_for_resellers():
    product = {"price": "0.50", "reseller_price": "0.35"}

    assert product_price(product) == Decimal("0.50")
    assert product_price(product, is_reseller=True) == Decimal("0.35")


def test_parse_stock_add_accepts_comma_and_newline_separated_codes():
    assert parse_stock_add("addstock product-123 | FF-KEY-1, FF-KEY-2\nFF-KEY-3") == (
        "product-123",
        ["FF-KEY-1", "FF-KEY-2", "FF-KEY-3"],
    )


def test_parse_stock_add_ignores_other_text_and_rejects_missing_credentials():
    assert parse_stock_add("hello there") is None
    try:
        parse_stock_add("addstock product-123 |  ")
    except ValueError as exc:
        assert "License Key" in str(exc)
    else:
        raise AssertionError("Expected empty stock data to be rejected")


def test_menu_uses_dynamic_tutorial_callback_and_groups_language_support():
    markup = menu(SimpleNamespace(admin_ids=set()), 12345, "en")

    assert markup.inline_keyboard[2][0].callback_data == "how_to_buy"
    assert [button.callback_data for button in markup.inline_keyboard[3]] == ["language", "support"]


def test_admin_welcome_keyboard_includes_admin_access_and_store_actions():
    markup = welcome_keyboard(12345, SimpleNamespace(admin_ids={12345}))

    rows = [[button.text for button in row] for row in markup.keyboard]
    assert rows[0] == ["⚙️ ADMIN"]
    assert rows[1] == ["👤 Account", "💰 Add funds"]
    assert rows[2] == ["🎮 Games Service"]
    assert rows[3] == ["🛒 My Orders", "🎥 How to Buy"]
    assert all("Roblox" not in button for row in rows for button in row)


def test_wallet_deposit_keyboard_has_presets_custom_and_back():
    markup = wallet_deposit_keyboard("en")

    assert [[button.text for button in row] for row in markup.inline_keyboard] == [
        ["$1", "$5", "$10"],
        ["$15", "$20", "$50"],
        ["✏️ Custom amount"],
        ["⬅️ Back"],
    ]


def test_custom_wallet_amount_is_parsed_to_the_exact_supported_amount():
    assert parse_wallet_deposit_amount("12.5") == Decimal("12.5")
    assert parse_wallet_deposit_amount("500.00") == Decimal("500.00")


def test_wallet_amount_rejects_values_outside_limits_or_precision():
    for amount in ("0.99", "500.01", "1.001", "not-a-number", "NaN", "Infinity"):
        try:
            parse_wallet_deposit_amount(amount, "en")
        except ValueError as exc:
            assert "Amount" in str(exc) or "valid" in str(exc)
        else:
            raise AssertionError(f"Expected invalid amount {amount!r} to be rejected")


def test_wallet_schema_error_detection_only_matches_missing_wallet_tables():
    missing_wallet_table = RuntimeError(
        "PGRST205: Could not find the table 'public.user_wallets' in the schema cache"
    )
    missing_product_table = RuntimeError(
        "PGRST205: Could not find the table 'public.products' in the schema cache"
    )

    assert is_wallet_schema_missing(missing_wallet_table)
    assert not is_wallet_schema_missing(missing_product_table)
    assert not is_wallet_schema_missing(RuntimeError("Network timeout"))
    assert "run the latest schema.sql" in wallet_error_message(
        "en",
        missing_wallet_table,
        is_admin=True,
    )
    assert "shop admin" in wallet_error_message("en", missing_wallet_table)


def test_wallet_balance_defaults_to_zero_when_supabase_returns_no_row_or_response():
    async def run_test():
        for response in (None, SimpleNamespace(data=None), SimpleNamespace(data={})):
            query = Mock()
            query.select.return_value = query
            query.eq.return_value = query
            query.maybe_single.return_value = query
            query.execute = AsyncMock(return_value=response)
            client = SimpleNamespace(table=Mock(return_value=query))

            assert await Store(client).wallet_balance(12345) == "0.00"

        query = Mock()
        query.select.return_value = query
        query.eq.return_value = query
        query.maybe_single.return_value = query
        query.execute = AsyncMock(return_value=SimpleNamespace(data={"balance": "12.50"}))
        client = SimpleNamespace(table=Mock(return_value=query))
        assert await Store(client).wallet_balance(12345) == "12.50"

    asyncio.run(run_test())


def test_wallet_deposit_completion_credits_once_and_notifies_user():
    async def run_test():
        bot = SimpleNamespace(delete_message=AsyncMock(), send_message=AsyncMock())
        store = SimpleNamespace(
            complete_wallet_deposit=AsyncMock(return_value=True),
            wallet_deposit_message=AsyncMock(return_value={"chat_id": 123, "message_id": 456}),
            wallet_balance=AsyncMock(return_value="6.00"),
            clear_payment_message=AsyncMock(),
            user_language=AsyncMock(return_value="en"),
        )
        deposit = {"id": "deposit-123", "chat_id": 123, "amount": "5.00"}

        completed = await complete_wallet_deposit(bot, store, deposit)

        assert completed is True
        store.complete_wallet_deposit.assert_awaited_once_with("deposit-123")
        bot.delete_message.assert_awaited_once_with(chat_id=123, message_id=456)
        assert "$5.00 was added to your wallet!" in bot.send_message.await_args.args[1]
        assert "New balance: $6.00" in bot.send_message.await_args.args[1]

    asyncio.run(run_test())


def test_catalog_pagination_limits_items_and_clamps_page():
    items = list(range(18))

    first_page, page, page_count = paginate(items, 0)
    last_page, last_page_number, last_page_count = paginate(items, 99)

    assert first_page == list(range(8))
    assert (page, page_count) == (0, 3)
    assert last_page == [16, 17]
    assert (last_page_number, last_page_count) == (2, 3)


def test_catalog_pagination_navigation_has_one_row_and_hides_unavailable_directions():
    first_page = pagination_row(0, 3, "shop:catalogs", "en")
    last_page = pagination_row(2, 3, "shop:catalogs", "en")

    assert [button.callback_data for button in first_page] == ["noop", "shop:catalogs:1"]
    assert [button.callback_data for button in last_page] == ["shop:catalogs:1", "noop"]
    assert pagination_row(0, 1, "shop:catalogs", "en") == []


def test_edit_text_message_replaces_photo_with_text_message():
    async def run_test():
        message = SimpleNamespace(
            text=None,
            photo=[object()],
            message_id=456,
            delete=AsyncMock(),
            answer=AsyncMock(),
            edit_text=AsyncMock(),
        )

        await edit_text_message(message, "Choose a package", parse_mode="HTML")

        message.delete.assert_awaited_once_with()
        message.answer.assert_awaited_once_with("Choose a package", parse_mode="HTML", reply_markup=None)
        message.edit_text.assert_not_awaited()

    asyncio.run(run_test())


def test_edit_text_message_switches_wallet_prompt_without_adding_duplicate_bubble():
    async def run_test():
        message = SimpleNamespace(
            text="Choose an amount",
            answer=AsyncMock(),
            edit_text=AsyncMock(),
        )
        markup = SimpleNamespace()

        await edit_text_message(
            message,
            "Enter a custom amount",
            reply_markup=markup,
        )

        message.edit_text.assert_awaited_once_with(
            "Enter a custom amount",
            parse_mode=None,
            reply_markup=markup,
        )
        message.answer.assert_not_awaited()

    asyncio.run(run_test())


def test_edit_text_message_can_send_reply_for_user_text_input():
    async def run_test():
        message = SimpleNamespace(answer=AsyncMock(), edit_text=AsyncMock())
        markup = SimpleNamespace()

        await edit_text_message(
            message,
            "Choose a category",
            reply_markup=markup,
            edit_message=False,
        )

        message.answer.assert_awaited_once_with(
            "Choose a category",
            parse_mode=None,
            reply_markup=markup,
        )
        message.edit_text.assert_not_awaited()

    asyncio.run(run_test())


def test_edit_text_message_replaces_video_message_with_text_message():
    async def run_test():
        message = SimpleNamespace(
            text=None,
            video=object(),
            message_id=789,
            delete=AsyncMock(),
            answer=AsyncMock(),
            edit_text=AsyncMock(),
        )

        await edit_text_message(message, "Welcome", parse_mode="HTML")

        message.delete.assert_awaited_once_with()
        message.answer.assert_awaited_once_with("Welcome", parse_mode="HTML", reply_markup=None)
        message.edit_text.assert_not_awaited()

    asyncio.run(run_test())


def test_delete_replaced_message_removes_previous_screen():
    async def run_test():
        message = SimpleNamespace(message_id=123, delete=AsyncMock())

        await delete_replaced_message(message)

        message.delete.assert_awaited_once_with()

    asyncio.run(run_test())


def test_send_replacing_screen_deletes_previous_bot_screen_and_user_input():
    async def run_test():
        user_id = 23456
        previous = SimpleNamespace(message_id=1, delete=AsyncMock())
        sent = SimpleNamespace(message_id=3)
        incoming = SimpleNamespace(
            from_user=SimpleNamespace(id=user_id),
            message_id=2,
            answer=AsyncMock(return_value=sent),
            delete=AsyncMock(),
        )
        bot_module.active_screen_messages[user_id] = previous
        bot_module.welcome_message_ids[user_id] = previous.message_id

        result = await send_replacing_screen(incoming, user_id, "Next screen")

        assert result is sent
        previous.delete.assert_awaited_once_with()
        incoming.delete.assert_awaited_once_with()
        assert bot_module.active_screen_messages[user_id] is sent
        assert user_id not in bot_module.welcome_message_ids
        bot_module.active_screen_messages.pop(user_id, None)

    asyncio.run(run_test())


def test_switch_active_screen_keeps_menu_keyboard_message_and_replaces_only_screen():
    async def run_test():
        user_id = 23458
        menu_message = SimpleNamespace(message_id=10, delete=AsyncMock())
        first_screen = SimpleNamespace(message_id=11, delete=AsyncMock())
        second_screen = SimpleNamespace(message_id=12)
        bot_module.menu_keyboard_messages[user_id] = menu_message
        bot_module.welcome_message_ids[user_id] = menu_message.message_id
        bot_module.active_screen_messages[user_id] = menu_message

        await bot_module.switch_active_screen(user_id, first_screen)
        assert menu_message.delete.await_count == 0
        assert bot_module.menu_keyboard_messages[user_id] is menu_message
        assert bot_module.active_screen_messages[user_id] is first_screen

        await bot_module.switch_active_screen(user_id, second_screen)
        first_screen.delete.assert_awaited_once_with()
        assert bot_module.menu_keyboard_messages[user_id] is menu_message
        assert bot_module.active_screen_messages[user_id] is second_screen

        bot_module.active_screen_messages.pop(user_id, None)
        bot_module.menu_keyboard_messages.pop(user_id, None)
        bot_module.welcome_message_ids.pop(user_id, None)

    asyncio.run(run_test())


def test_update_active_screen_edits_prompt_and_deletes_user_reply():
    async def run_test():
        user_id = 23457
        updated = SimpleNamespace(message_id=1, text="Updated")
        prompt = SimpleNamespace(
            message_id=1,
            text="Send a value",
            edit_text=AsyncMock(return_value=updated),
        )
        incoming = SimpleNamespace(
            from_user=SimpleNamespace(id=user_id),
            message_id=2,
            delete=AsyncMock(),
        )
        bot_module.active_screen_messages[user_id] = prompt

        result = await update_active_screen(
            user_id,
            "Saved",
            input_message=incoming,
        )

        assert result is updated
        prompt.edit_text.assert_awaited_once_with("Saved", parse_mode=None, reply_markup=None)
        incoming.delete.assert_awaited_once_with()
        assert bot_module.active_screen_messages[user_id] is updated
        bot_module.active_screen_messages.pop(user_id, None)

    asyncio.run(run_test())


def test_admin_category_actions_group_catalog_controls_after_package_creation():
    rows = admin_category_actions("category-123")

    assert [[button.callback_data for button in row] for row in rows] == [
        ["admin:product-add:category-123"],
        ["admin:category-edit:category-123", "admin:category-disable:category-123"],
        ["admin:catalogs"],
    ]


def test_admin_package_actions_group_editing_and_stock_controls():
    rows = admin_product_actions("product-123", "category-123")

    assert [[button.callback_data for button in row] for row in rows] == [
        ["admin:product-edit:product-123", "admin:product-disable:product-123"],
        ["admin:stock-add:product-123", "admin:stock-remove:product-123"],
        ["au:product-123"],
        ["ac:category-123"],
    ]


def test_admin_actions_are_translated_for_english_users():
    category_rows = admin_category_actions("category-123", "en")
    product_rows = admin_product_actions("product-123", "category-123", "en")

    assert [button.text for row in category_rows for button in row] == [
        "➕ Add package",
        "✏️ Edit catalog",
        "🗑 Disable catalog",
        "⬅️ Back",
    ]
    assert [button.text for row in product_rows for button in row] == [
        "✏️ Edit package",
        "🗑 Disable package",
        "➕ Add stock",
        "➖ Remove stock",
        "📥 Import stock (.txt)",
        "⬅️ Back",
    ]


def test_khqr_payment_keyboard_has_only_aba_and_check_buttons():
    open_url = "https://shop.example.com/aba/order-123"
    markup = payment_keyboard("order-123", open_url)

    assert len(markup.inline_keyboard) == 2
    assert markup.inline_keyboard[0][0].text == "🏦 បើកក្នុង ABA Mobile"
    assert markup.inline_keyboard[0][0].url == open_url
    assert markup.inline_keyboard[1][0].text == "✅ ខ្ញុំបានទូទាត់ · ពិនិត្យ"
    assert markup.inline_keyboard[1][0].callback_data == "check_payment_order-123"


def test_khqr_payment_keyboard_supports_english():
    markup = payment_keyboard("order-123", "https://shop.example.com/aba/order-123", "en")

    assert [row[0].text for row in markup.inline_keyboard] == [
        "🏦 Open in ABA Mobile",
        "✅ I have paid · Check",
    ]


def test_payment_caption_matches_requested_html_layout_and_escapes_data():
    caption = payment_caption("Gold <Package>", 2, "10.5", "txn-123", "en")

    assert caption == (
        "💳 <b>Pay $10.50</b>\n\n"
        "🛍 <b>Product:</b> Gold &lt;Package&gt; x2\n"
        "💰 <b>Total:</b> $10.50\n"
        "🟢 <b>Remaining to pay:</b> $10.50\n"
        "🏷 <b>Ref:</b> <code>txn-123</code>\n"
        "⏱ <b>Scan KHQR to complete payment.</b>\n"
        "⏰ <b>You have 4 minutes to pay. The QR refreshes itself.</b>"
    )


def test_payment_caption_supports_khmer():
    caption = payment_caption("Gold", 1, "5", "txn-123", "km")

    assert "ទូទាត់ $5.00" in caption
    assert "ផលិតផល៖" in caption
    assert "ស្កេន KHQR ដើម្បីបញ្ចប់ការទូទាត់។" in caption
    assert "អ្នកមានពេល 4 នាទី" in caption


def test_terminal_payment_status_deletes_qr_after_message_is_saved():
    async def run_test():
        for status in ("paid", "expired", "failed", "cancelled"):
            bot = SimpleNamespace(delete_message=AsyncMock())
            store = SimpleNamespace(
                set_payment_message=AsyncMock(return_value=status),
                payment_message=AsyncMock(return_value={"chat_id": 123, "message_id": 456}),
                clear_payment_message=AsyncMock(),
            )

            await track_payment_message(bot, store, "order-123", 123, 456)

            bot.delete_message.assert_awaited_once_with(chat_id=123, message_id=456)
            store.clear_payment_message.assert_awaited_once_with("order-123")

    asyncio.run(run_test())


def test_expired_payment_deletes_qr_notifies_user_and_releases_order():
    async def run_test():
        bot = SimpleNamespace(
            delete_message=AsyncMock(),
            send_message=AsyncMock(),
        )
        store = SimpleNamespace(
            payment_message=AsyncMock(return_value={"chat_id": 123, "message_id": 456}),
            clear_payment_message=AsyncMock(),
            release_order=AsyncMock(),
            user_language=AsyncMock(return_value="km"),
        )

        await expire_payment(bot, store, "order-123", 123, "expired")

        bot.delete_message.assert_awaited_once_with(chat_id=123, message_id=456)
        bot.send_message.assert_awaited_once_with(
            123,
            "❌ ការទូទាត់មិនបានបញ្ចប់។ QR code ត្រូវបានលុបចោល។ "
            "សូមព្យាយាមម្តងទៀតប្រសិនបើអ្នកនៅតែចង់ទិញផលិតផលនេះ។",
        )
        store.release_order.assert_awaited_once_with("order-123", "expired")
        store.user_language.assert_awaited_once_with(123)

    asyncio.run(run_test())


def test_expired_payment_message_uses_english_preference():
    async def run_test():
        bot = SimpleNamespace(delete_message=AsyncMock(), send_message=AsyncMock())
        store = SimpleNamespace(
            payment_message=AsyncMock(return_value=None),
            clear_payment_message=AsyncMock(),
            release_order=AsyncMock(),
            user_language=AsyncMock(return_value="en"),
        )

        await expire_payment(bot, store, "order-123", 123, "expired")

        assert bot.send_message.await_args.args == (
            123,
            "❌ Payment was not completed and the QR code was cancelled. Please try again if you still want to buy this product.",
        )

    asyncio.run(run_test())


def test_product_image_update_targets_product_and_stores_cloudinary_url():
    async def run_test():
        product_id = "product-123"
        image_url = "https://res.cloudinary.com/example/image/upload/product.png"
        query = Mock()
        query.update.return_value = query
        query.eq.return_value = query
        query.select.return_value = query
        query.maybe_single.return_value = query
        query.execute = AsyncMock(return_value=SimpleNamespace(data={"id": product_id}))
        client = SimpleNamespace(table=Mock(return_value=query))

        updated = await Store(client).update_product_image(product_id, image_url)

        assert updated
        client.table.assert_called_once_with("products")
        query.update.assert_called_once_with({"image_url": image_url, "video_url": None})
        query.eq.assert_called_once_with("id", product_id)

    asyncio.run(run_test())


def test_product_creation_inserts_category_and_cloudinary_image_url():
    async def run_test():
        query = Mock()
        query.insert.return_value = query
        query.select.return_value = query
        query.execute = AsyncMock(return_value=SimpleNamespace(data=[{"id": "product-123"}]))
        client = SimpleNamespace(table=Mock(return_value=query))

        product_id = await Store(client).create_product(
            "category-123",
            "Test package",
            "0.01",
            "Description",
            "https://res.cloudinary.com/example/image/upload/test.png",
        )

        assert product_id == "product-123"
        query.insert.assert_called_once_with(
            {
                "category_id": "category-123",
                "name": "Test package",
                "price": "0.01",
                "reseller_price": "0.01",
                "description": "Description",
                "image_url": "https://res.cloudinary.com/example/image/upload/test.png",
            }
        )

    asyncio.run(run_test())


def test_product_creation_without_media():
    async def run_test():
        query = Mock()
        query.insert.return_value = query
        query.select.return_value = query
        query.execute = AsyncMock(return_value=SimpleNamespace(data=[{"id": "product-123"}]))
        client = SimpleNamespace(table=Mock(return_value=query))

        product_id = await Store(client).create_product(
            "category-123",
            "Key 1h",
            "0.50",
            "",
        )

        assert product_id == "product-123"
        query.insert.assert_called_once_with(
            {
                "category_id": "category-123",
                "name": "Key 1h",
                "price": "0.50",
                "reseller_price": "0.50",
                "description": "",
            }
        )

    asyncio.run(run_test())


def test_product_creation_persists_a_reseller_price():
    async def run_test():
        query = Mock()
        query.insert.return_value = query
        query.select.return_value = query
        query.execute = AsyncMock(return_value=SimpleNamespace(data=[{"id": "product-123"}]))
        client = SimpleNamespace(table=Mock(return_value=query))

        product_id = await Store(client).create_product(
            "category-123",
            "Key 1h",
            "0.50",
            "One-hour key",
            reseller_price="0.35",
        )

        assert product_id == "product-123"
        assert query.insert.call_args.args[0]["price"] == "0.50"
        assert query.insert.call_args.args[0]["reseller_price"] == "0.35"

    asyncio.run(run_test())


def test_product_creation_saves_optional_after_order_message_and_media():
    async def run_test():
        query = Mock()
        query.insert.return_value = query
        query.select.return_value = query
        query.execute = AsyncMock(return_value=SimpleNamespace(data=[{"id": "product-123"}]))
        client = SimpleNamespace(table=Mock(return_value=query))

        await Store(client).create_product(
            "category-123",
            "Game code",
            "1.00",
            "",
            delivery_message="Redeem at example.com",
            delivery_image_url="https://res.cloudinary.com/example/image/upload/delivery.png",
            delivery_video_url="https://res.cloudinary.com/example/video/upload/guide.mp4",
        )

        values = query.insert.call_args.args[0]
        assert values["delivery_message"] == "Redeem at example.com"
        assert values["delivery_image_url"].endswith("delivery.png")
        assert values["delivery_video_url"].endswith("guide.mp4")

    asyncio.run(run_test())


def test_product_update_changes_fields_and_replaces_media_only_when_requested():
    async def run_test():
        query = Mock()
        query.update.return_value = query
        query.eq.return_value = query
        query.execute = AsyncMock(return_value=SimpleNamespace(data=[]))
        client = SimpleNamespace(table=Mock(return_value=query))

        await Store(client).update_product(
            "product-123",
            "Updated key",
            "0.50",
            "0.35",
            "Updated details",
            image_url="https://example.com/image.png",
            update_media=True,
        )

        query.update.assert_called_once_with(
            {
                "name": "Updated key",
                "price": "0.50",
                "reseller_price": "0.35",
                "description": "Updated details",
                "image_url": "https://example.com/image.png",
                "video_url": None,
            }
        )
        query.eq.assert_called_once_with("id", "product-123")

    asyncio.run(run_test())


def test_product_update_can_replace_and_clear_after_order_content():
    async def run_test():
        query = Mock()
        query.update.return_value = query
        query.eq.return_value = query
        query.execute = AsyncMock(return_value=SimpleNamespace(data=[]))
        client = SimpleNamespace(table=Mock(return_value=query))

        await Store(client).update_product(
            "product-123",
            "Updated key",
            "0.50",
            "0.35",
            "Updated details",
            delivery_message="",
            delivery_image_url=None,
            delivery_video_url="https://res.cloudinary.com/example/video/upload/guide.mp4",
            update_delivery=True,
        )

        values = query.update.call_args.args[0]
        assert values["delivery_message"] == ""
        assert values["delivery_image_url"] is None
        assert values["delivery_video_url"].endswith("guide.mp4")

    asyncio.run(run_test())


def test_order_delivery_details_loads_product_delivery_content():
    async def run_test():
        order_query = Mock()
        order_query.select.return_value = order_query
        order_query.eq.return_value = order_query
        order_query.maybe_single.return_value = order_query
        order_query.execute = AsyncMock(
            return_value=SimpleNamespace(data={"product_id": "product-123"})
        )
        product_query = Mock()
        product_query.select.return_value = product_query
        product_query.eq.return_value = product_query
        product_query.maybe_single.return_value = product_query
        delivery = {
            "delivery_message": "Redeem now",
            "delivery_image_url": "https://example.com/image.png",
            "delivery_video_url": None,
        }
        product_query.execute = AsyncMock(return_value=SimpleNamespace(data=delivery))
        client = SimpleNamespace(table=Mock(side_effect=[order_query, product_query]))

        result = await Store(client).order_delivery_details("order-123")

        assert result == delivery
        product_query.eq.assert_called_once_with("id", "product-123")

    asyncio.run(run_test())


def test_order_notification_details_loads_order_product_and_customer_profile():
    async def run_test():
        results = {
            "orders": {
                "id": "order-123",
                "chat_id": 123,
                "product_id": "product-123",
                "quantity": 2,
                "total": "2.00",
                "transaction_id": "txn-123",
                "created_at": "2026-10-20T09:35:00+00:00",
                "status": "paid",
            },
            "products": {"name": "Key 1Day", "price": "1.50", "reseller_price": "1.00"},
            "users": {"username": "buyer", "first_name": "Buyer"},
        }
        queries = {}
        for table_name, data in results.items():
            query = Mock()
            query.select.return_value = query
            query.eq.return_value = query
            query.maybe_single.return_value = query
            query.execute = AsyncMock(return_value=SimpleNamespace(data=data))
            queries[table_name] = query
        client = SimpleNamespace(table=Mock(side_effect=lambda name: queries[name]))

        details = await Store(client).order_notification_details("order-123")

        assert details["product"] == results["products"]
        assert details["customer"] == results["users"]
        queries["orders"].eq.assert_called_once_with("id", "order-123")
        queries["products"].eq.assert_called_once_with("id", "product-123")
        queries["users"].eq.assert_called_once_with("telegram_id", 123)

    asyncio.run(run_test())


def test_order_history_page_fetches_ten_orders_and_product_names():
    async def run_test():
        orders = [
            {
                "id": f"order-{index}",
                "product_id": f"product-{index % 2}",
                "quantity": 1,
                "total": "1.00",
                "status": "paid",
                "transaction_id": f"txn-{index}",
                "created_at": "2026-10-20T09:35:00+00:00",
            }
            for index in range(11)
        ]
        orders_query = Mock()
        orders_query.select.return_value = orders_query
        orders_query.eq.return_value = orders_query
        orders_query.order.return_value = orders_query
        orders_query.range.return_value = orders_query
        orders_query.execute = AsyncMock(return_value=SimpleNamespace(data=orders))

        products_query = Mock()
        products_query.select.return_value = products_query
        products_query.in_.return_value = products_query
        products_query.execute = AsyncMock(
            return_value=SimpleNamespace(
                data=[
                    {"id": "product-0", "name": "Robux"},
                    {"id": "product-1", "name": "Gift card"},
                ]
            )
        )
        client = SimpleNamespace(table=Mock(side_effect=[orders_query, products_query]))

        result, has_next = await Store(client).order_history_page(12345, page=1)

        assert len(result) == 10
        assert has_next
        assert result[0]["product_name"] == "Robux"
        orders_query.range.assert_called_once_with(10, 20)
        products_query.in_.assert_called_once_with("id", ["product-0", "product-1"])

    asyncio.run(run_test())


def test_scheduled_broadcast_store_operations_use_persistent_job_status():
    async def run_test():
        def query_with_result(result):
            query = Mock()
            for method in ("insert", "update", "select", "eq", "lte", "order", "limit", "maybe_single"):
                getattr(query, method).return_value = query
            query.execute = AsyncMock(return_value=SimpleNamespace(data=result))
            return query

        create_query = query_with_result([{"id": "job-1"}])
        due_query = query_with_result([{"id": "job-1", "admin_id": 123, "content": []}])
        claim_query = query_with_result([{"id": "job-1"}])
        finish_query = query_with_result(None)
        client = SimpleNamespace(
            table=Mock(side_effect=[create_query, due_query, claim_query, finish_query])
        )
        store = Store(client)

        job_id = await store.create_scheduled_broadcast(
            123,
            "2026-10-30T08:30:00+00:00",
            [{"message_id": 10, "media_group_id": None}],
            [{"text": "Shop", "url": "https://example.com"}],
        )
        due = await store.due_broadcast_jobs("2026-10-30T08:30:00+00:00")
        claimed = await store.claim_broadcast_job(job_id)
        await store.finish_broadcast_job(job_id, "sent", sent=5, failed=1)

        assert job_id == "job-1"
        assert due[0]["id"] == "job-1"
        assert claimed
        due_query.lte.assert_called_once_with("scheduled_at", "2026-10-30T08:30:00+00:00")
        assert finish_query.update.call_args.args[0]["status"] == "sent"
        assert finish_query.update.call_args.args[0]["sent_count"] == 5
        assert finish_query.update.call_args.args[0]["failed_count"] == 1

    asyncio.run(run_test())


def test_reseller_registry_uses_telegram_ids():
    async def run_test():
        query = Mock()
        query.upsert.return_value = query
        query.execute = AsyncMock(return_value=SimpleNamespace(data=None))
        client = SimpleNamespace(table=Mock(return_value=query))

        await Store(client).add_reseller(12345)

        client.table.assert_called_once_with("resellers")
        query.upsert.assert_called_once_with(
            {"telegram_id": 12345},
            on_conflict="telegram_id",
            ignore_duplicates=True,
        )

    asyncio.run(run_test())


def test_is_reseller_returns_false_when_maybe_single_has_no_result():
    async def run_test():
        query = Mock()
        query.select.return_value = query
        query.eq.return_value = query
        query.maybe_single.return_value = query
        query.execute = AsyncMock(return_value=None)
        client = SimpleNamespace(table=Mock(return_value=query))

        assert await Store(client).is_reseller(12345) is False
        client.table.assert_called_once_with("resellers")
        query.eq.assert_called_once_with("telegram_id", 12345)

    asyncio.run(run_test())


def test_order_reservation_passes_telegram_id_for_database_pricing():
    async def run_test():
        query = Mock()
        query.execute = AsyncMock(return_value=SimpleNamespace(data={"id": "order-1", "total": "0.70"}))
        client = SimpleNamespace(rpc=Mock(return_value=query))

        order = await Store(client).reserve_order(12345, "product-123", 2)

        assert order["total"] == "0.70"
        client.rpc.assert_called_once_with(
            "reserve_order",
            {"p_chat_id": 12345, "p_product_id": "product-123", "p_quantity": 2},
        )

    asyncio.run(run_test())


def test_wallet_order_reservation_passes_owner_product_and_quantity():
    async def run_test():
        query = Mock()
        query.execute = AsyncMock(return_value=SimpleNamespace(data={"id": "order-wallet", "total": "2.00"}))
        client = SimpleNamespace(rpc=Mock(return_value=query))

        order = await Store(client).reserve_wallet_order(12345, "product-123", 2)

        assert order["id"] == "order-wallet"
        client.rpc.assert_called_once_with(
            "reserve_wallet_order",
            {"p_chat_id": 12345, "p_product_id": "product-123", "p_quantity": 2},
        )


    asyncio.run(run_test())


def test_stock_removal_matches_only_available_stock_for_the_selected_product():
    async def run_test():
        product_id = "product-123"
        credential = "FF-KEY-REMOVE"
        key = "MDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDA="
        query = Mock()
        query.delete.return_value = query
        query.eq.return_value = query
        query.in_.return_value = query
        query.select.return_value = query
        query.execute = AsyncMock(return_value=SimpleNamespace(data=[{"id": "stock-1"}]))
        client = SimpleNamespace(table=Mock(return_value=query))

        removed = await Store(client).remove_stock(product_id, [credential], key)

        assert removed == 1
        client.table.assert_called_once_with("stock_items")
        query.eq.assert_any_call("product_id", product_id)
        query.eq.assert_any_call("status", "available")
        query.in_.assert_called_once_with(
            "credential_fingerprint",
            [stock_fingerprint(credential, key)],
        )

    asyncio.run(run_test())


def test_stock_import_uses_database_deduplication_function_and_returns_added_count():
    async def run_test():
        product_id = "product-123"
        credential = "FF-KEY-REISSUE"
        key = Fernet.generate_key().decode("ascii")
        query = Mock()
        query.execute = AsyncMock(return_value=SimpleNamespace(data=1))
        client = SimpleNamespace(rpc=Mock(return_value=query))

        added = await Store(client).import_stock(product_id, [credential], key)

        assert added == 1
        client.rpc.assert_called_once()
        function_name, arguments = client.rpc.call_args.args
        assert function_name == "import_stock_items"
        assert arguments["p_product_id"] == product_id
        stock_item = arguments["p_items"][0]
        assert decrypt_stock(stock_item["credential_cipher"], key) == credential
        assert stock_item["credential_fingerprint"] == stock_fingerprint(credential, key)

    asyncio.run(run_test())


def test_product_photo_downloads_and_uploads_to_cloudinary(monkeypatch):
    async def run_test():
        import app.bot as bot_module

        async def download_photo(file_id, destination):
            destination.write(b"photo-bytes")

        download = AsyncMock(side_effect=download_photo)
        telegram_bot = SimpleNamespace(download=download)
        settings = SimpleNamespace(
            cloudinary_cloud_name="cloud",
            cloudinary_api_key="key",
            cloudinary_api_secret="secret",
        )
        uploaded = Mock(return_value={"secure_url": "https://res.cloudinary.com/cloud/image/upload/item.png"})
        monkeypatch.setattr(bot_module.cloudinary.uploader, "upload", uploaded)

        image_url = await upload_product_image(telegram_bot, settings, "telegram-file-id")

        assert image_url == "https://res.cloudinary.com/cloud/image/upload/item.png"
        download.assert_awaited_once()
        assert uploaded.call_args.args[0].getvalue() == b"photo-bytes"
        assert uploaded.call_args.kwargs["resource_type"] == "image"
        assert uploaded.call_args.kwargs["cloud_name"] == "cloud"

    asyncio.run(run_test())


def test_product_video_downloads_and_uploads_to_cloudinary(monkeypatch):
    async def run_test():
        async def download_video(file_id, destination):
            destination.write(b"video-bytes")

        telegram_bot = SimpleNamespace(download=AsyncMock(side_effect=download_video))
        settings = SimpleNamespace(
            cloudinary_cloud_name="cloud",
            cloudinary_api_key="key",
            cloudinary_api_secret="secret",
        )
        uploaded = Mock(return_value={"secure_url": "https://res.cloudinary.com/cloud/video/upload/item.mp4"})
        monkeypatch.setattr(bot_module.cloudinary.uploader, "upload", uploaded)

        video_url = await upload_product_video(telegram_bot, settings, "telegram-video-id")

        assert video_url == "https://res.cloudinary.com/cloud/video/upload/item.mp4"
        assert uploaded.call_args.args[0].getvalue() == b"video-bytes"
        assert uploaded.call_args.kwargs["resource_type"] == "video"
        assert uploaded.call_args.kwargs["folder"] == "products"

    asyncio.run(run_test())


def test_product_video_creation_and_update_store_video_url():
    async def run_test():
        query = Mock()
        query.insert.return_value = query
        query.select.return_value = query
        query.execute = AsyncMock(return_value=SimpleNamespace(data=[{"id": "product-123"}]))
        client = SimpleNamespace(table=Mock(return_value=query))

        product_id = await Store(client).create_product(
            "category-123",
            "Video package",
            "0.01",
            "Description",
            video_url="https://res.cloudinary.com/example/video/upload/test.mp4",
        )

        assert product_id == "product-123"
        assert query.insert.call_args.args[0]["video_url"] == "https://res.cloudinary.com/example/video/upload/test.mp4"
        assert "image_url" not in query.insert.call_args.args[0]

        update_query = Mock()
        update_query.update.return_value = update_query
        update_query.eq.return_value = update_query
        update_query.select.return_value = update_query
        update_query.maybe_single.return_value = update_query
        update_query.execute = AsyncMock(return_value=SimpleNamespace(data={"id": product_id}))
        updated = await Store(SimpleNamespace(table=Mock(return_value=update_query))).update_product_video(
            product_id,
            "https://res.cloudinary.com/example/video/upload/replacement.mp4",
        )

        assert updated
        update_query.update.assert_called_once_with(
            {
                "image_url": None,
                "video_url": "https://res.cloudinary.com/example/video/upload/replacement.mp4",
            }
        )

    asyncio.run(run_test())


def test_delivery_deletes_payment_qr_before_fulfilling_and_sending_credentials():
    async def run_test():
        events = []
        key = "MDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDA="

        async def delete_message(**kwargs):
            events.append("delete")

        async def clear_payment_message(order_id):
            events.append("clear")

        async def fulfill_order(order_id):
            events.append("fulfill")
            return [
                encrypt_stock("FF-1H-KEY-001", key),
                encrypt_stock("FF-1D-KEY-002", key),
            ]

        async def send_message(*args, **kwargs):
            events.append("send")

        async def mark_delivered(order_id):
            events.append("mark")

        bot = SimpleNamespace(
            delete_message=AsyncMock(side_effect=delete_message),
            send_message=AsyncMock(side_effect=send_message),
        )
        store = SimpleNamespace(
            payment_message=AsyncMock(return_value={"chat_id": 123, "message_id": 456}),
            clear_payment_message=AsyncMock(side_effect=clear_payment_message),
            order_delivery_details=AsyncMock(return_value={}),
            bot_settings=AsyncMock(return_value={}),
            fulfill_order=AsyncMock(side_effect=fulfill_order),
            mark_delivered=AsyncMock(side_effect=mark_delivered),
        )
        settings = SimpleNamespace(stock_encryption_key=key)

        await deliver_order(bot, store, settings, "order-123", 123)

        assert events == ["delete", "clear", "fulfill", "send", "mark"]
        bot.delete_message.assert_awaited_once_with(chat_id=123, message_id=456)
        assert bot.send_message.await_args.args == (
            123,
            "✅ ទំនិញបានជោគជ័យ\n\n"
            "🔑 បញ្ជី Key របស់អ្នក [ សរុប 2 Key ]\n"
            "- <code>FF-1H-KEY-001</code>\n"
            "- <code>FF-1D-KEY-002</code>\n\n"
            "🙏 អរគុណសម្រាប់ការទិញទំនិញ 🙏",
        )
        assert bot.send_message.await_args.kwargs["parse_mode"] == "HTML"
        buttons = bot.send_message.await_args.kwargs["reply_markup"].inline_keyboard
        assert buttons[0][0].text == "📋 Copy Key 1"
        assert buttons[0][0].copy_text.text == "FF-1H-KEY-001"
        assert buttons[1][0].text == "📋 Copy Key 2"
        assert buttons[1][0].copy_text.text == "FF-1D-KEY-002"
        assert len(buttons) == 2

    asyncio.run(run_test())


def test_delivery_sends_configured_product_message_and_media_before_stock_codes():
    async def run_test():
        events = []
        key = "MDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDA="

        async def send_message(chat_id, text, **kwargs):
            events.append(("message", text))

        async def send_photo(chat_id, photo, **kwargs):
            events.append(("photo", photo))

        async def send_video(chat_id, video, **kwargs):
            events.append(("video", video))

        bot = SimpleNamespace(
            send_message=AsyncMock(side_effect=send_message),
            send_photo=AsyncMock(side_effect=send_photo),
            send_video=AsyncMock(side_effect=send_video),
        )
        store = SimpleNamespace(
            payment_message=AsyncMock(return_value=None),
            order_delivery_details=AsyncMock(
                return_value={
                    "delivery_message": "Redeem <now>",
                    "delivery_image_url": "https://example.com/instructions.png",
                    "delivery_video_url": "https://example.com/guide.mp4",
                }
            ),
            fulfill_order=AsyncMock(return_value=[encrypt_stock("CODE-001", key)]),
            mark_delivered=AsyncMock(),
        )
        settings = SimpleNamespace(stock_encryption_key=key, order_notification_chat_ids="")

        await deliver_order(bot, store, settings, "order-123", 123)

        assert events[0] == ("message", "Redeem &lt;now&gt;")
        assert events[1] == ("photo", "https://example.com/instructions.png")
        assert events[2] == ("video", "https://example.com/guide.mp4")
        assert events[3][0] == "message"
        assert "CODE-001" in events[3][1]
        store.mark_delivered.assert_awaited_once_with("order-123")

    asyncio.run(run_test())


def test_order_success_notification_sends_order_details_to_configured_group_and_channel():
    async def run_test():
        order = {
            "id": "order-123",
            "chat_id": 123456789,
            "quantity": 1,
            "total": "1.00",
            "transaction_id": "txn_abc123456",
            "created_at": "2026-10-20T09:35:00+00:00",
            "product": {"name": "Key FreeFire 1Day", "price": "2.00", "reseller_price": "1.00"},
            "customer": {"first_name": "ChhinhLong", "username": "chhinhlong"},
        }
        bot = SimpleNamespace(send_message=AsyncMock())
        store = SimpleNamespace(order_notification_details=AsyncMock(return_value=order))
        settings = SimpleNamespace(order_notification_chat_ids="-1001234567890,@adminchannel")

        await notify_order_success(bot, store, settings, "order-123")

        assert [call.args[0] for call in bot.send_message.await_args_list] == [
            -1001234567890,
            "@adminchannel",
        ]
        text = bot.send_message.await_args_list[0].args[1]
        assert "ChhinhLong (@chhinhlong)" in text
        assert "<code>123456789</code>" in text
        assert "Key FreeFire 1Day (x1)" in text
        assert "$1.00 (Reseller Price)" in text
        assert "<code>txn_abc123456</code>" in text
        assert "2026-10-20 16:35:00 (Asia/Phnom_Penh)" in text
        assert "បង់ប្រាក់រួចរាល់ &amp; ប្រគល់ Key រួចរាល់" in text
        assert bot.send_message.await_args.kwargs["parse_mode"] == "HTML"

    asyncio.run(run_test())


def test_user_language_falls_back_to_khmer_when_preferences_are_unavailable():
    store = SimpleNamespace(user_language=AsyncMock(side_effect=RuntimeError("users table missing")))

    language = asyncio.run(safe_user_language(store, 12345))

    assert language == "km"


def test_stock_notification_reaches_users_and_configured_chats_once():
    async def run_test():
        bot = SimpleNamespace(
            get_me=AsyncMock(return_value=SimpleNamespace(username="JingRobloxBot")),
            send_message=AsyncMock(),
        )
        store = SimpleNamespace(
            product=AsyncMock(return_value={"id": "product-123", "name": "Robux"}),
            stock_notification_users=AsyncMock(
                return_value=[
                    {"telegram_id": 12345, "language": "en"},
                    {"telegram_id": 23456, "language": "km"},
                ]
            ),
        )
        await notify_stock_added(bot, store, "-100987,@shopnews,-100987", "product-123", 4)

        assert [call.args[0] for call in bot.send_message.await_args_list] == [
            12345,
            23456,
            -100987,
            "@shopnews",
        ]
        assert "New quantity: 4" in bot.send_message.await_args_list[0].args[1]
        assert "ចំនួនថ្មី៖ 4" in bot.send_message.await_args_list[1].args[1]
        assert "New quantity: 4" in bot.send_message.await_args_list[2].args[1]
        button = bot.send_message.await_args_list[0].kwargs["reply_markup"].inline_keyboard[0][0]
        assert button.url == "https://t.me/JingRobloxBot?start=package_product-123"
        assert button.callback_data is None
        assert bot.send_message.await_args_list[0].kwargs["reply_markup"].inline_keyboard[0][0].text == "🛍 View product"
        assert bot.send_message.await_args_list[1].kwargs["reply_markup"].inline_keyboard[0][0].text == "🛍 មើល Package"

    asyncio.run(run_test())


def test_stock_notification_start_links_open_the_package_in_private_chat():
    assert stock_notification_url("@JingRobloxBot", "product-123") == (
        "https://t.me/JingRobloxBot?start=package_product-123"
    )
    assert package_id_from_start("/start package_product-123") == "product-123"
    assert package_id_from_start("/start@JingRobloxBot package_product-123") == "product-123"
    assert package_id_from_start("/start") is None
    assert package_id_from_start("/start package_") is None
    assert package_id_from_start("/start package_bad%20id") is None


def test_stock_notification_skips_duplicate_stock():
    async def run_test():
        bot = SimpleNamespace(send_message=AsyncMock())
        store = SimpleNamespace(
            product=AsyncMock(),
            stock_notification_users=AsyncMock(),
        )
        await notify_stock_added(bot, store, "@shopnews", "product-123", 0)

        store.product.assert_not_awaited()
        store.stock_notification_users.assert_not_awaited()
        bot.send_message.assert_not_awaited()

    asyncio.run(run_test())


def test_stock_notification_users_are_loaded_in_pages():
    async def run_test():
        first_page = [{"telegram_id": user_id, "language": "km"} for user_id in range(1000)]
        second_page = [{"telegram_id": 1000, "language": "en"}]
        query = Mock()
        query.select.return_value = query
        query.order.return_value = query
        query.range.return_value = query
        query.execute = AsyncMock(
            side_effect=[
                SimpleNamespace(data=first_page),
                SimpleNamespace(data=second_page),
            ]
        )
        client = SimpleNamespace(table=Mock(return_value=query))

        users = await Store(client).stock_notification_users()

        assert len(users) == 1001
        assert [call.args for call in query.range.call_args_list] == [(0, 999), (1000, 1999)]

    asyncio.run(run_test())


def test_expiration_deletes_cached_qr_when_message_id_persistence_fails():
    async def run_test():
        bot = SimpleNamespace(delete_message=AsyncMock(), send_message=AsyncMock())
        store = SimpleNamespace(
            set_payment_message=AsyncMock(side_effect=RuntimeError("database unavailable")),
            payment_message=AsyncMock(return_value=None),
            clear_payment_message=AsyncMock(),
            release_order=AsyncMock(),
            user_language=AsyncMock(return_value="km"),
        )

        await track_payment_message(bot, store, "order-cache-fallback", 123, 456)
        await expire_payment(bot, store, "order-cache-fallback", 123, "expired")

        bot.delete_message.assert_awaited_once_with(chat_id=123, message_id=456)
        bot.send_message.assert_awaited_once()
        store.release_order.assert_awaited_once_with("order-cache-fallback", "expired")

    asyncio.run(run_test())