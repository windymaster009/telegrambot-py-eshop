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
    )


async def make_shop() -> tuple[ShopService, AsyncMongoMockClient]:
    client = AsyncMongoMockClient()
    shop = ShopService(
        client.eshop,
        client,
        payment_expiry_minutes=30,
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

        matched = await shop.process_aba_topup(payment("trx-one", first.amount_cents))

        assert matched.duplicate is False
        assert matched.deposit is not None
        assert matched.deposit.status == DepositStatus.APPROVED.value
        assert matched.deposit.aba_transaction_id == "trx-one"
        assert (await shop.get_user(123456789)).balance_cents == first.amount_cents

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
        assert (await shop.get_user(123456789)).balance_cents == 0
    finally:
        client.close()
