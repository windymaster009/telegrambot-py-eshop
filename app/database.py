from __future__ import annotations

from typing import Any

from pymongo import ASCENDING, DESCENDING, AsyncMongoClient

from app.config import Settings


class Database:
    def __init__(self, settings: Settings) -> None:
        self.client: AsyncMongoClient[dict[str, Any]] = AsyncMongoClient(
            settings.mongo_uri.get_secret_value(),
            tz_aware=True,
            serverSelectionTimeoutMS=10_000,
            appname="windy-telegram-shop",
        )
        self.db = self.client[settings.mongo_db_name]

    async def connect(self) -> None:
        await self.ping()
        await self._create_indexes()
        await self._synchronize_counters()

    async def ping(self) -> None:
        await self.client.admin.command("ping")

    async def close(self) -> None:
        await self.client.close()

    async def _create_indexes(self) -> None:
        await self.db.users.create_index("telegram_id", unique=True)
        await self.db.users.create_index([("created_at", DESCENDING)])

        await self.db.products.create_index([("active", ASCENDING), ("_id", ASCENDING)])

        await self.db.stock_items.create_index(
            [
                ("product_id", ASCENDING),
                ("sold_at", ASCENDING),
                ("reserved_order_id", ASCENDING),
            ]
        )
        await self.db.stock_items.create_index("reserved_order_id")

        await self.db.orders.create_index([("status", ASCENDING), ("created_at", ASCENDING)])
        await self.db.orders.create_index([("user_id", ASCENDING), ("created_at", DESCENDING)])
        await self.db.orders.create_index("expires_at")

        await self.db.deposits.create_index([("status", ASCENDING), ("created_at", ASCENDING)])
        await self.db.deposits.create_index([("user_id", ASCENDING), ("created_at", DESCENDING)])
        await self.db.deposits.create_index(
            [("amount_cents", ASCENDING), ("status", ASCENDING), ("expires_at", ASCENDING)]
        )

        await self.db.payment_slots.create_index("release_at", expireAfterSeconds=0)
        await self.db.payment_events.create_index(
            [("status", ASCENDING), ("received_at", DESCENDING)]
        )
        await self.db.payment_events.create_index("matched_deposit_id")

    async def _synchronize_counters(self) -> None:
        for name in ("products", "stock_items", "orders", "deposits"):
            last = await self.db[name].find_one(sort=[("_id", DESCENDING)])
            maximum = int(last["_id"]) if last else 0
            await self.db.counters.update_one(
                {"_id": name},
                {"$max": {"seq": maximum}},
                upsert=True,
            )
