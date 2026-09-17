from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from payment_listener import (
    _listener_status,
    _notify_expired_topups_once,
    receive_aba_notification,
)


def aba_message(*, chat_id: int, username: str = "PayWayByABA_bot") -> SimpleNamespace:
    return SimpleNamespace(
        chat=SimpleNamespace(id=chat_id),
        message_id=55,
        from_user=SimpleNamespace(username=username, id=777, is_bot=True),
        sender_chat=None,
        forward_origin=None,
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


async def test_listener_ignores_wrong_group_but_scans_matching_text_from_any_sender() -> None:
    service = SimpleNamespace(
        process_aba_topup=AsyncMock(
            return_value=SimpleNamespace(duplicate=True, deposit=None)
        )
    )
    shop_bot = SimpleNamespace()
    check_bot = SimpleNamespace(send_message=AsyncMock())

    await receive_aba_notification(
        aba_message(chat_id=-100999),
        service,
        listener_settings(),
        shop_bot,
        check_bot,
    )
    human_message = aba_message(chat_id=-100777, username="payment_admin")
    human_message.from_user.is_bot = False
    await receive_aba_notification(
        human_message,
        service,
        listener_settings(),
        shop_bot,
        check_bot,
    )

    service.process_aba_topup.assert_awaited_once()
    scanned = service.process_aba_topup.await_args.args[0]
    assert scanned.amount_minor == 1007
    assert scanned.transaction_id == "123456789"
    assert scanned.payer_name == "TEST CUSTOMER"
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
    assert "TEST CUSTOMER" in confirmation
    assert "Sep 17" in confirmation
    assert "123456789" in confirmation
    assert "445566" in confirmation


async def test_listener_status_reports_group_permissions_and_text_scan_mode() -> None:
    check_bot = SimpleNamespace(
        get_me=AsyncMock(
            return_value=SimpleNamespace(
                id=123,
                username="Check_ABA_Bot",
                can_read_all_group_messages=False,
            )
        ),
        get_chat_member=AsyncMock(
            return_value=SimpleNamespace(status=SimpleNamespace(value="administrator"))
        ),
    )

    status = await _listener_status(check_bot, listener_settings())

    assert "Group-message access: <b>ready</b>" in status
    assert "Reader mode: <b>ABA text scan</b>" in status


async def test_expired_queue_sends_failure_callback_once() -> None:
    deposit = SimpleNamespace(id=12)
    service = SimpleNamespace(
        expire_deposits=AsyncMock(return_value=1),
        expired_deposits_awaiting_notification=AsyncMock(return_value=[deposit]),
        mark_expiry_notification_processed=AsyncMock(),
    )
    shop_bot = SimpleNamespace()

    with patch(
        "payment_listener.send_deposit_expired", new_callable=AsyncMock
    ) as notify_customer:
        processed = await _notify_expired_topups_once(service, shop_bot)

    assert processed == 1
    notify_customer.assert_awaited_once_with(shop_bot, deposit)
    service.mark_expiry_notification_processed.assert_awaited_once_with(
        12, delivered=True
    )
