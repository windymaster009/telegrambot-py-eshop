from pathlib import Path

import pytest
from aiogram.types import User as TelegramUser

from app.config import Settings
from app.database import Database
from app.models import OrderStatus
from app.services import InsufficientBalance, ShopService, delivery_text


@pytest.fixture
async def shop(tmp_path: Path):
    settings = Settings(
        bot_token="123456:TEST_TOKEN",
        database_url=f"sqlite+aiosqlite:///{tmp_path / 'test.db'}",
    )
    database = Database(settings)
    await database.create_schema()
    yield ShopService(database.sessions, payment_expiry_minutes=30)
    await database.close()


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


async def test_deposit_then_wallet_purchase(shop: ShopService, telegram_user: TelegramUser) -> None:
    await shop.get_or_create_user(telegram_user)
    product = await shop.add_product("One Key", 250, "No warranty", "A test key")
    await shop.add_stock(product.id, ["secret-key"])

    with pytest.raises(InsufficientBalance):
        await shop.buy_with_balance(telegram_user.id, product.id, 1)

    deposit = await shop.create_deposit(telegram_user.id, 1000)
    await shop.submit_deposit_proof(telegram_user.id, deposit.id, "proof-file-id")
    await shop.approve_deposit(deposit.id)

    order = await shop.buy_with_balance(telegram_user.id, product.id, 1)
    assert delivery_text(order) == "secret-key"
    assert (await shop.get_user(telegram_user.id)).balance_cents == 750
