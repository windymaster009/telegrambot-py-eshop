from datetime import timedelta

from aiogram.types import User as TelegramUser
from mongomock_motor import AsyncMongoMockClient

from app.aba_payments import AbaPayment
from app.models import DepositStatus, utc_now
from app.services import ShopService


def payment(transaction_id: str, amount_cents: int, *, currency: str = "USD") -> AbaPayment:
    return AbaPayment(
        transaction_id=transaction_id,
        amount_minor=amount_cents,
        currency=currency,
        payer_name="TEST CUSTOMER",
        payer_account="*123",
        apv="456789",
        channel="ABA PAY",
        merchant="BLINK",
        paid_at_text="Sep 17, 11:45 PM",
    )


async def make_shop() -> tuple[ShopService, AsyncMongoMockClient]:
    client = AsyncMongoMockClient()
    shop = ShopService(
        client.eshop,
        client,
        payment_expiry_minutes=30,
        topup_expiry_minutes=15,
        auto_topup_enabled=True,
        use_transactions=False,
    )
    await shop.get_or_create_user(
        TelegramUser(
            id=123456789,
            is_bot=False,
            first_name="Top-up",
            username="topup_customer",
        )
    )
    return shop, client


async def test_auto_topup_reserves_unique_amounts_and_credits_once() -> None:
    shop, client = await make_shop()
    try:
        first = await shop.create_deposit(123456789, 1000)
        same_user_retry = await shop.create_deposit(123456789, 2000)
        await shop.get_or_create_user(
            TelegramUser(
                id=987654321,
                is_bot=False,
                first_name="Second",
                username="second_customer",
            )
        )
        second = await shop.create_deposit(987654321, 1000)

        assert 1001 <= first.amount_cents <= 1099
        assert 1001 <= second.amount_cents <= 1099
        assert same_user_retry.id == first.id
        assert first.amount_cents != second.amount_cents
        assert first.expires_at is not None
        assert first.expires_at - first.created_at == timedelta(minutes=15)

        matched = await shop.process_aba_topup(payment("trx-one", first.amount_cents))

        assert matched.duplicate is False
        assert matched.deposit is not None
        assert matched.deposit.status == DepositStatus.APPROVED.value
        assert matched.deposit.aba_transaction_id == "trx-one"
        assert (await shop.get_user(123456789)).balance_cents == first.amount_cents
        event = await shop.payment_events.find_one({"_id": "trx-one"})
        assert event is not None
        assert event["payer_name"] == "TEST CUSTOMER"
        assert event["paid_at_text"] == "Sep 17, 11:45 PM"
        assert event["apv"] == "456789"

        duplicate = await shop.process_aba_topup(payment("trx-one", first.amount_cents))

        assert duplicate.duplicate is True
        assert duplicate.deposit is not None
        assert (await shop.get_user(123456789)).balance_cents == first.amount_cents
    finally:
        client.close()


async def test_wrong_amount_and_currency_do_not_credit_wallet() -> None:
    shop, client = await make_shop()
    try:
        deposit = await shop.create_deposit(123456789, 2000)

        no_amount_match = await shop.process_aba_topup(
            payment("trx-wrong-amount", deposit.amount_cents + 1)
        )
        khr_payment = await shop.process_aba_topup(
            payment("trx-khr", deposit.amount_cents, currency="KHR")
        )

        assert no_amount_match.deposit is None
        assert khr_payment.deposit is None
        assert (await shop.get_user(123456789)).balance_cents == 0
    finally:
        client.close()


async def test_expired_queue_does_not_match_and_old_event_cannot_be_replayed() -> None:
    shop, client = await make_shop()
    try:
        deposit = await shop.create_deposit(123456789, 3000)
        await shop.deposits.update_one(
            {"_id": deposit.id},
            {"$set": {"expires_at": utc_now() - timedelta(seconds=1)}},
        )

        first_seen = await shop.process_aba_topup(payment("trx-expired", deposit.amount_cents))
        await shop.deposits.update_one(
            {"_id": deposit.id},
            {"$set": {"expires_at": utc_now() + timedelta(minutes=30)}},
        )
        replay = await shop.process_aba_topup(payment("trx-expired", deposit.amount_cents))

        assert first_seen.deposit is None
        assert replay.duplicate is True
        assert replay.deposit is None
        assert (
            await shop.get_user_deposit(123456789, deposit.id)
        ).status == DepositStatus.EXPIRED.value
        assert (await shop.get_user(123456789)).balance_cents == 0
    finally:
        client.close()


async def test_customer_can_cancel_unpaid_queue_and_create_another() -> None:
    shop, client = await make_shop()
    try:
        deposit = await shop.create_deposit(123456789, 100)

        cancelled = await shop.cancel_deposit(123456789, deposit.id)

        assert cancelled.status == DepositStatus.CANCELLED.value
        result = await shop.process_aba_topup(payment("trx-cancelled", deposit.amount_cents))
        assert result.deposit is None
        assert (await shop.get_user(123456789)).balance_cents == 0

        replacement = await shop.create_deposit(123456789, 100)
        assert replacement.id != deposit.id
    finally:
        client.close()


async def test_new_fifteen_minute_policy_expires_older_existing_queue() -> None:
    shop, client = await make_shop()
    try:
        deposit = await shop.create_deposit(123456789, 500)
        await shop.deposits.update_one(
            {"_id": deposit.id},
            {
                "$set": {
                    "created_at": utc_now() - timedelta(minutes=16),
                    "expires_at": utc_now() + timedelta(minutes=14),
                }
            },
        )

        assert await shop.expire_deposits() == 1
        expired = await shop.get_user_deposit(123456789, deposit.id)
        assert expired is not None
        assert expired.status == DepositStatus.EXPIRED.value
    finally:
        client.close()
