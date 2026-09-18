from aiogram.types import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    ReplyKeyboardMarkup,
)
from aiogram.utils.keyboard import InlineKeyboardBuilder

from app.i18n import tr
from app.money import format_money


def main_menu(language: str) -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [
                KeyboardButton(text=tr(language, "menu_shop")),
                KeyboardButton(text=tr(language, "menu_balance")),
            ],
            [
                KeyboardButton(text=tr(language, "menu_deposit")),
                KeyboardButton(text=tr(language, "menu_contact")),
            ],
            [
                KeyboardButton(text=tr(language, "menu_language")),
                KeyboardButton(text=tr(language, "menu_orders")),
            ],
        ],
        resize_keyboard=True,
    )


def product_list(products: list[tuple[int, str, int, int]]) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for product_id, name, price_cents, stock in products:
        builder.button(
            text=f"{name} — {format_money(price_cents)} ({stock})",
            callback_data=f"product:{product_id}",
        )
    builder.adjust(1)
    return builder.as_markup()


def product_actions(
    product_id: int, price_cents: int, stock: int, language: str
) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    if stock:
        builder.button(
            text=tr(language, "buy_one", price=format_money(price_cents)),
            callback_data=f"quantity:{product_id}:1",
        )
        if stock > 1:
            builder.button(
                text=tr(language, "choose_qty", stock=stock),
                callback_data=f"custom_quantity:{product_id}",
            )
    builder.button(text=tr(language, "back"), callback_data="shop")
    builder.adjust(1)
    return builder.as_markup()


def payment_choices(
    product_id: int, quantity: int, total_cents: int, language: str
) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(
        text=tr(language, "pay_now", total=format_money(total_cents)),
        callback_data=f"checkout:qr:{product_id}:{quantity}",
    )
    builder.button(
        text=tr(language, "pay_balance", total=format_money(total_cents)),
        callback_data=f"checkout:balance:{product_id}:{quantity}",
    )
    builder.button(text=tr(language, "back"), callback_data=f"product:{product_id}")
    builder.adjust(1)
    return builder.as_markup()


def deposit_payment_actions(deposit_id: int, language: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=tr(language, "submit_payment_proof"),
                    callback_data=f"deposit:proof:{deposit_id}",
                )
            ],
            [
                InlineKeyboardButton(
                    text=tr(language, "cancel_topup"),
                    callback_data=f"deposit:cancel:{deposit_id}",
                )
            ],
        ]
    )


def balance_actions(
    language: str,
    balance_cents: int,
    *,
    pending_refund_id: int | None = None,
) -> InlineKeyboardMarkup | None:
    if pending_refund_id is not None:
        return None
    if balance_cents <= 0:
        return None
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=tr(language, "request_refund"),
                    callback_data="refund:start",
                )
            ]
        ]
    )


def language_choices() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="🇬🇧 English", callback_data="language:en")],
            [InlineKeyboardButton(text="🇰🇭 ភាសាខ្មែរ", callback_data="language:km")],
        ]
    )


def admin_home() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="📦 Products", callback_data="admin:products"),
                InlineKeyboardButton(text="➕ Add product", callback_data="admin:add_product"),
            ],
            [
                InlineKeyboardButton(
                    text="🧾 Pending orders", callback_data="admin:pending_orders"
                ),
                InlineKeyboardButton(text="💰 Deposits", callback_data="admin:pending_deposits"),
            ],
            [InlineKeyboardButton(text="💸 Refund tickets", callback_data="admin:pending_refunds")],
        ]
    )


def admin_products(products: list[tuple[int, str, int, bool]]) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for product_id, name, stock, active in products:
        state = "✅" if active else "⏸"
        builder.button(
            text=f"{state} #{product_id} {name} ({stock})",
            callback_data=f"admin:product:{product_id}",
        )
    builder.button(text="⬅️ Admin", callback_data="admin:home")
    builder.adjust(1)
    return builder.as_markup()


def admin_product_actions(product_id: int, active: bool) -> InlineKeyboardMarkup:
    toggle = "⏸ Disable" if active else "▶️ Enable"
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="✏️ Edit product", callback_data=f"admin:edit_product:{product_id}"
                )
            ],
            [
                InlineKeyboardButton(
                    text="➕ Add stock", callback_data=f"admin:add_stock:{product_id}"
                )
            ],
            [InlineKeyboardButton(text=toggle, callback_data=f"admin:toggle_product:{product_id}")],
            [InlineKeyboardButton(text="⬅️ Products", callback_data="admin:products")],
        ]
    )


def review_order(order_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="✅ Approve & deliver", callback_data=f"admin:approve_order:{order_id}"
                ),
                InlineKeyboardButton(
                    text="❌ Reject", callback_data=f"admin:reject_order:{order_id}"
                ),
            ]
        ]
    )


def review_deposit(deposit_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="✅ Approve", callback_data=f"admin:approve_deposit:{deposit_id}"
                ),
                InlineKeyboardButton(
                    text="❌ Reject", callback_data=f"admin:reject_deposit:{deposit_id}"
                ),
            ]
        ]
    )


def review_refund(refund_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="✅ Mark paid", callback_data=f"admin:pay_refund:{refund_id}"
                ),
                InlineKeyboardButton(
                    text="❌ Reject & restore",
                    callback_data=f"admin:reject_refund:{refund_id}",
                ),
            ]
        ]
    )
