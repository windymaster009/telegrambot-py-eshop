from __future__ import annotations

from html import escape
from secrets import token_urlsafe

from aiogram import Bot, F, Router
from aiogram.exceptions import TelegramAPIError, TelegramBadRequest
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, FSInputFile, Message

from app.config import Settings
from app.i18n import TEXTS, tr
from app.keyboards import (
    balance_actions,
    deposit_payment_actions,
    language_choices,
    main_menu,
    payment_choices,
    product_actions,
    product_list,
    review_deposit,
    review_order,
    review_refund,
)
from app.models import DepositStatus, Language, RefundRequest, utc_now
from app.money import format_money, parse_money
from app.services import (
    ActiveRefundExists,
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
        pending = await service.pending_refund_for_user(message.from_user.id)
        text = tr(user.language, "balance", balance=format_money(user.balance_cents))
        if pending is not None:
            text += tr(
                user.language,
                "refund_pending_balance",
                refund_id=pending.id,
                amount=format_money(pending.amount_cents),
            )
        await message.answer(
            text,
            reply_markup=balance_actions(
                user.language,
                user.balance_cents,
                pending_refund_id=pending.id if pending else None,
            ),
        )


@router.callback_query(F.data == "refund:start")
async def begin_refund(callback: CallbackQuery, state: FSMContext, service: ShopService) -> None:
    await state.clear()
    user = await service.get_user(callback.from_user.id)
    pending = await service.pending_refund_for_user(callback.from_user.id)
    if pending is not None:
        await callback.answer(
            tr(user.language, "refund_active_exists", refund_id=pending.id),
            show_alert=True,
        )
        return
    if user.balance_cents <= 0:
        await callback.answer(tr(user.language, "refund_no_balance"), show_alert=True)
        return
    await state.set_state(CustomerState.entering_refund_amount)
    if callback.message:
        await callback.message.answer(
            tr(
                user.language,
                "refund_amount_prompt",
                balance=format_money(user.balance_cents),
            )
        )
    await callback.answer()


@router.message(CustomerState.entering_refund_amount, F.text)
async def receive_refund_amount(message: Message, state: FSMContext, service: ShopService) -> None:
    if message.from_user is None:
        return
    user = await service.get_user(message.from_user.id)
    pending = await service.pending_refund_for_user(message.from_user.id)
    if pending is not None:
        await state.clear()
        await message.answer(tr(user.language, "refund_active_exists", refund_id=pending.id))
        return
    try:
        amount_cents = parse_money(message.text or "")
    except ValueError:
        await message.answer(tr(user.language, "refund_amount_invalid"))
        return
    if amount_cents > user.balance_cents:
        await message.answer(
            tr(
                user.language,
                "insufficient_balance",
                needed=format_money(amount_cents),
                balance=format_money(user.balance_cents),
            )
        )
        return
    await state.set_state(CustomerState.awaiting_refund_qr)
    await state.update_data(
        refund_amount_cents=amount_cents,
        refund_test_key=token_urlsafe(18),
    )
    await message.answer(tr(user.language, "refund_send_qr", amount=format_money(amount_cents)))


async def create_refund_from_qr(
    message: Message,
    state: FSMContext,
    service: ShopService,
    settings: Settings,
    bot: Bot,
    *,
    file_id: str,
    file_type: str,
) -> None:
    if message.from_user is None:
        return
    user = await service.get_user(message.from_user.id)
    data = await state.get_data()
    amount_cents = data.get("refund_amount_cents")
    if not isinstance(amount_cents, int) or amount_cents <= 0:
        await state.clear()
        await message.answer(tr(user.language, "refund_amount_invalid"))
        return
    try:
        refund = await service.create_refund_request(
            message.from_user.id,
            amount_cents,
            file_id,
            qr_file_type=file_type,
        )
    except ActiveRefundExists as exc:
        await state.clear()
        await message.answer(tr(user.language, "refund_active_exists", refund_id=exc.refund_id))
        return
    except InsufficientBalance as exc:
        await state.clear()
        await message.answer(
            tr(
                user.language,
                "insufficient_balance",
                needed=format_money(exc.needed_cents),
                balance=format_money(exc.balance_cents),
            )
        )
        return
    await state.clear()
    if refund.user is None:
        raise RuntimeError("Refund user is not loaded")
    await message.answer(
        tr(
            refund.user.language,
            "refund_created",
            refund_id=refund.id,
            amount=format_money(refund.amount_cents),
            balance=format_money(refund.user.balance_cents),
        )
    )
    await notify_admins_refund(bot, settings, refund)


@router.message(CustomerState.awaiting_refund_qr, F.photo)
async def receive_refund_qr_photo(
    message: Message,
    state: FSMContext,
    service: ShopService,
    settings: Settings,
    bot: Bot,
) -> None:
    if not message.photo:
        return
    await create_refund_from_qr(
        message,
        state,
        service,
        settings,
        bot,
        file_id=message.photo[-1].file_id,
        file_type="photo",
    )


@router.message(CustomerState.awaiting_refund_qr, F.document)
async def receive_refund_qr_document(
    message: Message,
    state: FSMContext,
    service: ShopService,
    settings: Settings,
    bot: Bot,
) -> None:
    if message.from_user is None or message.document is None:
        return
    user = await service.get_user(message.from_user.id)
    mime_type = message.document.mime_type or ""
    file_size = message.document.file_size or 0
    if not mime_type.startswith("image/") or file_size > 10 * 1024 * 1024:
        await message.answer(tr(user.language, "refund_qr_only"))
        return
    await create_refund_from_qr(
        message,
        state,
        service,
        settings,
        bot,
        file_id=message.document.file_id,
        file_type="document",
    )


@router.message(CustomerState.awaiting_refund_qr)
async def refund_qr_requires_image(message: Message, service: ShopService) -> None:
    if message.from_user:
        user = await service.get_user(message.from_user.id)
        await message.answer(tr(user.language, "refund_qr_only"))


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
            minutes=settings.topup_expiry_minutes,
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
    if deposit.status == DepositStatus.CANCELLED.value:
        await callback.answer(
            tr(deposit.user.language, "topup_already_cancelled"),  # type: ignore[union-attr]
            show_alert=True,
        )
        return
    if deposit.status == DepositStatus.EXPIRED.value:
        await callback.answer(
            tr(deposit.user.language, "topup_expired"),  # type: ignore[union-attr]
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


@router.callback_query(F.data.regexp(r"^deposit:cancel:\d+$"))
async def cancel_deposit(
    callback: CallbackQuery, state: FSMContext, service: ShopService
) -> None:
    deposit_id = int(callback.data.rsplit(":", 1)[1])  # type: ignore[union-attr]
    deposit = await service.get_user_deposit(callback.from_user.id, deposit_id)
    if deposit is None:
        await callback.answer("Deposit not found.", show_alert=True)
        return

    language = deposit.user.language  # type: ignore[union-attr]
    try:
        deposit = await service.cancel_deposit(callback.from_user.id, deposit_id)
    except AlreadyProcessed:
        deposit = await service.get_user_deposit(callback.from_user.id, deposit_id)
        status_key = {
            DepositStatus.APPROVED.value: "topup_already_approved",
            DepositStatus.CANCELLED.value: "topup_already_cancelled",
            DepositStatus.EXPIRED.value: "topup_expired",
        }.get(deposit.status if deposit else "", "topup_not_pending")
        await callback.answer(tr(language, status_key), show_alert=True)
        return

    await state.clear()
    if callback.message:
        try:
            await callback.message.edit_reply_markup(reply_markup=None)
        except TelegramBadRequest:
            pass
        await callback.message.answer(
            tr(language, "topup_cancelled", deposit_id=deposit.id)
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


async def notify_admins_refund(bot: Bot, settings: Settings, refund: RefundRequest) -> None:
    if refund.user is None:
        raise RuntimeError("Refund user is not loaded")
    username = f"@{refund.user.username}" if refund.user.username else refund.user.full_name
    caption = (
        f"💸 <b>Refund ticket — #{refund.id}</b>\n"
        f"Customer: {escape(username)} (<code>{refund.user.telegram_id}</code>)\n"
        f"Refund amount: <b>{format_money(refund.amount_cents)}</b>\n"
        f"Available balance after hold: <b>{format_money(refund.user.balance_cents)}</b>\n\n"
        "Scan the customer's QR and transfer the money manually, then mark it paid."
    )
    for admin_id in settings.admin_ids:
        try:
            if refund.qr_file_type == "document":
                await bot.send_document(
                    admin_id,
                    refund.qr_file_id,
                    caption=caption,
                    reply_markup=review_refund(refund.id),
                )
            else:
                await bot.send_photo(
                    admin_id,
                    refund.qr_file_id,
                    caption=caption,
                    reply_markup=review_refund(refund.id),
                )
        except (TelegramAPIError, OSError):
            continue
