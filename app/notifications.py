from html import escape

from aiogram import Bot

from app.i18n import tr
from app.models import Deposit, Order, RefundRequest
from app.money import format_money
from app.services import delivery_text, product_name


async def send_order_approved(bot: Bot, order: Order) -> None:
    if order.user is None:
        raise RuntimeError("Order user is not loaded")
    language = order.user.language
    await bot.send_message(
        order.user.telegram_id,
        tr(
            language,
            "purchase_complete",
            order_id=order.id,
            name=escape(product_name(order.product, language)),
            quantity=order.quantity,
            delivery=escape(delivery_text(order)),
        ),
    )


async def send_order_rejected(bot: Bot, order: Order) -> None:
    if order.user is None:
        raise RuntimeError("Order user is not loaded")
    await bot.send_message(
        order.user.telegram_id,
        tr(order.user.language, "order_rejected", order_id=order.id),
    )


async def send_deposit_approved(bot: Bot, deposit: Deposit) -> None:
    if deposit.user is None:
        raise RuntimeError("Deposit user is not loaded")
    await bot.send_message(
        deposit.user.telegram_id,
        tr(
            deposit.user.language,
            "deposit_approved",
            deposit_id=deposit.id,
            balance=format_money(deposit.user.balance_cents),
        ),
    )


async def send_deposit_rejected(bot: Bot, deposit: Deposit) -> None:
    if deposit.user is None:
        raise RuntimeError("Deposit user is not loaded")
    await bot.send_message(
        deposit.user.telegram_id,
        tr(deposit.user.language, "deposit_rejected", deposit_id=deposit.id),
    )


async def send_deposit_expired(bot: Bot, deposit: Deposit, *, minutes: int) -> None:
    if deposit.user is None:
        raise RuntimeError("Deposit user is not loaded")
    await bot.send_message(
        deposit.user.telegram_id,
        tr(
            deposit.user.language,
            "topup_failed",
            deposit_id=deposit.id,
            minutes=minutes,
        ),
    )


async def send_refund_paid(bot: Bot, refund: RefundRequest) -> None:
    if refund.user is None:
        raise RuntimeError("Refund user is not loaded")
    await bot.send_message(
        refund.user.telegram_id,
        tr(
            refund.user.language,
            "refund_paid",
            refund_id=refund.id,
            amount=format_money(refund.amount_cents),
        ),
    )


async def send_refund_rejected(bot: Bot, refund: RefundRequest) -> None:
    if refund.user is None:
        raise RuntimeError("Refund user is not loaded")
    await bot.send_message(
        refund.user.telegram_id,
        tr(
            refund.user.language,
            "refund_rejected",
            refund_id=refund.id,
            amount=format_money(refund.amount_cents),
            balance=format_money(refund.user.balance_cents),
        ),
    )
