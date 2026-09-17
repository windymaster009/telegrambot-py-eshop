from __future__ import annotations

from html import escape

from aiogram import Bot, F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, FSInputFile, Message

from app.config import Settings
from app.i18n import TEXTS, tr
from app.keyboards import (
    deposit_payment_actions,
    language_choices,
    main_menu,
    payment_choices,
    product_actions,
    product_list,
    review_deposit,
    review_order,
)
from app.models import DepositStatus, Language, utc_now
from app.money import format_money, parse_money
from app.services import (
    AlreadyProcessed,
    InsufficientBalance,
    NotEnoughStock,
    ShopService,
    delivery_text,
    product_description,
    product_name,
)
from app.states import CustomerState

router = Router(name="customer")

MENU_KEYS = {
    key: {TEXTS["en"][key], TEXTS["km"][key]}
    for key in (
        "menu_shop",
        "menu_balance",
        "menu_deposit",
        "menu_contact",
        "menu_language",
        "menu_orders",
    )
}


async def edit_or_answer(message: Message, text: str, **kwargs: object) -> Message:
    try:
        return await message.edit_text(text, **kwargs)  # type: ignore[arg-type]
    except TelegramBadRequest:
        return await message.answer(text, **kwargs)  # type: ignore[arg-type]


@router.message(CommandStart())
async def start(
    message: Message, state: FSMContext, service: ShopService, settings: Settings
) -> None:
    if message.from_user is None:
        return
    await state.clear()
    user = await service.get_or_create_user(message.from_user)
    username = f"@{user.username}" if user.username else user.full_name
    await message.answer(
        tr(
            user.language,
            "welcome",
            shop_name=escape(settings.shop_name),
            username=escape(username),
            balance=format_money(user.balance_cents),
        ),
        reply_markup=main_menu(user.language),
    )
    await send_shop(message, service, user.language)


@router.message(Command("cancel"))
async def cancel(message: Message, state: FSMContext, service: ShopService) -> None:
    await state.clear()
    if message.from_user:
        user = await service.get_user(message.from_user.id)
        await message.answer(tr(user.language, "cancelled"), reply_markup=main_menu(user.language))


@router.message(F.text.in_(MENU_KEYS["menu_shop"]))
async def shop_menu(message: Message, state: FSMContext, service: ShopService) -> None:
    await state.clear()
    if message.from_user:
        user = await service.get_user(message.from_user.id)
        await send_shop(message, service, user.language)


@router.callback_query(F.data == "shop")
async def shop_callback(callback: CallbackQuery, state: FSMContext, service: ShopService) -> None:
    await state.clear()
    if callback.from_user and callback.message:
        user = await service.get_user(callback.from_user.id)
        await show_shop(callback.message, service, user.language, edit=True)
    await callback.answer()


async def send_shop(message: Message, service: ShopService, language: str) -> None:
    await show_shop(message, service, language, edit=False)


async def show_shop(message: Message, service: ShopService, language: str, *, edit: bool) -> None:
    products = await service.list_products()
    if not products:
        text = tr(language, "no_products")
        if edit:
            await edit_or_answer(message, text)
        else:
            await message.answer(text)
        return
    rows = [
        (
            view.product.id,
            product_name(view.product, language),
            view.product.price_cents,
            view.stock,
        )
        for view in products
    ]
    kwargs = {"reply_markup": product_list(rows)}
    if edit:
        await edit_or_answer(message, tr(language, "products"), **kwargs)
    else:
        await message.answer(tr(language, "products"), **kwargs)


@router.callback_query(F.data.startswith("product:"))
async def view_product(callback: CallbackQuery, service: ShopService) -> None:
    if not callback.message:
        return
    user = await service.get_user(callback.from_user.id)
    product_id = int(callback.data.split(":")[1])  # type: ignore[union-attr]
    view = await service.get_product(product_id)
    if view is None:
        await callback.answer(tr(user.language, "out_of_stock"), show_alert=True)
        return
    text = tr(
        user.language,
        "product",
        name=escape(product_name(view.product, user.language)),
        price=format_money(view.product.price_cents),
        warranty=escape(view.product.warranty),
        stock=view.stock,
        description=escape(product_description(view.product, user.language)),
    )
    await edit_or_answer(
        callback.message,
        text,
        reply_markup=product_actions(
            view.product.id, view.product.price_cents, view.stock, user.language
        ),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("quantity:"))
async def choose_one(callback: CallbackQuery, service: ShopService) -> None:
    if not callback.message:
        return
    _, product_id, quantity = callback.data.split(":")  # type: ignore[union-attr]
    await show_checkout(
        callback.message, service, callback.from_user.id, int(product_id), int(quantity)
    )
    await callback.answer()


@router.callback_query(F.data.startswith("custom_quantity:"))
async def custom_quantity(callback: CallbackQuery, state: FSMContext, service: ShopService) -> None:
    product_id = int(callback.data.split(":")[1])  # type: ignore[union-attr]
    view = await service.get_product(product_id)
    user = await service.get_user(callback.from_user.id)
    if view is None or view.stock < 1:
        await callback.answer(tr(user.language, "out_of_stock"), show_alert=True)
        return
    await state.set_state(CustomerState.choosing_quantity)
    await state.update_data(product_id=product_id, max_quantity=view.stock)
    if callback.message:
        await callback.message.answer(tr(user.language, "choose_quantity", max_quantity=view.stock))
    await callback.answer()


@router.message(CustomerState.choosing_quantity, F.text)
async def receive_quantity(message: Message, state: FSMContext, service: ShopService) -> None:
    if message.from_user is None:
        return
    user = await service.get_user(message.from_user.id)
    data = await state.get_data()
    maximum = int(data["max_quantity"])
    try:
        quantity = int(message.text or "")
        if quantity < 1 or quantity > maximum:
            raise ValueError
    except ValueError:
        await message.answer(tr(user.language, "invalid_quantity", max_quantity=maximum))
        return
    await state.clear()
    await show_checkout(message, service, message.from_user.id, int(data["product_id"]), quantity)


async def show_checkout(
    message: Message, service: ShopService, telegram_id: int, product_id: int, quantity: int
) -> None:
    user = await service.get_user(telegram_id)
    view = await service.get_product(product_id)
    if view is None or quantity < 1 or quantity > view.stock:
        await message.answer(tr(user.language, "out_of_stock"))
        return
    total = view.product.price_cents * quantity
    await edit_or_answer(
        message,
        tr(
            user.language,
            "order_summary",
            name=escape(product_name(view.product, user.language)),
            quantity=quantity,
            total=format_money(total),
        ),
        reply_markup=payment_choices(product_id, quantity, total, user.language),
    )


@router.callback_query(F.data.startswith("checkout:balance:"))
async def checkout_balance(callback: CallbackQuery, service: ShopService) -> None:
    _, _, product_id, quantity = callback.data.split(":")  # type: ignore[union-attr]
    user = await service.get_user(callback.from_user.id)
    try:
        order = await service.buy_with_balance(
            callback.from_user.id, int(product_id), int(quantity)
        )
    except InsufficientBalance as exc:
        await callback.answer(
            tr(
                user.language,
                "insufficient_balance",
                needed=format_money(exc.needed_cents),
                balance=format_money(exc.balance_cents),
            ),
            show_alert=True,
        )
        return
    except NotEnoughStock:
        await callback.answer(tr(user.language, "out_of_stock"), show_alert=True)
        return
    text = tr(
        user.language,
        "purchase_complete",
        order_id=order.id,
        name=escape(product_name(order.product, user.language)),
        quantity=order.quantity,
        delivery=escape(delivery_text(order)),
    )
    if callback.message:
        await edit_or_answer(callback.message, text)
    await callback.answer()


@router.callback_query(F.data.startswith("checkout:qr:"))
async def checkout_qr(
    callback: CallbackQuery,
    state: FSMContext,
    service: ShopService,
    settings: Settings,
) -> None:
    _, _, product_id, quantity = callback.data.split(":")  # type: ignore[union-attr]
    user = await service.get_user(callback.from_user.id)
    try:
        order = await service.create_qr_order(callback.from_user.id, int(product_id), int(quantity))
    except NotEnoughStock:
        await callback.answer(tr(user.language, "out_of_stock"), show_alert=True)
        return
    await state.set_state(CustomerState.awaiting_order_proof)
    await state.update_data(order_id=order.id)
    caption = tr(
        user.language,
        "payment_caption",
        order_id=order.id,
        name=escape(product_name(order.product, user.language)),
        quantity=order.quantity,
        total=format_money(order.total_cents),
        account_name=escape(settings.payment_account_name),
        account_number=escape(settings.payment_account_number),
        minutes=settings.payment_expiry_minutes,
    )
    if callback.message:
        if settings.payment_qr_path.is_file():
            await callback.message.answer_photo(
                FSInputFile(settings.payment_qr_path), caption=caption
            )
        else:
            await callback.message.answer(
                tr(
                    user.language,
                    "payment_no_qr",
                    order_id=order.id,
                    total=format_money(order.total_cents),
                    account_name=escape(settings.payment_account_name),
                    account_number=escape(settings.payment_account_number),
                )
            )
        await callback.message.answer(tr(user.language, "send_proof"))
    await callback.answer()


@router.message(CustomerState.awaiting_order_proof, F.photo)
async def receive_order_proof(
    message: Message,
    state: FSMContext,
    service: ShopService,
    settings: Settings,
    bot: Bot,
) -> None:
    if message.from_user is None or not message.photo:
        return
    user = await service.get_user(message.from_user.id)
    data = await state.get_data()
    try:
        order = await service.submit_order_proof(
            message.from_user.id, int(data["order_id"]), message.photo[-1].file_id
        )
    except AlreadyProcessed:
        await state.clear()
        await message.answer(tr(user.language, "order_expired"))
        return
    await state.clear()
    await message.answer(tr(user.language, "proof_received", order_id=order.id))
    await notify_admins_order(bot, settings, order)


@router.message(CustomerState.awaiting_order_proof)
async def order_proof_requires_photo(message: Message, service: ShopService) -> None:
    if message.from_user:
        user = await service.get_user(message.from_user.id)
        await message.answer(tr(user.language, "proof_photo_only"))


@router.message(F.text.in_(MENU_KEYS["menu_balance"]))
async def show_balance(message: Message, state: FSMContext, service: ShopService) -> None:
    await state.clear()
    if message.from_user:
        user = await service.get_user(message.from_user.id)
        await message.answer(tr(user.language, "balance", balance=format_money(user.balance_cents)))


@router.message(F.text.in_(MENU_KEYS["menu_contact"]))
async def show_contact(
    message: Message, state: FSMContext, service: ShopService, settings: Settings
) -> None:
    await state.clear()
    if message.from_user:
        user = await service.get_user(message.from_user.id)
        await message.answer(
            tr(user.language, "contact", support=escape(settings.support_username))
        )


@router.message(F.text.in_(MENU_KEYS["menu_language"]))
async def show_language(message: Message, state: FSMContext, service: ShopService) -> None:
    await state.clear()
    if message.from_user:
        user = await service.get_user(message.from_user.id)
        await message.answer(tr(user.language, "language"), reply_markup=language_choices())


@router.callback_query(F.data.startswith("language:"))
async def change_language(callback: CallbackQuery, service: ShopService) -> None:
    language = Language(callback.data.split(":")[1])  # type: ignore[union-attr]
    user = await service.set_language(callback.from_user.id, language)
    if callback.message:
        await callback.message.answer(
            tr(user.language, "language_changed"), reply_markup=main_menu(user.language)
        )
        await show_shop(callback.message, service, user.language, edit=False)
    await callback.answer()


@router.message(F.text.in_(MENU_KEYS["menu_orders"]))
async def show_orders(message: Message, state: FSMContext, service: ShopService) -> None:
    await state.clear()
    if message.from_user is None:
        return
    user = await service.get_user(message.from_user.id)
    orders = await service.list_orders(message.from_user.id)
    if not orders:
        await message.answer(tr(user.language, "orders_empty"))
        return
    lines = []
    for order in orders:
        status_key = f"status_{order.status}"
        lines.append(
            tr(
                user.language,
                "order_line",
                id=order.id,
                name=escape(product_name(order.product, user.language)),
                quantity=order.quantity,
                total=format_money(order.total_cents),
                status=tr(user.language, status_key),
                date=order.created_at.strftime("%Y-%m-%d %H:%M"),
            )
        )
    await message.answer(tr(user.language, "orders", orders="\n\n".join(lines)))


@router.message(F.text.in_(MENU_KEYS["menu_deposit"]))
async def begin_deposit(message: Message, state: FSMContext, service: ShopService) -> None:
    await state.clear()
    if message.from_user:
        user = await service.get_user(message.from_user.id)
        await state.set_state(CustomerState.entering_deposit_amount)
        await message.answer(tr(user.language, "deposit_prompt"))


@router.message(CustomerState.entering_deposit_amount, F.text)
async def receive_deposit_amount(
    message: Message,
    state: FSMContext,
    service: ShopService,
    settings: Settings,
) -> None:
    if message.from_user is None:
        return
    user = await service.get_user(message.from_user.id)
    try:
        amount_cents = parse_money(message.text or "")
    except ValueError:
        await message.answer(tr(user.language, "deposit_amount_invalid"))
        return
    deposit = await service.create_deposit(message.from_user.id, amount_cents)
    if settings.auto_topup_enabled:
        await state.clear()
        caption = tr(
            user.language,
            "deposit_auto_caption",
            deposit_id=deposit.id,
            requested_amount=format_money(deposit.requested_amount_cents),
            amount=format_money(deposit.amount_cents),
            account_name=escape(settings.payment_account_name),
            account_number=escape(settings.payment_account_number),
            minutes=settings.payment_expiry_minutes,
        )
        reply_markup = deposit_payment_actions(deposit.id, user.language)
    else:
        await state.set_state(CustomerState.awaiting_deposit_proof)
        await state.update_data(deposit_id=deposit.id)
        caption = tr(
            user.language,
            "deposit_caption",
            deposit_id=deposit.id,
            amount=format_money(deposit.amount_cents),
            account_name=escape(settings.payment_account_name),
            account_number=escape(settings.payment_account_number),
        )
        reply_markup = None
    if settings.payment_qr_path.is_file():
        await message.answer_photo(
            FSInputFile(settings.payment_qr_path), caption=caption, reply_markup=reply_markup
        )
    else:
        await message.answer(caption, reply_markup=reply_markup)


@router.callback_query(F.data.regexp(r"^deposit:proof:\d+$"))
async def request_deposit_proof(
    callback: CallbackQuery, state: FSMContext, service: ShopService
) -> None:
    deposit_id = int(callback.data.rsplit(":", 1)[1])  # type: ignore[union-attr]
    deposit = await service.get_user_deposit(callback.from_user.id, deposit_id)
    if deposit is None:
        await callback.answer("Deposit not found.", show_alert=True)
        return
    if deposit.status == DepositStatus.APPROVED.value:
        await callback.answer(
            tr(deposit.user.language, "topup_already_approved"),  # type: ignore[union-attr]
            show_alert=True,
        )
        return
    if (
        deposit.status != DepositStatus.AWAITING_PROOF.value
        or (deposit.expires_at is not None and deposit.expires_at <= utc_now())
    ):
        await callback.answer(
            tr(deposit.user.language, "topup_not_pending"),  # type: ignore[union-attr]
            show_alert=True,
        )
        return
    await state.set_state(CustomerState.awaiting_deposit_proof)
    await state.update_data(deposit_id=deposit.id)
    if callback.message:
        await callback.message.answer(
            tr(deposit.user.language, "send_proof")  # type: ignore[union-attr]
        )
    await callback.answer()


@router.message(CustomerState.awaiting_deposit_proof, F.photo)
async def receive_deposit_proof(
    message: Message,
    state: FSMContext,
    service: ShopService,
    settings: Settings,
    bot: Bot,
) -> None:
    if message.from_user is None or not message.photo:
        return
    user = await service.get_user(message.from_user.id)
    data = await state.get_data()
    try:
        deposit = await service.submit_deposit_proof(
            message.from_user.id, int(data["deposit_id"]), message.photo[-1].file_id
        )
    except AlreadyProcessed:
        await state.clear()
        deposit = await service.get_user_deposit(
            message.from_user.id, int(data["deposit_id"])
        )
        key = (
            "topup_already_approved"
            if deposit and deposit.status == DepositStatus.APPROVED.value
            else "already_processed"
        )
        await message.answer(tr(user.language, key))
        return
    await state.clear()
    await message.answer(tr(user.language, "deposit_received"))
    await notify_admins_deposit(bot, settings, deposit)


@router.message(CustomerState.awaiting_deposit_proof)
async def deposit_proof_requires_photo(
    message: Message, state: FSMContext, service: ShopService
) -> None:
    if message.from_user:
        user = await service.get_user(message.from_user.id)
        data = await state.get_data()
        deposit_id = data.get("deposit_id")
        if deposit_id is not None:
            deposit = await service.get_user_deposit(message.from_user.id, int(deposit_id))
            if deposit and deposit.status == DepositStatus.APPROVED.value:
                await state.clear()
                await message.answer(tr(user.language, "topup_already_approved"))
                return
        await message.answer(tr(user.language, "proof_photo_only"))


async def notify_admins_order(bot: Bot, settings: Settings, order: object) -> None:
    username = f"@{order.user.username}" if order.user.username else order.user.full_name  # type: ignore[attr-defined]
    caption = (
        f"🧾 <b>Payment review — Order #{order.id}</b>\n"  # type: ignore[attr-defined]
        f"Customer: {escape(username)} (<code>{order.user.telegram_id}</code>)\n"  # type: ignore[attr-defined]
        f"Product: {escape(order.product.name_en)} × {order.quantity}\n"  # type: ignore[attr-defined]
        f"Total: <b>{format_money(order.total_cents)}</b>"  # type: ignore[attr-defined]
    )
    for admin_id in settings.admin_ids:
        try:
            await bot.send_photo(
                admin_id,
                order.payment_proof_file_id,  # type: ignore[attr-defined]
                caption=caption,
                reply_markup=review_order(order.id),  # type: ignore[attr-defined]
            )
        except TelegramBadRequest:
            continue


async def notify_admins_deposit(bot: Bot, settings: Settings, deposit: object) -> None:
    username = (
        f"@{deposit.user.username}" if deposit.user.username else deposit.user.full_name  # type: ignore[attr-defined]
    )
    caption = (
        f"💰 <b>Deposit review — #{deposit.id}</b>\n"  # type: ignore[attr-defined]
        f"Customer: {escape(username)} (<code>{deposit.user.telegram_id}</code>)\n"  # type: ignore[attr-defined]
        f"Amount: <b>{format_money(deposit.amount_cents)}</b>"  # type: ignore[attr-defined]
    )
    for admin_id in settings.admin_ids:
        try:
            await bot.send_photo(
                admin_id,
                deposit.payment_proof_file_id,  # type: ignore[attr-defined]
                caption=caption,
                reply_markup=review_deposit(deposit.id),  # type: ignore[attr-defined]
            )
        except TelegramBadRequest:
            continue
