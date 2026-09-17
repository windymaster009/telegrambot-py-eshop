from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.config import PROJECT_ROOT
from app.handlers.customer import checkout_qr, receive_deposit_amount
from app.models import Product


def payment_settings() -> SimpleNamespace:
    return SimpleNamespace(
        payment_qr_path=PROJECT_ROOT / "assets/payment_qr.png",
        payment_account_name="BLINK Digital Shop",
        payment_account_number="KHQR",
        payment_expiry_minutes=30,
    )


@pytest.mark.asyncio
async def test_qr_checkout_sends_bundled_payment_poster() -> None:
    product = Product(
        id=1,
        name_en="Netflix",
        name_km=None,
        description_en="Netflix key",
        description_km=None,
        price_cents=500,
        warranty="30 days",
    )
    order = SimpleNamespace(id=7, product=product, quantity=2, total_cents=1000)
    message = SimpleNamespace(answer_photo=AsyncMock(), answer=AsyncMock())
    callback = SimpleNamespace(
        data="checkout:qr:1:2",
        from_user=SimpleNamespace(id=123),
        message=message,
        answer=AsyncMock(),
    )
    state = SimpleNamespace(set_state=AsyncMock(), update_data=AsyncMock())
    service = SimpleNamespace(
        get_user=AsyncMock(return_value=SimpleNamespace(language="en")),
        create_qr_order=AsyncMock(return_value=order),
    )

    await checkout_qr(callback, state, service, payment_settings())

    message.answer_photo.assert_awaited_once()
    sent_file = message.answer_photo.await_args.args[0]
    assert Path(sent_file.path) == PROJECT_ROOT / "assets/payment_qr.png"
    assert "$10.00" in message.answer_photo.await_args.kwargs["caption"]


@pytest.mark.asyncio
async def test_deposit_sends_bundled_payment_poster() -> None:
    message = SimpleNamespace(
        from_user=SimpleNamespace(id=123),
        text="12.50",
        answer_photo=AsyncMock(),
        answer=AsyncMock(),
    )
    state = SimpleNamespace(set_state=AsyncMock(), update_data=AsyncMock())
    service = SimpleNamespace(
        get_user=AsyncMock(return_value=SimpleNamespace(language="en")),
        create_deposit=AsyncMock(return_value=SimpleNamespace(id=8)),
    )

    await receive_deposit_amount(message, state, service, payment_settings())

    message.answer_photo.assert_awaited_once()
    sent_file = message.answer_photo.await_args.args[0]
    assert Path(sent_file.path) == PROJECT_ROOT / "assets/payment_qr.png"
    assert "$12.50" in message.answer_photo.await_args.kwargs["caption"]
