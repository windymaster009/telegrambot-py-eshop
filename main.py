import asyncio
import contextlib
import logging

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.fsm.storage.memory import MemoryStorage

from app.config import get_settings
from app.database import Database
from app.handlers import admin, customer
from app.services import ShopService


async def expire_orders_loop(service: ShopService) -> None:
    while True:
        try:
            expired = await service.expire_orders()
            if expired:
                logging.info("Expired %s unpaid order(s)", expired)
            expired_deposits = await service.expire_deposits()
            if expired_deposits:
                logging.info("Expired %s unpaid top-up(s)", expired_deposits)
        except Exception:
            logging.exception("Failed to expire unpaid orders or top-up queues")
        await asyncio.sleep(60)


async def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    )
    settings = get_settings()
    if settings.admin_test_mode_enabled:
        logging.warning(
            "ADMIN TEST MODE IS ENABLED: admin accounts can simulate payments without ABA"
        )
    database = Database(settings)
    await database.connect()
    service = ShopService(
        database.db,
        database.client,
        settings.payment_expiry_minutes,
        topup_expiry_minutes=settings.topup_expiry_minutes,
        auto_topup_enabled=settings.auto_topup_enabled,
    )
    bot = Bot(
        settings.bot_token.get_secret_value(),
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    dispatcher = Dispatcher(storage=MemoryStorage())
    dispatcher.include_router(admin.router)
    dispatcher.include_router(customer.router)

    expiry_task = asyncio.create_task(expire_orders_loop(service))
    try:
        await bot.delete_webhook(drop_pending_updates=False)
        await dispatcher.start_polling(bot, service=service, settings=settings)
    finally:
        expiry_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await expiry_task
        await database.close()
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())
