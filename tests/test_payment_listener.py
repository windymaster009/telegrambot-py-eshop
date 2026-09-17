from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from payment_listener import receive_aba_notification


def aba_message(*, chat_id: int, username: str = "PayWayByABA_bot") -> SimpleNamespace:
    return SimpleNamespace(
        chat=SimpleNamespace(id=chat_id),
        from_user=SimpleNamespace(username=username),
        sender_chat=None,
        text=(
            "$10.07 paid by TEST CUSTOMER (*123) on Sep 17 via ABA PAY at BLINK. "
            "Trx. ID: 123456789, APV: 445566."
        ),
        caption=None,
    )


def listener_settings() -> SimpleNamespace:
    return SimpleNamespace(
        aba_payment_group_id=-100777,
        aba_payment_bot_username="paywaybyaba_bot",
    )


async def test_listener_ignores_wrong_group_and_sender() -> None:
    service = SimpleNamespace(process_aba_topup=AsyncMock())
    shop_bot = SimpleNamespace()
    check_bot = SimpleNamespace(send_message=AsyncMock())

    await receive_aba_notification(
        aba_message(chat_id=-100999),
        service,
        listener_settings(),
        shop_bot,
        check_bot,
    )
    await receive_aba_notification(
        aba_message(chat_id=-100777, username="not_the_aba_bot"),
        service,
        listener_settings(),
        shop_bot,
        check_bot,
    )

    service.process_aba_topup.assert_not_awaited()
    check_bot.send_message.assert_not_awaited()


async def test_listener_notifies_customer_and_group_after_match() -> None:
    deposit = SimpleNamespace(id=7, user_id=123, amount_cents=1007)
    service = SimpleNamespace(
        process_aba_topup=AsyncMock(
            return_value=SimpleNamespace(duplicate=False, deposit=deposit)
        )
    )
    shop_bot = SimpleNamespace()
    check_bot = SimpleNamespace(send_message=AsyncMock())

    with patch(
        "payment_listener.send_deposit_approved", new_callable=AsyncMock
    ) as notify_customer:
        await receive_aba_notification(
            aba_message(chat_id=-100777),
            service,
            listener_settings(),
            shop_bot,
            check_bot,
        )

    notify_customer.assert_awaited_once_with(shop_bot, deposit)
    confirmation = check_bot.send_message.await_args.args[1]
    assert "TOPUP-7" in confirmation
    assert "$10.07" in confirmation
    assert "123456789" in confirmation
