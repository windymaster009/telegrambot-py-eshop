from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from aiogram.types import User as TelegramUser

from app.api import create_app
from app.config import Settings
from app.models import RefundStatus
from app.services import (
    ActiveRefundExists,
    AlreadyProcessed,
    InsufficientBalance,
    ShopService,
)


def telegram_user(user_id: int = 9988776655) -> TelegramUser:
    return TelegramUser(
        id=user_id,
        is_bot=False,
        first_name="Refund",
        username=f"refund_{user_id}",
    )


async def fund_user(shop: ShopService, amount_cents: int = 1000) -> int:
    user = telegram_user()
    await shop.get_or_create_user(user)
    await shop.users.update_one(
        {"_id": user.id},
        {"$set": {"balance_cents": amount_cents}},
    )
    return user.id


async def test_refund_reserves_balance_and_marking_paid_does_not_debit_twice(
    shop: ShopService,
) -> None:
    user_id = await fund_user(shop)

    refund = await shop.create_refund_request(user_id, 800, "customer-qr")

    assert refund.status == RefundStatus.PENDING.value
    assert refund.qr_file_id == "customer-qr"
    assert refund.user is not None
    assert refund.user.balance_cents == 200
    assert (await shop.get_user(user_id)).balance_cents == 200

    paid = await shop.mark_refund_paid(refund.id, reviewed_by=123456789)

    assert paid.status == RefundStatus.PAID.value
    assert paid.reviewed_by == 123456789
    assert (await shop.get_user(user_id)).balance_cents == 200
    with pytest.raises(AlreadyProcessed):
        await shop.mark_refund_paid(refund.id, reviewed_by=123456789)


async def test_rejected_refund_restores_balance_exactly_once(
    shop: ShopService,
) -> None:
    user_id = await fund_user(shop)
    rejected_request = await shop.create_refund_request(user_id, 700, "reject-qr")

    rejected = await shop.reject_refund(
        rejected_request.id,
        "QR could not be scanned",
        reviewed_by=123456789,
    )

    assert rejected.status == RefundStatus.REJECTED.value
    assert rejected.admin_note == "QR could not be scanned"
    assert rejected.user is not None
    assert rejected.user.balance_cents == 1000
    with pytest.raises(AlreadyProcessed):
        await shop.reject_refund(rejected.id)
    assert (await shop.get_user(user_id)).balance_cents == 1000


async def test_only_one_pending_refund_and_no_overdraw(shop: ShopService) -> None:
    user_id = await fund_user(shop)
    pending = await shop.create_refund_request(user_id, 300, "first-qr")

    with pytest.raises(ActiveRefundExists) as active:
        await shop.create_refund_request(user_id, 100, "second-qr")

    assert active.value.refund_id == pending.id
    await shop.reject_refund(pending.id)

    with pytest.raises(InsufficientBalance) as insufficient:
        await shop.create_refund_request(user_id, 1001, "too-much-qr")

    assert insufficient.value.balance_cents == 1000
    assert insufficient.value.needed_cents == 1001


async def test_refund_admin_api_is_protected_and_can_mark_ticket_paid(
    shop: ShopService,
) -> None:
    user_id = await fund_user(shop)
    refund = await shop.create_refund_request(user_id, 500, "api-qr")
    settings = Settings(
        _env_file=None,
        bot_token="123456:TEST_TOKEN",
        admin_ids="123456789",
        mongo_uri="mongodb://localhost:27017",
        mongo_db_name="eshop",
        api_key="test-api-key-that-is-at-least-32-characters",
    )
    bot = SimpleNamespace(send_message=AsyncMock())
    app = create_app(settings=settings, service=shop, bot=bot)
    transport = httpx.ASGITransport(app=app)
    headers = {"X-API-Key": settings.api_key.get_secret_value()}

    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            unauthorized = await client.get("/api/v1/admin/refunds")
            listed = await client.get("/api/v1/admin/refunds", headers=headers)
            paid = await client.post(
                f"/api/v1/admin/refunds/{refund.id}/paid",
                headers=headers,
            )
            duplicate = await client.post(
                f"/api/v1/admin/refunds/{refund.id}/paid",
                headers=headers,
            )

    assert unauthorized.status_code == 401
    assert listed.status_code == 200
    assert listed.json()[0]["qr_file_id"] == "api-qr"
    assert paid.status_code == 200
    assert paid.json()["refund"]["status"] == RefundStatus.PAID.value
    assert paid.json()["notification_sent"] is True
    assert duplicate.status_code == 409
    bot.send_message.assert_awaited_once()
