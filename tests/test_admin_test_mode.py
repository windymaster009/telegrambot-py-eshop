from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from aiogram.types import User as TelegramUser

from app.handlers.admin import is_test_keyword, simulate_success_for_admin
from app.models import DepositStatus, OrderStatus, RefundStatus
from app.services import AlreadyProcessed, ShopService, delivery_text
from app.states import CustomerState

ADMIN_ID = 792364367


def admin_user() -> TelegramUser:
    return TelegramUser(
        id=ADMIN_ID,
        is_bot=False,
        first_name="Admin",
        username="admin_test",
    )


def message_stub(user_id: int = ADMIN_ID) -> SimpleNamespace:
    return SimpleNamespace(
        from_user=SimpleNamespace(id=user_id),
        text="[For testing]",
        answer=AsyncMock(),
    )


def state_stub(state_name: str | None = None, **data: int) -> SimpleNamespace:
    return SimpleNamespace(
        get_state=AsyncMock(return_value=state_name),
        get_data=AsyncMock(return_value=data),
        clear=AsyncMock(),
    )


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("For testing", True),
        ("[For testing]", True),
        ("[ For testing ]", True),
        ("  FOR   TESTING  ", True),
        ("For test", False),
        ("For testing now", False),
    ],
)
def test_keyword_normalization(text: str, expected: bool) -> None:
    assert is_test_keyword(text) is expected


async def test_test_order_completes_and_consumes_real_stock(shop: ShopService) -> None:
    user = admin_user()
    await shop.get_or_create_user(user)
    product = await shop.add_product("Test Key", 250, "None", "Test product")
    await shop.add_stock(product.id, ["real-stock-key"])
    order = await shop.create_qr_order(user.id, product.id, 1)

    completed = await shop.complete_order_for_test(
        user.id,
        order.id,
        reviewed_by=user.id,
    )

    assert completed.status == OrderStatus.COMPLETED.value
    assert completed.admin_note is not None and completed.admin_note.startswith("[TEST]")
    assert delivery_text(completed) == "real-stock-key"
    with pytest.raises(AlreadyProcessed):
        await shop.complete_order_for_test(user.id, order.id, reviewed_by=user.id)


async def test_test_deposit_credits_balance_once(shop: ShopService) -> None:
    user = admin_user()
    await shop.get_or_create_user(user)
    deposit = await shop.create_deposit(user.id, 1000)

    assert (await shop.pending_deposit_for_user(user.id)).id == deposit.id  # type: ignore[union-attr]
    approved = await shop.approve_deposit_for_test(
        user.id,
        deposit.id,
        reviewed_by=user.id,
    )

    assert approved.status == DepositStatus.APPROVED.value
    assert approved.admin_note is not None and approved.admin_note.startswith("[TEST]")
    assert approved.user is not None and approved.user.balance_cents == 1000
    assert await shop.pending_deposit_for_user(user.id) is None
    with pytest.raises(AlreadyProcessed):
        await shop.approve_deposit_for_test(user.id, deposit.id, reviewed_by=user.id)
    assert (await shop.get_user(user.id)).balance_cents == 1000


async def test_test_refund_simulates_paid_without_pending_ticket(shop: ShopService) -> None:
    user = admin_user()
    await shop.get_or_create_user(user)
    await shop.users.update_one({"_id": user.id}, {"$set": {"balance_cents": 1000}})

    refund = await shop.complete_refund_for_test(
        user.id,
        750,
        reviewed_by=user.id,
        test_key="refund-test-one",
    )

    assert refund.status == RefundStatus.PAID.value
    assert refund.qr_file_type == "test"
    assert refund.admin_note is not None and refund.admin_note.startswith("[TEST]")
    assert refund.user is not None and refund.user.balance_cents == 250
    assert await shop.pending_refund_for_user(user.id) is None

    repeated = await shop.complete_refund_for_test(
        user.id,
        750,
        reviewed_by=user.id,
        test_key="refund-test-one",
    )
    assert repeated.id == refund.id
    assert (await shop.get_user(user.id)).balance_cents == 250


async def test_non_admin_cannot_use_test_keyword() -> None:
    message = message_stub(user_id=123)
    state = state_stub()
    service = SimpleNamespace(get_user=AsyncMock())
    settings = SimpleNamespace(
        admin_ids=frozenset({ADMIN_ID}),
        admin_test_mode_enabled=True,
    )

    await simulate_success_for_admin(message, state, service, settings)

    assert "Not authorized" in message.answer.await_args.args[0]
    service.get_user.assert_not_awaited()


async def test_admin_test_mode_must_be_explicitly_enabled() -> None:
    message = message_stub()
    state = state_stub()
    service = SimpleNamespace(
        get_user=AsyncMock(return_value=SimpleNamespace(language="en")),
    )
    settings = SimpleNamespace(
        admin_ids=frozenset({ADMIN_ID}),
        admin_test_mode_enabled=False,
    )

    await simulate_success_for_admin(message, state, service, settings)

    assert "ADMIN_TEST_MODE_ENABLED=true" in message.answer.await_args.args[0]
    state.get_state.assert_not_awaited()


async def test_admin_keyword_approves_latest_pending_deposit(shop: ShopService) -> None:
    user = admin_user()
    await shop.get_or_create_user(user)
    deposit = await shop.create_deposit(user.id, 500)
    message = message_stub()
    state = state_stub()
    settings = SimpleNamespace(
        admin_ids=frozenset({ADMIN_ID}),
        admin_test_mode_enabled=True,
    )

    await simulate_success_for_admin(message, state, shop, settings)

    updated = await shop.get_user_deposit(user.id, deposit.id)
    assert updated is not None and updated.status == DepositStatus.APPROVED.value
    assert updated.user is not None and updated.user.balance_cents == 500
    assert "TEST MODE" in message.answer.await_args.args[0]


async def test_admin_keyword_completes_order_from_payment_state(shop: ShopService) -> None:
    user = admin_user()
    await shop.get_or_create_user(user)
    product = await shop.add_product("Test Key", 100, "None", "Test")
    await shop.add_stock(product.id, ["delivered-test-key"])
    order = await shop.create_qr_order(user.id, product.id, 1)
    message = message_stub()
    state = state_stub(CustomerState.awaiting_order_proof.state, order_id=order.id)
    settings = SimpleNamespace(
        admin_ids=frozenset({ADMIN_ID}),
        admin_test_mode_enabled=True,
    )

    await simulate_success_for_admin(message, state, shop, settings)

    assert "delivered-test-key" in message.answer.await_args.args[0]
    state.clear.assert_awaited_once()


async def test_admin_keyword_simulates_refund_from_qr_state(shop: ShopService) -> None:
    user = admin_user()
    await shop.get_or_create_user(user)
    await shop.users.update_one({"_id": user.id}, {"$set": {"balance_cents": 1000}})
    message = message_stub()
    state = state_stub(
        CustomerState.awaiting_refund_qr.state,
        refund_amount_cents=400,
        refund_test_key="handler-refund-test",
    )
    settings = SimpleNamespace(
        admin_ids=frozenset({ADMIN_ID}),
        admin_test_mode_enabled=True,
    )

    await simulate_success_for_admin(message, state, shop, settings)

    assert "no ABA transfer was sent" in message.answer.await_args.args[0]
    assert (await shop.get_user(user.id)).balance_cents == 600
    state.clear.assert_awaited_once()
