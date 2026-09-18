from __future__ import annotations

import asyncio
import logging
from html import escape
from typing import Any

from aiogram import Bot, Dispatcher, Router
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramAPIError
from aiogram.filters import Command
from aiogram.types import Message
from telethon import TelegramClient, events
from telethon.sessions import StringSession

from app.aba_payments import AbaPayment, parse_aba_payment
from app.config import Settings, get_settings
from app.database import Database
from app.money import format_money
from app.notifications import send_deposit_approved, send_deposit_expired
from app.services import ShopService

logger = logging.getLogger(__name__)
router = Router(name="aba-payment-listener")


def _normalized_username(user: object | None) -> str:
    username = getattr(user, "username", None)
    if not username:
        return ""
    return str(username).removeprefix("@").lower()


def _payment_summary(payment: AbaPayment) -> str:
    if payment.currency == "USD":
        return format_money(payment.amount_minor)
    return f"KHR {payment.amount_minor:,}"


@router.message(Command("chatid"))
async def show_chat_id(message: Message, settings: Settings) -> None:
    if message.from_user is None or message.from_user.id not in settings.admin_ids:
        return
    await message.answer(f"This chat ID is: <code>{message.chat.id}</code>")


async def _listener_status(check_bot: Bot, settings: Settings) -> str:
    me = await check_bot.get_me()
    privacy_disabled = bool(getattr(me, "can_read_all_group_messages", False))
    member_status = "not checked"
    group_access = False

    if settings.aba_payment_group_id is not None:
        try:
            member = await check_bot.get_chat_member(settings.aba_payment_group_id, me.id)
            raw_status = member.status
            member_status = getattr(raw_status, "value", str(raw_status))
            group_access = member_status in {"administrator", "creator", "owner"}
        except TelegramAPIError as exc:
            member_status = f"error: {exc}"

    permissions_ready = group_access or privacy_disabled
    user_reader_ready = bool(getattr(settings, "payment_reader_configured", False))
    reader_mode = (
        "Telegram user session + Bot API fallback"
        if user_reader_ready
        else "Bot API fallback only"
    )
    return (
        "🔎 <b>Payment listener status</b>\n"
        f"Check bot: @{escape(me.username or str(me.id))}\n"
        f"Configured group: <code>{settings.aba_payment_group_id or 'not set'}</code>\n"
        f"Group membership: <b>{escape(member_status)}</b>\n"
        f"Group privacy: <b>{'disabled' if privacy_disabled else 'enabled'}</b>\n"
        f"Bot group access: <b>{'ready' if permissions_ready else 'not ready'}</b>\n"
        f"PayWay bot-message reader: <b>{'ready' if user_reader_ready else 'missing'}</b>\n"
        f"Reader mode: <b>{reader_mode}</b>\n"
        "Every ABA-formatted text received from the configured group is scanned."
    )


@router.message(Command("listenerstatus"))
async def show_listener_status(message: Message, settings: Settings, check_bot: Bot) -> None:
    if message.from_user is None or message.from_user.id not in settings.admin_ids:
        return
    await message.answer(await _listener_status(check_bot, settings))


async def _process_aba_text(
    text: str | None,
    *,
    service: ShopService,
    shop_bot: Bot,
    check_bot: Bot,
    group_chat_id: int,
    sender_label: str,
) -> bool:
    payment = parse_aba_payment(text)
    if payment is None:
        return False

    logger.info(
        "Scanned ABA transaction %s for %s from message sender %s",
        payment.transaction_id,
        _payment_summary(payment),
        sender_label,
    )

    result = await service.process_aba_topup(payment)
    if result.duplicate:
        logger.info("Ignored duplicate ABA transaction %s", payment.transaction_id)
        return True

    if result.deposit is None:
        await check_bot.send_message(
            group_chat_id,
            "⚠️ <b>ABA payment received, but no active top-up matched</b>\n"
            f"Amount: <b>{_payment_summary(payment)}</b>\n"
            f"Payer: {escape(payment.payer_name)} ({escape(payment.payer_account)})\n"
            f"Paid at: {escape(payment.paid_at_text or 'not provided')}\n"
            f"Transaction: <code>{escape(payment.transaction_id)}</code>\n"
            f"APV: <code>{escape(payment.apv or 'not provided')}</code>",
        )
        return True

    deposit = result.deposit
    notification_sent = True
    try:
        await send_deposit_approved(shop_bot, deposit)
    except (TelegramAPIError, OSError):
        notification_sent = False
        logger.exception(
            "Top-up %s was credited, but the customer notification failed", deposit.id
        )

    customer_notice = "sent" if notification_sent else "failed; contact the customer"
    await check_bot.send_message(
        group_chat_id,
        "✅ <b>Top-up automatically confirmed</b>\n"
        f"Queue: <code>TOPUP-{deposit.id}</code>\n"
        f"User ID: <code>{deposit.user_id}</code>\n"
        f"Amount credited: <b>{format_money(deposit.amount_cents)}</b>\n"
        f"Payer: {escape(payment.payer_name)} ({escape(payment.payer_account)})\n"
        f"Paid at: {escape(payment.paid_at_text or 'not provided')}\n"
        f"ABA transaction: <code>{escape(payment.transaction_id)}</code>\n"
        f"APV: <code>{escape(payment.apv or 'not provided')}</code>\n"
        f"Customer notification: {customer_notice}",
    )
    return True


@router.message()
@router.channel_post()
async def receive_aba_notification(
    message: Message,
    service: ShopService,
    settings: Settings,
    shop_bot: Bot,
    check_bot: Bot,
) -> None:
    if settings.aba_payment_group_id is None:
        return
    if message.chat.id != settings.aba_payment_group_id:
        return

    sender = message.from_user or message.sender_chat
    sender_label = _normalized_username(sender) or str(getattr(sender, "id", "unknown"))
    await _process_aba_text(
        message.text or message.caption,
        service=service,
        shop_bot=shop_bot,
        check_bot=check_bot,
        group_chat_id=message.chat.id,
        sender_label=sender_label,
    )


async def _notify_expired_topups_once(service: ShopService, shop_bot: Bot) -> int:
    await service.expire_deposits()
    deposits = await service.expired_deposits_awaiting_notification()
    processed = 0
    for deposit in deposits:
        delivered = True
        try:
            await send_deposit_expired(
                shop_bot,
                deposit,
                minutes=service.topup_expiry_minutes,
            )
        except (TelegramAPIError, OSError):
            delivered = False
            logger.exception("Could not notify the customer that top-up %s expired", deposit.id)
        await service.mark_expiry_notification_processed(deposit.id, delivered=delivered)
        processed += 1
    return processed


async def _notify_expired_topups(service: ShopService, shop_bot: Bot) -> None:
    while True:
        try:
            await _notify_expired_topups_once(service, shop_bot)
        except Exception:
            logger.exception("Top-up expiry notification sweep failed")
        await asyncio.sleep(10)


async def _run_user_reader(
    settings: Settings,
    service: ShopService,
    shop_bot: Bot,
    check_bot: Bot,
) -> None:
    if not settings.payment_reader_configured:
        raise RuntimeError("Telegram user-account payment reader is not configured")
    if settings.aba_payment_group_id is None:
        raise RuntimeError("ABA_PAYMENT_GROUP_ID is required for the user-account reader")
    if (
        settings.payment_reader_api_id is None
        or settings.payment_reader_api_hash is None
        or settings.payment_reader_session is None
    ):
        raise RuntimeError("All PAYMENT_READER_* values are required")

    client = TelegramClient(
        StringSession(settings.payment_reader_session.get_secret_value()),
        settings.payment_reader_api_id,
        settings.payment_reader_api_hash.get_secret_value(),
        sequential_updates=True,
    )

    @client.on(events.NewMessage(chats=settings.aba_payment_group_id))
    async def receive_user_session_message(event: Any) -> None:
        sender = await event.get_sender()
        sender_label = (
            str(getattr(sender, "username", "") or "")
            or str(getattr(event, "sender_id", "unknown"))
        )
        await _process_aba_text(
            event.raw_text,
            service=service,
            shop_bot=shop_bot,
            check_bot=check_bot,
            group_chat_id=settings.aba_payment_group_id,
            sender_label=sender_label,
        )

    await client.connect()
    try:
        if not await client.is_user_authorized():
            raise RuntimeError(
                "PAYMENT_READER_SESSION is not authorized. Generate a fresh session string."
            )
        me = await client.get_me()
        logger.info(
            "Telegram user-account payment reader connected as %s (%s)",
            getattr(me, "username", None) or getattr(me, "first_name", "unknown"),
            getattr(me, "id", "unknown"),
        )
        await client.run_until_disconnected()
    finally:
        await client.disconnect()


async def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    )
    settings = get_settings()
    if not settings.auto_topup_enabled:
        logger.info("Automatic top-ups are disabled; payment listener is stopping")
        return
    if settings.payment_check_bot_token is None:
        raise RuntimeError("PAYMENT_CHECK_BOT_TOKEN is required for the payment listener")
    if (
        settings.payment_check_bot_token.get_secret_value()
        == settings.bot_token.get_secret_value()
    ):
        raise RuntimeError("PAYMENT_CHECK_BOT_TOKEN must belong to a separate Telegram bot")

    database = Database(settings)
    await database.connect()
    service = ShopService(
        database.db,
        database.client,
        settings.payment_expiry_minutes,
        topup_expiry_minutes=settings.topup_expiry_minutes,
        auto_topup_enabled=True,
    )
    check_bot = Bot(
        settings.payment_check_bot_token.get_secret_value(),
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    shop_bot = Bot(
        settings.bot_token.get_secret_value(),
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    dispatcher = Dispatcher()
    dispatcher.include_router(router)

    try:
        webhook = await check_bot.get_webhook_info()
        if webhook.url:
            raise RuntimeError(
                "The payment-check bot still has a webhook configured. Use a dedicated bot or "
                "delete its old webhook before starting this polling listener."
            )
        if settings.aba_payment_group_id is None:
            logger.warning(
                "ABA_PAYMENT_GROUP_ID is empty. Add the bot to the group, send /chatid as an "
                "admin, then save that ID in .env and restart this process."
            )
        status = await _listener_status(check_bot, settings)
        logger.info("Payment listener diagnostics:\n%s", status)
        logger.warning(
            "ABA text-scan mode trusts matching text received in the configured payment group. "
            "Keep that group private and allow only trusted members."
        )
        reader_values = (
            settings.payment_reader_api_id,
            settings.payment_reader_api_hash,
            settings.payment_reader_session,
        )
        if any(value is not None for value in reader_values) and not (
            settings.payment_reader_configured
        ):
            raise RuntimeError(
                "PAYMENT_READER_API_ID, PAYMENT_READER_API_HASH and "
                "PAYMENT_READER_SESSION must all be configured"
            )

        tasks = [
            asyncio.create_task(
                dispatcher.start_polling(
                    check_bot,
                    service=service,
                    settings=settings,
                    shop_bot=shop_bot,
                    check_bot=check_bot,
                    allowed_updates=["message", "channel_post"],
                ),
                name="payment-check-bot",
            ),
            asyncio.create_task(
                _notify_expired_topups(service, shop_bot),
                name="top-up-expiry-notifier",
            ),
        ]
        if settings.payment_reader_configured:
            tasks.append(
                asyncio.create_task(
                    _run_user_reader(settings, service, shop_bot, check_bot),
                    name="telegram-user-payment-reader",
                )
            )
        else:
            logger.error(
                "PAYMENT_READER_* is missing. Telegram Bot API does not deliver messages "
                "written by PayWay's bot, so automatic matching will not work until the "
                "read-only user session is configured."
            )

        done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        error: BaseException | None = None
        for task in done:
            if not task.cancelled() and task.exception() is not None and error is None:
                error = task.exception()
        for task in pending:
            task.cancel()
        await asyncio.gather(*pending, return_exceptions=True)
        if error is not None:
            raise error
    finally:
        await shop_bot.session.close()
        await check_bot.session.close()
        await database.close()


if __name__ == "__main__":
    asyncio.run(main())
