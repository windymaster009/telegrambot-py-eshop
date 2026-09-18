from __future__ import annotations

from html import escape

from aiogram import Bot, F, Router
from aiogram.exceptions import TelegramAPIError
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder

from app.config import Settings
from app.i18n import tr
from app.keyboards import admin_home, admin_product_actions, admin_products, review_refund
from app.money import format_money, parse_money
from app.notifications import send_refund_paid, send_refund_rejected
from app.services import (
    AlreadyProcessed,
    NotEnoughStock,
    ShopError,
    ShopService,
    delivery_text,
    product_name,
)
from app.states import AdminState

router = Router(name="admin")


def is_admin(user_id: int, settings: Settings) -> bool:
    return user_id in settings.admin_ids


async def deny(callback: CallbackQuery, settings: Settings) -> bool:
    if not is_admin(callback.from_user.id, settings):
        await callback.answer("Not authorized", show_alert=True)
        return True
    return False


@router.message(Command("admin"))
async def admin_panel(message: Message, state: FSMContext, settings: Settings) -> None:
    if message.from_user is None or not is_admin(message.from_user.id, settings):
        return
    await state.clear()
    await message.answer("🛠 <b>Shop admin</b>", reply_markup=admin_home())


@router.callback_query(F.data == "admin:home")
async def admin_home_callback(
    callback: CallbackQuery, state: FSMContext, settings: Settings
) -> None:
    if await deny(callback, settings):
        return
    await state.clear()
    if callback.message:
        await callback.message.edit_text("🛠 <b>Shop admin</b>", reply_markup=admin_home())
    await callback.answer()


@router.callback_query(F.data == "admin:products")
async def list_products(callback: CallbackQuery, service: ShopService, settings: Settings) -> None:
    if await deny(callback, settings):
        return
    products = await service.list_products(include_inactive=True)
    rows = [
        (view.product.id, view.product.name_en, view.stock, view.product.active)
        for view in products
    ]
    text = "📦 <b>Products</b>\nSelect one to manage it."
    if not rows:
        text += "\n\nNo products yet."
    if callback.message:
        await callback.message.edit_text(text, reply_markup=admin_products(rows))
    await callback.answer()


@router.callback_query(F.data.regexp(r"^admin:product:\d+$"))
async def product_detail(callback: CallbackQuery, service: ShopService, settings: Settings) -> None:
    if await deny(callback, settings):
        return
    product_id = int(callback.data.rsplit(":", 1)[1])  # type: ignore[union-attr]
    view = await service.get_product(product_id, include_inactive=True)
    if view is None:
        await callback.answer("Product not found", show_alert=True)
        return
    product = view.product
    text = (
        f"📦 <b>#{product.id} {escape(product.name_en)}</b>\n\n"
        f"Price: {format_money(product.price_cents)}\n"
        f"Warranty: {escape(product.warranty)}\n"
        f"Available stock: {view.stock}\n"
        f"Status: {'Active' if product.active else 'Disabled'}\n\n"
        f"{escape(product.description_en)}"
    )
    if callback.message:
        await callback.message.edit_text(
            text, reply_markup=admin_product_actions(product.id, product.active)
        )
    await callback.answer()


@router.callback_query(F.data == "admin:add_product")
async def begin_add_product(callback: CallbackQuery, state: FSMContext, settings: Settings) -> None:
    if await deny(callback, settings):
        return
    await state.set_state(AdminState.adding_product)
    if callback.message:
        await callback.message.answer(
            "Send the product in this format:\n\n"
            "<code>Name | Price | Warranty | Description</code>\n\n"
            "Example:\n"
            "<code>ChatGPT Plus 1 Month | 12.00 | 30 days | Private account</code>\n\n"
            "Optional Khmer fields:\n"
            "<code>Name EN | Price | Warranty | Description EN | Name KM | Description KM</code>"
        )
    await callback.answer()


@router.message(AdminState.adding_product, F.text)
async def receive_product(
    message: Message, state: FSMContext, service: ShopService, settings: Settings
) -> None:
    if message.from_user is None or not is_admin(message.from_user.id, settings):
        return
    parts = [part.strip() for part in (message.text or "").split("|", maxsplit=5)]
    if len(parts) < 4:
        await message.answer("Invalid format. Use: Name | Price | Warranty | Description")
        return
    try:
        price_cents = parse_money(parts[1])
    except ValueError:
        await message.answer("Invalid price. Example: 12.00")
        return
    product = await service.add_product(
        name_en=parts[0],
        price_cents=price_cents,
        warranty=parts[2],
        description_en=parts[3],
        name_km=parts[4] if len(parts) > 4 and parts[4] else None,
        description_km=parts[5] if len(parts) > 5 and parts[5] else None,
    )
    await state.clear()
    await message.answer(
        f"✅ Product #{product.id} created. Add stock before customers can buy it.",
        reply_markup=admin_product_actions(product.id, product.active),
    )


@router.callback_query(F.data.regexp(r"^admin:edit_product:\d+$"))
async def begin_edit_product(
    callback: CallbackQuery, state: FSMContext, service: ShopService, settings: Settings
) -> None:
    if await deny(callback, settings):
        return
    product_id = int(callback.data.rsplit(":", 1)[1])  # type: ignore[union-attr]
    view = await service.get_product(product_id, include_inactive=True)
    if view is None:
        await callback.answer("Product not found", show_alert=True)
        return
    product = view.product
    await state.set_state(AdminState.editing_product)
    await state.update_data(product_id=product_id)
    current = " | ".join(
        [
            product.name_en,
            f"{product.price_cents / 100:.2f}",
            product.warranty,
            product.description_en,
            product.name_km or "",
            product.description_km or "",
        ]
    )
    if callback.message:
        await callback.message.answer(
            "✏️ Send the complete updated product line. Copy and edit this:\n\n"
            f"<code>{escape(current)}</code>"
        )
    await callback.answer()


@router.message(AdminState.editing_product, F.text)
async def receive_product_edit(
    message: Message, state: FSMContext, service: ShopService, settings: Settings
) -> None:
    if message.from_user is None or not is_admin(message.from_user.id, settings):
        return
    parts = [part.strip() for part in (message.text or "").split("|", maxsplit=5)]
    if len(parts) < 4:
        await message.answer("Invalid format. Use: Name | Price | Warranty | Description")
        return
    try:
        price_cents = parse_money(parts[1])
    except ValueError:
        await message.answer("Invalid price. Example: 12.00")
        return
    data = await state.get_data()
    product = await service.update_product(
        product_id=int(data["product_id"]),
        name_en=parts[0],
        price_cents=price_cents,
        warranty=parts[2],
        description_en=parts[3],
        name_km=parts[4] if len(parts) > 4 and parts[4] else None,
        description_km=parts[5] if len(parts) > 5 and parts[5] else None,
    )
    await state.clear()
    await message.answer(
        f"✅ Product #{product.id} updated.",
        reply_markup=admin_product_actions(product.id, product.active),
    )


@router.callback_query(F.data.regexp(r"^admin:add_stock:\d+$"))
async def begin_add_stock(
    callback: CallbackQuery, state: FSMContext, service: ShopService, settings: Settings
) -> None:
    if await deny(callback, settings):
        return
    product_id = int(callback.data.rsplit(":", 1)[1])  # type: ignore[union-attr]
    view = await service.get_product(product_id, include_inactive=True)
    if view is None:
        await callback.answer("Product not found", show_alert=True)
        return
    await state.set_state(AdminState.adding_stock)
    await state.update_data(product_id=product_id)
    if callback.message:
        await callback.message.answer(
            f"➕ Adding stock to <b>#{product_id} {escape(view.product.name_en)}</b>\n\n"
            "Send one account/key per line. Each non-empty line becomes one stock item.\n\n"
            "Example:\n<code>email1@example.com | password1\n"
            "email2@example.com | password2</code>\n\n"
            "⚠️ Send credentials only in this private admin chat."
        )
    await callback.answer()


@router.message(AdminState.adding_stock, F.text)
async def receive_stock(
    message: Message, state: FSMContext, service: ShopService, settings: Settings
) -> None:
    if message.from_user is None or not is_admin(message.from_user.id, settings):
        return
    data = await state.get_data()
    contents = (message.text or "").splitlines()
    count = await service.add_stock(int(data["product_id"]), contents)
    if not count:
        await message.answer("No stock items found. Send at least one non-empty line.")
        return
    await state.clear()
    await message.answer(f"✅ Added {count} stock item(s).", reply_markup=admin_home())


@router.callback_query(F.data.regexp(r"^admin:toggle_product:\d+$"))
async def toggle_product(callback: CallbackQuery, service: ShopService, settings: Settings) -> None:
    if await deny(callback, settings):
        return
    product_id = int(callback.data.rsplit(":", 1)[1])  # type: ignore[union-attr]
    try:
        product = await service.toggle_product(product_id)
    except ShopError:
        await callback.answer("Product not found", show_alert=True)
        return
    await callback.answer("Product enabled" if product.active else "Product disabled")
    view = await service.get_product(product_id, include_inactive=True)
    if callback.message and view:
        await callback.message.edit_reply_markup(
            reply_markup=admin_product_actions(product_id, product.active)
        )


@router.callback_query(F.data == "admin:pending_orders")
async def pending_orders(callback: CallbackQuery, service: ShopService, settings: Settings) -> None:
    if await deny(callback, settings):
        return
    orders = await service.pending_orders()
    builder = InlineKeyboardBuilder()
    for order in orders:
        builder.button(
            text=f"#{order.id} {order.product.name_en} — {format_money(order.total_cents)}",
            callback_data=f"admin:review_order:{order.id}",
        )
    builder.button(text="⬅️ Admin", callback_data="admin:home")
    builder.adjust(1)
    text = f"🧾 <b>Pending orders ({len(orders)})</b>"
    if callback.message:
        await callback.message.edit_text(text, reply_markup=builder.as_markup())
    await callback.answer()


@router.callback_query(F.data.regexp(r"^admin:review_order:\d+$"))
async def review_order_detail(
    callback: CallbackQuery, service: ShopService, settings: Settings, bot: Bot
) -> None:
    if await deny(callback, settings):
        return
    order_id = int(callback.data.rsplit(":", 1)[1])  # type: ignore[union-attr]
    orders = await service.pending_orders(limit=100)
    order = next((item for item in orders if item.id == order_id), None)
    if order is None:
        await callback.answer("Order is no longer pending", show_alert=True)
        return
    username = f"@{order.user.username}" if order.user.username else order.user.full_name
    caption = (
        f"🧾 <b>Order #{order.id}</b>\n"
        f"Customer: {escape(username)} (<code>{order.user.telegram_id}</code>)\n"
        f"Product: {escape(order.product.name_en)} × {order.quantity}\n"
        f"Total: <b>{format_money(order.total_cents)}</b>"
    )
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="✅ Approve & deliver", callback_data=f"admin:approve_order:{order.id}"
                ),
                InlineKeyboardButton(
                    text="❌ Reject", callback_data=f"admin:reject_order:{order.id}"
                ),
            ]
        ]
    )
    if callback.message:
        if order.payment_proof_file_id:
            await bot.send_photo(
                callback.from_user.id,
                order.payment_proof_file_id,
                caption=caption,
                reply_markup=keyboard,
            )
        else:
            await callback.message.answer(caption, reply_markup=keyboard)
    await callback.answer()


@router.callback_query(F.data.regexp(r"^admin:approve_order:\d+$"))
async def approve_order(
    callback: CallbackQuery, service: ShopService, settings: Settings, bot: Bot
) -> None:
    if await deny(callback, settings):
        return
    order_id = int(callback.data.rsplit(":", 1)[1])  # type: ignore[union-attr]
    try:
        order = await service.approve_order(order_id)
    except (AlreadyProcessed, NotEnoughStock):
        await callback.answer("Order already processed or stock unavailable", show_alert=True)
        return
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
    if callback.message:
        await callback.message.edit_reply_markup(reply_markup=None)
        await callback.message.reply(f"✅ Order #{order.id} approved and delivered.")
    await callback.answer("Approved and delivered")


@router.callback_query(F.data.regexp(r"^admin:reject_order:\d+$"))
async def reject_order(
    callback: CallbackQuery, service: ShopService, settings: Settings, bot: Bot
) -> None:
    if await deny(callback, settings):
        return
    order_id = int(callback.data.rsplit(":", 1)[1])  # type: ignore[union-attr]
    try:
        order = await service.reject_order(order_id)
    except AlreadyProcessed:
        await callback.answer("Order already processed", show_alert=True)
        return
    await bot.send_message(
        order.user.telegram_id,
        tr(order.user.language, "order_rejected", order_id=order.id),
    )
    if callback.message:
        await callback.message.edit_reply_markup(reply_markup=None)
        await callback.message.reply(f"❌ Order #{order.id} rejected.")
    await callback.answer("Rejected")


@router.callback_query(F.data == "admin:pending_deposits")
async def pending_deposits(
    callback: CallbackQuery, service: ShopService, settings: Settings
) -> None:
    if await deny(callback, settings):
        return
    deposits = await service.pending_deposits()
    builder = InlineKeyboardBuilder()
    for deposit in deposits:
        builder.button(
            text=f"#{deposit.id} {deposit.user.full_name} — {format_money(deposit.amount_cents)}",
            callback_data=f"admin:review_deposit:{deposit.id}",
        )
    builder.button(text="⬅️ Admin", callback_data="admin:home")
    builder.adjust(1)
    if callback.message:
        await callback.message.edit_text(
            f"💰 <b>Pending deposits ({len(deposits)})</b>", reply_markup=builder.as_markup()
        )
    await callback.answer()


@router.callback_query(F.data.regexp(r"^admin:review_deposit:\d+$"))
async def review_deposit_detail(
    callback: CallbackQuery, service: ShopService, settings: Settings, bot: Bot
) -> None:
    if await deny(callback, settings):
        return
    deposit_id = int(callback.data.rsplit(":", 1)[1])  # type: ignore[union-attr]
    deposits = await service.pending_deposits(limit=100)
    deposit = next((item for item in deposits if item.id == deposit_id), None)
    if deposit is None:
        await callback.answer("Deposit is no longer pending", show_alert=True)
        return
    caption = (
        f"💰 <b>Deposit #{deposit.id}</b>\n"
        f"Customer: {escape(deposit.user.full_name)} (<code>{deposit.user.telegram_id}</code>)\n"
        f"Amount: <b>{format_money(deposit.amount_cents)}</b>"
    )
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="✅ Approve", callback_data=f"admin:approve_deposit:{deposit.id}"
                ),
                InlineKeyboardButton(
                    text="❌ Reject", callback_data=f"admin:reject_deposit:{deposit.id}"
                ),
            ]
        ]
    )
    if deposit.payment_proof_file_id:
        await bot.send_photo(
            callback.from_user.id,
            deposit.payment_proof_file_id,
            caption=caption,
            reply_markup=keyboard,
        )
    await callback.answer()


@router.callback_query(F.data.regexp(r"^admin:approve_deposit:\d+$"))
async def approve_deposit(
    callback: CallbackQuery, service: ShopService, settings: Settings, bot: Bot
) -> None:
    if await deny(callback, settings):
        return
    deposit_id = int(callback.data.rsplit(":", 1)[1])  # type: ignore[union-attr]
    try:
        deposit = await service.approve_deposit(deposit_id)
    except AlreadyProcessed:
        await callback.answer("Deposit already processed", show_alert=True)
        return
    await bot.send_message(
        deposit.user.telegram_id,
        tr(
            deposit.user.language,
            "deposit_approved",
            deposit_id=deposit.id,
            balance=format_money(deposit.user.balance_cents),
        ),
    )
    if callback.message:
        await callback.message.edit_reply_markup(reply_markup=None)
        await callback.message.reply(f"✅ Deposit #{deposit.id} approved.")
    await callback.answer("Approved")


@router.callback_query(F.data.regexp(r"^admin:reject_deposit:\d+$"))
async def reject_deposit(
    callback: CallbackQuery, service: ShopService, settings: Settings, bot: Bot
) -> None:
    if await deny(callback, settings):
        return
    deposit_id = int(callback.data.rsplit(":", 1)[1])  # type: ignore[union-attr]
    try:
        deposit = await service.reject_deposit(deposit_id)
    except AlreadyProcessed:
        await callback.answer("Deposit already processed", show_alert=True)
        return
    await bot.send_message(
        deposit.user.telegram_id,
        tr(deposit.user.language, "deposit_rejected", deposit_id=deposit.id),
    )
    if callback.message:
        await callback.message.edit_reply_markup(reply_markup=None)
        await callback.message.reply(f"❌ Deposit #{deposit.id} rejected.")
    await callback.answer("Rejected")


@router.callback_query(F.data == "admin:pending_refunds")
async def pending_refunds(
    callback: CallbackQuery, service: ShopService, settings: Settings
) -> None:
    if await deny(callback, settings):
        return
    refunds = await service.pending_refunds()
    builder = InlineKeyboardBuilder()
    for refund in refunds:
        builder.button(
            text=(f"#{refund.id} {refund.user.full_name} — {format_money(refund.amount_cents)}"),
            callback_data=f"admin:review_refund:{refund.id}",
        )
    builder.button(text="⬅️ Admin", callback_data="admin:home")
    builder.adjust(1)
    if callback.message:
        await callback.message.edit_text(
            f"💸 <b>Pending refund tickets ({len(refunds)})</b>",
            reply_markup=builder.as_markup(),
        )
    await callback.answer()


@router.callback_query(F.data.regexp(r"^admin:review_refund:\d+$"))
async def review_refund_detail(
    callback: CallbackQuery, service: ShopService, settings: Settings, bot: Bot
) -> None:
    if await deny(callback, settings):
        return
    refund_id = int(callback.data.rsplit(":", 1)[1])  # type: ignore[union-attr]
    refunds = await service.pending_refunds(limit=100)
    refund = next((item for item in refunds if item.id == refund_id), None)
    if refund is None or refund.user is None:
        await callback.answer("Refund ticket is no longer pending", show_alert=True)
        return
    username = f"@{refund.user.username}" if refund.user.username else refund.user.full_name
    caption = (
        f"💸 <b>Refund ticket #{refund.id}</b>\n"
        f"Customer: {escape(username)} (<code>{refund.user.telegram_id}</code>)\n"
        f"Refund amount: <b>{format_money(refund.amount_cents)}</b>\n"
        f"Available balance after hold: <b>{format_money(refund.user.balance_cents)}</b>\n\n"
        "Pay this QR manually before pressing Mark paid."
    )
    if refund.qr_file_type == "document":
        await bot.send_document(
            callback.from_user.id,
            refund.qr_file_id,
            caption=caption,
            reply_markup=review_refund(refund.id),
        )
    else:
        await bot.send_photo(
            callback.from_user.id,
            refund.qr_file_id,
            caption=caption,
            reply_markup=review_refund(refund.id),
        )
    await callback.answer()


@router.callback_query(F.data.regexp(r"^admin:pay_refund:\d+$"))
async def pay_refund(
    callback: CallbackQuery, service: ShopService, settings: Settings, bot: Bot
) -> None:
    if await deny(callback, settings):
        return
    refund_id = int(callback.data.rsplit(":", 1)[1])  # type: ignore[union-attr]
    try:
        refund = await service.mark_refund_paid(
            refund_id,
            reviewed_by=callback.from_user.id,
        )
    except AlreadyProcessed:
        await callback.answer("Refund ticket already processed", show_alert=True)
        return
    notification_sent = True
    try:
        await send_refund_paid(bot, refund)
    except (TelegramAPIError, OSError):
        notification_sent = False
    if callback.message:
        await callback.message.edit_reply_markup(reply_markup=None)
        text = f"✅ Refund #{refund.id} marked paid — {format_money(refund.amount_cents)}."
        if not notification_sent:
            text += "\n⚠️ The customer notification could not be delivered."
        await callback.message.reply(text)
    await callback.answer("Marked as paid")


@router.callback_query(F.data.regexp(r"^admin:reject_refund:\d+$"))
async def reject_refund(
    callback: CallbackQuery, service: ShopService, settings: Settings, bot: Bot
) -> None:
    if await deny(callback, settings):
        return
    refund_id = int(callback.data.rsplit(":", 1)[1])  # type: ignore[union-attr]
    try:
        refund = await service.reject_refund(
            refund_id,
            reviewed_by=callback.from_user.id,
        )
    except AlreadyProcessed:
        await callback.answer("Refund ticket already processed", show_alert=True)
        return
    notification_sent = True
    try:
        await send_refund_rejected(bot, refund)
    except (TelegramAPIError, OSError):
        notification_sent = False
    if callback.message:
        await callback.message.edit_reply_markup(reply_markup=None)
        text = f"❌ Refund #{refund.id} rejected; {format_money(refund.amount_cents)} restored."
        if not notification_sent:
            text += "\n⚠️ The customer notification could not be delivered."
        await callback.message.reply(text)
    await callback.answer("Rejected and restored")
