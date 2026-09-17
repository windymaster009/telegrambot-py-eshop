from __future__ import annotations

import asyncio
import logging
from html import escape

from aiogram import Bot, Dispatcher, Router
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramAPIError
from aiogram.filters import Command
from aiogram.types import Message

from app.aba_payments import AbaPayment, parse_aba_payment
from app.config import Settings, get_settings
from app.database import Database
from app.money import format_money
from app.notifications import send_deposit_approved
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
    return (
        "🔎 <b>Payment listener status</b>\n"
        f"Check bot: @{escape(me.username or str(me.id))}\n"
        f"Configured group: <code>{settings.aba_payment_group_id or 'not set'}</code>\n"
        f"Group membership: <b>{escape(member_status)}</b>\n"
        f"Group privacy: <b>{'disabled' if privacy_disabled else 'enabled'}</b>\n"
        f"Group-message access: <b>{'ready' if permissions_ready else 'not ready'}</b>\n"
        "Reader mode: <b>ABA text scan</b>\n"
        "Every ABA-formatted text or caption received from this configured group is scanned."
    )


@router.message(Command("listenerstatus"))
async def show_listener_status(message: Message, settings: Settings, check_bot: Bot) -> None:
    if message.from_user is None or message.from_user.id not in settings.admin_ids:
        return
    await message.answer(await _listener_status(check_bot, settings))


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

    payment = parse_aba_payment(message.text or message.caption)
    if payment is None:
        return

    sender = message.from_user or message.sender_chat
    sender_label = _normalized_username(sender) or str(getattr(sender, "id", "unknown"))
    logger.info(
        "Scanned ABA transaction %s for %s from message sender %s",
        payment.transaction_id,
        _payment_summary(payment),
        sender_label,
    )

    result = await service.process_aba_topup(payment)
    if result.duplicate:
        logger.info("Ignored duplicate ABA transaction %s", payment.transaction_id)
        return

    if result.deposit is None:
        await check_bot.send_message(
            message.chat.id,
            "⚠️ <b>ABA payment received, but no active top-up matched</b>\n"
            f"Amount: <b>{_payment_summary(payment)}</b>\n"
            f"Payer: {escape(payment.payer_name)} ({escape(payment.payer_account)})\n"
            f"Paid at: {escape(payment.paid_at_text or 'not provided')}\n"
            f"Transaction: <code>{escape(payment.transaction_id)}</code>\n"
            f"APV: <code>{escape(payment.apv or 'not provided')}</code>",
        )
        return

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
        message.chat.id,
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
        await dispatcher.start_polling(
            check_bot,
            service=service,
            settings=settings,
            shop_bot=shop_bot,
            check_bot=check_bot,
            allowed_updates=["message", "channel_post"],
        )
    finally:
        await shop_bot.session.close()
        await check_bot.session.close()
        await database.close()


if __name__ == "__main__":
    asyncio.run(main())
