from types import SimpleNamespace
from unittest.mock import AsyncMock

from app.handlers.customer import create_refund_from_qr, show_balance


async def test_balance_shows_refund_button_when_money_is_available() -> None:
    message = SimpleNamespace(
        from_user=SimpleNamespace(id=123),
        answer=AsyncMock(),
    )
    state = SimpleNamespace(clear=AsyncMock())
    service = SimpleNamespace(
        get_user=AsyncMock(return_value=SimpleNamespace(language="en", balance_cents=1000)),
        pending_refund_for_user=AsyncMock(return_value=None),
    )

    await show_balance(message, state, service)

    assert "$10.00" in message.answer.await_args.args[0]
    markup = message.answer.await_args.kwargs["reply_markup"]
    assert markup.inline_keyboard[0][0].callback_data == "refund:start"


async def test_qr_submission_creates_ticket_and_sends_it_to_admin() -> None:
    refund_user = SimpleNamespace(
        telegram_id=123,
        username="refund_user",
        full_name="Refund User",
        language="en",
        balance_cents=250,
    )
    refund = SimpleNamespace(
        id=9,
        user_id=123,
        amount_cents=750,
        status="pending",
        qr_file_id="qr-photo-id",
        qr_file_type="photo",
        user=refund_user,
    )
    message = SimpleNamespace(
        from_user=SimpleNamespace(id=123),
        answer=AsyncMock(),
    )
    state = SimpleNamespace(
        get_data=AsyncMock(return_value={"refund_amount_cents": 750}),
        clear=AsyncMock(),
    )
    service = SimpleNamespace(
        get_user=AsyncMock(return_value=refund_user),
        create_refund_request=AsyncMock(return_value=refund),
    )
    settings = SimpleNamespace(admin_ids=frozenset({456}))
    bot = SimpleNamespace(send_photo=AsyncMock(), send_document=AsyncMock())

    await create_refund_from_qr(
        message,
        state,
        service,
        settings,
        bot,
        file_id="qr-photo-id",
        file_type="photo",
    )

    service.create_refund_request.assert_awaited_once_with(
        123,
        750,
        "qr-photo-id",
        qr_file_type="photo",
    )
    state.clear.assert_awaited_once()
    assert "Refund ticket #9" in message.answer.await_args.args[0]
    bot.send_photo.assert_awaited_once()
    assert bot.send_photo.await_args.args[:2] == (456, "qr-photo-id")
