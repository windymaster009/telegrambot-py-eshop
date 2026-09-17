from datetime import timedelta

import pytest
from aiogram.types import User as TelegramUser

from app.models import OrderStatus, utc_now
from app.services import (
    AlreadyProcessed,
    InsufficientBalance,
    ShopService,
    delivery_text,
)


@pytest.fixture
def telegram_user() -> TelegramUser:
    return TelegramUser(
        id=9988776655,
        is_bot=False,
        first_name="Test",
        username="shop_test_user",
    )


async def test_qr_order_is_reserved_reviewed_and_delivered(
    shop: ShopService, telegram_user: TelegramUser
) -> None:
    await shop.get_or_create_user(telegram_user)
    product = await shop.add_product("VPN 2 Months", 167, "40 days", "Two-device plan")
    assert await shop.add_stock(product.id, ["key-one", "key-two"]) == 2

    order = await shop.create_qr_order(telegram_user.id, product.id, 1)
    assert order.status == OrderStatus.AWAITING_PAYMENT.value
    assert (await shop.get_product(product.id)).stock == 1  # type: ignore[union-attr]

    await shop.submit_order_proof(telegram_user.id, order.id, "proof-file-id")
    completed = await shop.approve_order(order.id)

    assert completed.status == OrderStatus.COMPLETED.value
    assert delivery_text(completed) == "key-one"
    with pytest.raises(AlreadyProcessed):
        await shop.approve_order(order.id)


async def test_rejected_order_releases_reserved_stock(
    shop: ShopService, telegram_user: TelegramUser
) -> None:
    await shop.get_or_create_user(telegram_user)
    product = await shop.add_product("Test Product", 500, "None", "Description")
    await shop.add_stock(product.id, ["one-key"])
    order = await shop.create_qr_order(telegram_user.id, product.id, 1)
    await shop.submit_order_proof(telegram_user.id, order.id, "proof-file-id")

    rejected = await shop.reject_order(order.id, "Payment not found")

    assert rejected.status == OrderStatus.REJECTED.value
    assert (await shop.get_product(product.id)).stock == 1  # type: ignore[union-attr]


async def test_expired_order_releases_reserved_stock(
    shop: ShopService, telegram_user: TelegramUser
) -> None:
    await shop.get_or_create_user(telegram_user)
    product = await shop.add_product("Test Product", 500, "None", "Description")
    await shop.add_stock(product.id, ["one-key"])
    order = await shop.create_qr_order(telegram_user.id, product.id, 1)
    await shop.orders.update_one(
        {"_id": order.id}, {"$set": {"expires_at": utc_now() - timedelta(minutes=1)}}
    )

    assert await shop.expire_orders() == 1
    assert (await shop.get_product(product.id)).stock == 1  # type: ignore[union-attr]


async def test_deposit_then_wallet_purchase(shop: ShopService, telegram_user: TelegramUser) -> None:
    await shop.get_or_create_user(telegram_user)
    product = await shop.add_product("One Key", 250, "No warranty", "A test key")
    await shop.add_stock(product.id, ["secret-key"])

    with pytest.raises(InsufficientBalance):
        await shop.buy_with_balance(telegram_user.id, product.id, 1)

    deposit = await shop.create_deposit(telegram_user.id, 1000)
    await shop.submit_deposit_proof(telegram_user.id, deposit.id, "proof-file-id")
    await shop.approve_deposit(deposit.id)

    with pytest.raises(AlreadyProcessed):
        await shop.approve_deposit(deposit.id)

    order = await shop.buy_with_balance(telegram_user.id, product.id, 1)
    assert delivery_text(order) == "secret-key"
    assert (await shop.get_user(telegram_user.id)).balance_cents == 750
