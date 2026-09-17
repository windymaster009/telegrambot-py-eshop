from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, TypeVar

from aiogram.types import User as TelegramUser
from pymongo import ASCENDING, DESCENDING, ReturnDocument

from app.models import (
    Deposit,
    DepositStatus,
    Language,
    Order,
    OrderStatus,
    PaymentMethod,
    Product,
    StockItem,
    User,
    utc_now,
)

Document = dict[str, Any]
T = TypeVar("T")


class ShopError(Exception):
    pass


class NotEnoughStock(ShopError):
    pass


class InsufficientBalance(ShopError):
    def __init__(self, balance_cents: int, needed_cents: int) -> None:
        self.balance_cents = balance_cents
        self.needed_cents = needed_cents
        super().__init__("Insufficient balance")


class AlreadyProcessed(ShopError):
    pass


@dataclass(frozen=True)
class ProductView:
    product: Product
    stock: int


class ShopService:
    def __init__(
        self,
        database: Any,
        client: Any,
        payment_expiry_minutes: int,
        *,
        use_transactions: bool = True,
    ) -> None:
        self.database = database
        self.client = client
        self.payment_expiry_minutes = payment_expiry_minutes
        self.use_transactions = use_transactions

        self.users = database["users"]
        self.products = database["products"]
        self.stock_items = database["stock_items"]
        self.orders = database["orders"]
        self.deposits = database["deposits"]
        self.counters = database["counters"]

    async def get_or_create_user(self, telegram_user: TelegramUser) -> User:
        now = utc_now()
        full_name = telegram_user.full_name[:160] or str(telegram_user.id)
        document = await self.users.find_one_and_update(
            {"_id": telegram_user.id},
            {
                "$set": {
                    "telegram_id": telegram_user.id,
                    "username": telegram_user.username,
                    "full_name": full_name,
                    "updated_at": now,
                },
                "$setOnInsert": {
                    "language": Language.EN.value,
                    "balance_cents": 0,
                    "created_at": now,
                },
            },
            upsert=True,
            return_document=ReturnDocument.AFTER,
        )
        if document is None:
            raise ShopError("Could not create user")
        return self._user_from_document(document)

    async def set_language(self, telegram_id: int, language: Language) -> User:
        document = await self.users.find_one_and_update(
            {"_id": telegram_id},
            {"$set": {"language": language.value, "updated_at": utc_now()}},
            return_document=ReturnDocument.AFTER,
        )
        if document is None:
            raise ShopError("User not found. Send /start first.")
        return self._user_from_document(document)

    async def get_user(self, telegram_id: int) -> User:
        document = await self.users.find_one({"_id": telegram_id})
        if document is None:
            raise ShopError("User not found. Send /start first.")
        return self._user_from_document(document)

    async def list_users(self, limit: int = 100) -> list[User]:
        documents = await (
            self.users.find().sort("created_at", DESCENDING).limit(limit).to_list(length=limit)
        )
        return [self._user_from_document(document) for document in documents]

    async def list_products(self, *, include_inactive: bool = False) -> list[ProductView]:
        query: Document = {} if include_inactive else {"active": True}
        documents = await self.products.find(query).sort("_id", ASCENDING).to_list(length=None)
        views: list[ProductView] = []
        for document in documents:
            product_id = int(document["_id"])
            views.append(
                ProductView(
                    product=self._product_from_document(document),
                    stock=await self._available_stock(product_id),
                )
            )
        return views

    async def get_product(
        self, product_id: int, *, include_inactive: bool = False
    ) -> ProductView | None:
        query: Document = {"_id": product_id}
        if not include_inactive:
            query["active"] = True
        document = await self.products.find_one(query)
        if document is None:
            return None
        return ProductView(
            product=self._product_from_document(document),
            stock=await self._available_stock(product_id),
        )

    async def buy_with_balance(self, telegram_id: int, product_id: int, quantity: int) -> Order:
        if quantity < 1:
            raise NotEnoughStock

        async def operation(session: Any) -> Order:
            user_document = await self.users.find_one({"_id": telegram_id}, session=session)
            if user_document is None:
                raise ShopError("User not found. Send /start first.")
            product_document = await self.products.find_one(
                {"_id": product_id, "active": True}, session=session
            )
            if product_document is None:
                raise NotEnoughStock

            total = int(product_document["price_cents"]) * quantity
            balance = int(user_document.get("balance_cents", 0))
            if balance < total:
                raise InsufficientBalance(balance, total)

            order_id = (await self._next_ids("orders", session=session))[0]
            now = utc_now()
            await self._reserve_stock(
                product_id,
                quantity,
                order_id,
                sold_at=now,
                session=session,
            )
            update = await self.users.update_one(
                {"_id": telegram_id, "balance_cents": {"$gte": total}},
                {"$inc": {"balance_cents": -total}, "$set": {"updated_at": now}},
                session=session,
            )
            if update.modified_count != 1:
                current = await self.users.find_one({"_id": telegram_id}, session=session)
                raise InsufficientBalance(int((current or {}).get("balance_cents", 0)), total)

            await self.orders.insert_one(
                {
                    "_id": order_id,
                    "user_id": telegram_id,
                    "product_id": product_id,
                    "quantity": quantity,
                    "unit_price_cents": int(product_document["price_cents"]),
                    "total_cents": total,
                    "payment_method": PaymentMethod.BALANCE.value,
                    "status": OrderStatus.COMPLETED.value,
                    "payment_proof_file_id": None,
                    "admin_note": None,
                    "expires_at": None,
                    "created_at": now,
                    "completed_at": now,
                },
                session=session,
            )
            return await self._load_order(order_id, session=session)

        return await self._in_transaction(operation)

    async def create_qr_order(self, telegram_id: int, product_id: int, quantity: int) -> Order:
        if quantity < 1:
            raise NotEnoughStock

        async def operation(session: Any) -> Order:
            if await self.users.find_one({"_id": telegram_id}, session=session) is None:
                raise ShopError("User not found. Send /start first.")
            product_document = await self.products.find_one(
                {"_id": product_id, "active": True}, session=session
            )
            if product_document is None:
                raise NotEnoughStock

            order_id = (await self._next_ids("orders", session=session))[0]
            await self._reserve_stock(product_id, quantity, order_id, session=session)
            now = utc_now()
            await self.orders.insert_one(
                {
                    "_id": order_id,
                    "user_id": telegram_id,
                    "product_id": product_id,
                    "quantity": quantity,
                    "unit_price_cents": int(product_document["price_cents"]),
                    "total_cents": int(product_document["price_cents"]) * quantity,
                    "payment_method": PaymentMethod.QR.value,
                    "status": OrderStatus.AWAITING_PAYMENT.value,
                    "payment_proof_file_id": None,
                    "admin_note": None,
                    "expires_at": now + timedelta(minutes=self.payment_expiry_minutes),
                    "created_at": now,
                    "completed_at": None,
                },
                session=session,
            )
            return await self._load_order(order_id, session=session)

        return await self._in_transaction(operation)

    async def submit_order_proof(self, telegram_id: int, order_id: int, file_id: str) -> Order:
        async def operation(session: Any) -> Order | None:
            order_document = await self.orders.find_one(
                {
                    "_id": order_id,
                    "user_id": telegram_id,
                    "status": OrderStatus.AWAITING_PAYMENT.value,
                },
                session=session,
            )
            if order_document is None:
                return None

            now = utc_now()
            expires_at = self._datetime(order_document.get("expires_at"))
            if expires_at is not None and expires_at < now:
                await self._release_stock(order_id, session=session)
                await self.orders.update_one(
                    {"_id": order_id, "status": OrderStatus.AWAITING_PAYMENT.value},
                    {"$set": {"status": OrderStatus.CANCELLED.value}},
                    session=session,
                )
                return None

            updated = await self.orders.find_one_and_update(
                {"_id": order_id, "status": OrderStatus.AWAITING_PAYMENT.value},
                {
                    "$set": {
                        "payment_proof_file_id": file_id,
                        "status": OrderStatus.AWAITING_REVIEW.value,
                    }
                },
                return_document=ReturnDocument.AFTER,
                session=session,
            )
            if updated is None:
                return None
            return await self._load_order(order_id, session=session)

        order = await self._in_transaction(operation)
        if order is None:
            raise AlreadyProcessed
        return order

    async def list_orders(self, telegram_id: int, limit: int = 10) -> list[Order]:
        if await self.users.find_one({"_id": telegram_id}) is None:
            raise ShopError("User not found. Send /start first.")
        documents = await (
            self.orders.find({"user_id": telegram_id})
            .sort("created_at", DESCENDING)
            .limit(limit)
            .to_list(length=limit)
        )
        return [await self._load_order(int(document["_id"])) for document in documents]

    async def create_deposit(self, telegram_id: int, amount_cents: int) -> Deposit:
        if amount_cents <= 0:
            raise ShopError("Deposit amount must be positive")
        if await self.users.find_one({"_id": telegram_id}) is None:
            raise ShopError("User not found. Send /start first.")
        deposit_id = (await self._next_ids("deposits"))[0]
        await self.deposits.insert_one(
            {
                "_id": deposit_id,
                "user_id": telegram_id,
                "amount_cents": amount_cents,
                "status": DepositStatus.AWAITING_PROOF.value,
                "payment_proof_file_id": None,
                "admin_note": None,
                "created_at": utc_now(),
                "reviewed_at": None,
            }
        )
        return await self._load_deposit(deposit_id)

    async def submit_deposit_proof(
        self, telegram_id: int, deposit_id: int, file_id: str
    ) -> Deposit:
        document = await self.deposits.find_one_and_update(
            {
                "_id": deposit_id,
                "user_id": telegram_id,
                "status": DepositStatus.AWAITING_PROOF.value,
            },
            {
                "$set": {
                    "payment_proof_file_id": file_id,
                    "status": DepositStatus.AWAITING_REVIEW.value,
                }
            },
            return_document=ReturnDocument.AFTER,
        )
        if document is None:
            raise AlreadyProcessed
        return await self._load_deposit(deposit_id)

    async def add_product(
        self,
        name_en: str,
        price_cents: int,
        warranty: str,
        description_en: str,
        name_km: str | None = None,
        description_km: str | None = None,
    ) -> Product:
        if price_cents <= 0:
            raise ShopError("Price must be positive")
        product_id = (await self._next_ids("products"))[0]
        now = utc_now()
        document: Document = {
            "_id": product_id,
            "name_en": name_en.strip(),
            "name_km": self._optional_text(name_km),
            "description_en": description_en.strip(),
            "description_km": self._optional_text(description_km),
            "price_cents": price_cents,
            "warranty": warranty.strip() or "No warranty",
            "active": True,
            "created_at": now,
            "updated_at": now,
        }
        await self.products.insert_one(document)
        return self._product_from_document(document)

    async def update_product(
        self,
        product_id: int,
        name_en: str,
        price_cents: int,
        warranty: str,
        description_en: str,
        name_km: str | None = None,
        description_km: str | None = None,
    ) -> Product:
        if price_cents <= 0:
            raise ShopError("Price must be positive")
        document = await self.products.find_one_and_update(
            {"_id": product_id},
            {
                "$set": {
                    "name_en": name_en.strip(),
                    "name_km": self._optional_text(name_km),
                    "description_en": description_en.strip(),
                    "description_km": self._optional_text(description_km),
                    "price_cents": price_cents,
                    "warranty": warranty.strip() or "No warranty",
                    "updated_at": utc_now(),
                }
            },
            return_document=ReturnDocument.AFTER,
        )
        if document is None:
            raise ShopError("Product not found")
        return self._product_from_document(document)

    async def add_stock(self, product_id: int, contents: list[str]) -> int:
        cleaned = [item.strip() for item in contents if item.strip()]
        if not cleaned:
            return 0
        if await self.products.find_one({"_id": product_id}) is None:
            raise ShopError("Product not found")
        identifiers = await self._next_ids("stock_items", len(cleaned))
        now = utc_now()
        await self.stock_items.insert_many(
            [
                {
                    "_id": identifier,
                    "product_id": product_id,
                    "content": content,
                    "reserved_order_id": None,
                    "sold_at": None,
                    "created_at": now,
                }
                for identifier, content in zip(identifiers, cleaned, strict=True)
            ]
        )
        return len(cleaned)

    async def toggle_product(self, product_id: int) -> Product:
        current = await self.products.find_one({"_id": product_id})
        if current is None:
            raise ShopError("Product not found")
        document = await self.products.find_one_and_update(
            {"_id": product_id},
            {"$set": {"active": not bool(current.get("active", True)), "updated_at": utc_now()}},
            return_document=ReturnDocument.AFTER,
        )
        if document is None:
            raise ShopError("Product not found")
        return self._product_from_document(document)

    async def pending_orders(self, limit: int = 20) -> list[Order]:
        return await self.admin_orders(status=OrderStatus.AWAITING_REVIEW.value, limit=limit)

    async def admin_orders(self, status: str | None = None, limit: int = 100) -> list[Order]:
        query: Document = {} if status is None else {"status": status}
        documents = await (
            self.orders.find(query)
            .sort("created_at", DESCENDING)
            .limit(limit)
            .to_list(length=limit)
        )
        return [await self._load_order(int(document["_id"])) for document in documents]

    async def approve_order(self, order_id: int) -> Order:
        async def operation(session: Any) -> Order | None:
            document = await self.orders.find_one(
                {"_id": order_id, "status": OrderStatus.AWAITING_REVIEW.value},
                session=session,
            )
            if document is None:
                return None
            stock_documents = await self.stock_items.find(
                {"reserved_order_id": order_id, "sold_at": None}, session=session
            ).to_list(length=None)
            if len(stock_documents) != int(document["quantity"]):
                raise NotEnoughStock
            now = utc_now()
            stock_update = await self.stock_items.update_many(
                {"reserved_order_id": order_id, "sold_at": None},
                {"$set": {"sold_at": now}},
                session=session,
            )
            if stock_update.modified_count != int(document["quantity"]):
                raise NotEnoughStock
            order_document = await self.orders.find_one_and_update(
                {"_id": order_id, "status": OrderStatus.AWAITING_REVIEW.value},
                {
                    "$set": {
                        "status": OrderStatus.COMPLETED.value,
                        "completed_at": now,
                    }
                },
                return_document=ReturnDocument.AFTER,
                session=session,
            )
            if order_document is None:
                return None
            return await self._load_order(order_id, session=session)

        order = await self._in_transaction(operation)
        if order is None:
            raise AlreadyProcessed
        return order

    async def reject_order(self, order_id: int, note: str | None = None) -> Order:
        async def operation(session: Any) -> Order | None:
            document = await self.orders.find_one(
                {"_id": order_id, "status": OrderStatus.AWAITING_REVIEW.value},
                session=session,
            )
            if document is None:
                return None
            await self._release_stock(order_id, session=session)
            updated = await self.orders.find_one_and_update(
                {"_id": order_id, "status": OrderStatus.AWAITING_REVIEW.value},
                {
                    "$set": {
                        "status": OrderStatus.REJECTED.value,
                        "admin_note": self._optional_text(note),
                    }
                },
                return_document=ReturnDocument.AFTER,
                session=session,
            )
            if updated is None:
                return None
            return await self._load_order(order_id, session=session)

        order = await self._in_transaction(operation)
        if order is None:
            raise AlreadyProcessed
        return order

    async def pending_deposits(self, limit: int = 20) -> list[Deposit]:
        return await self.admin_deposits(status=DepositStatus.AWAITING_REVIEW.value, limit=limit)

    async def admin_deposits(self, status: str | None = None, limit: int = 100) -> list[Deposit]:
        query: Document = {} if status is None else {"status": status}
        documents = await (
            self.deposits.find(query)
            .sort("created_at", DESCENDING)
            .limit(limit)
            .to_list(length=limit)
        )
        return [await self._load_deposit(int(document["_id"])) for document in documents]

    async def approve_deposit(self, deposit_id: int) -> Deposit:
        async def operation(session: Any) -> Deposit | None:
            document = await self.deposits.find_one(
                {"_id": deposit_id, "status": DepositStatus.AWAITING_REVIEW.value},
                session=session,
            )
            if document is None:
                return None
            now = utc_now()
            user_update = await self.users.update_one(
                {"_id": int(document["user_id"])},
                {
                    "$inc": {"balance_cents": int(document["amount_cents"])},
                    "$set": {"updated_at": now},
                },
                session=session,
            )
            if user_update.modified_count != 1:
                raise ShopError("User not found")
            updated = await self.deposits.find_one_and_update(
                {"_id": deposit_id, "status": DepositStatus.AWAITING_REVIEW.value},
                {
                    "$set": {
                        "status": DepositStatus.APPROVED.value,
                        "reviewed_at": now,
                    }
                },
                return_document=ReturnDocument.AFTER,
                session=session,
            )
            if updated is None:
                return None
            return await self._load_deposit(deposit_id, session=session)

        deposit = await self._in_transaction(operation)
        if deposit is None:
            raise AlreadyProcessed
        return deposit

    async def reject_deposit(self, deposit_id: int, note: str | None = None) -> Deposit:
        document = await self.deposits.find_one_and_update(
            {"_id": deposit_id, "status": DepositStatus.AWAITING_REVIEW.value},
            {
                "$set": {
                    "status": DepositStatus.REJECTED.value,
                    "admin_note": self._optional_text(note),
                    "reviewed_at": utc_now(),
                }
            },
            return_document=ReturnDocument.AFTER,
        )
        if document is None:
            raise AlreadyProcessed
        return await self._load_deposit(deposit_id)

    async def expire_orders(self) -> int:
        now = utc_now()
        documents = await self.orders.find(
            {
                "status": OrderStatus.AWAITING_PAYMENT.value,
                "expires_at": {"$lt": now},
            },
            {"_id": 1},
        ).to_list(length=None)
        expired = 0
        for document in documents:
            if await self._expire_order(int(document["_id"]), now):
                expired += 1
        return expired

    async def _expire_order(self, order_id: int, now: datetime) -> bool:
        async def operation(session: Any) -> bool:
            document = await self.orders.find_one(
                {
                    "_id": order_id,
                    "status": OrderStatus.AWAITING_PAYMENT.value,
                    "expires_at": {"$lt": now},
                },
                session=session,
            )
            if document is None:
                return False
            await self._release_stock(order_id, session=session)
            update = await self.orders.update_one(
                {"_id": order_id, "status": OrderStatus.AWAITING_PAYMENT.value},
                {"$set": {"status": OrderStatus.CANCELLED.value}},
                session=session,
            )
            return update.modified_count == 1

        return await self._in_transaction(operation)

    async def _available_stock(self, product_id: int, session: Any = None) -> int:
        return await self.stock_items.count_documents(
            {
                "product_id": product_id,
                "sold_at": None,
                "reserved_order_id": None,
            },
            session=session,
        )

    async def _reserve_stock(
        self,
        product_id: int,
        quantity: int,
        order_id: int,
        *,
        sold_at: datetime | None = None,
        session: Any = None,
    ) -> list[Document]:
        available_query = {
            "product_id": product_id,
            "sold_at": None,
            "reserved_order_id": None,
        }
        documents = await (
            self.stock_items.find(available_query, session=session)
            .sort("_id", ASCENDING)
            .limit(quantity)
            .to_list(length=quantity)
        )
        if len(documents) != quantity:
            raise NotEnoughStock
        identifiers = [int(document["_id"]) for document in documents]
        update = await self.stock_items.update_many(
            {
                "_id": {"$in": identifiers},
                "sold_at": None,
                "reserved_order_id": None,
            },
            {"$set": {"reserved_order_id": order_id, "sold_at": sold_at}},
            session=session,
        )
        if update.modified_count != quantity:
            raise NotEnoughStock
        for document in documents:
            document["reserved_order_id"] = order_id
            document["sold_at"] = sold_at
        return documents

    async def _release_stock(self, order_id: int, session: Any = None) -> None:
        await self.stock_items.update_many(
            {"reserved_order_id": order_id, "sold_at": None},
            {"$set": {"reserved_order_id": None}},
            session=session,
        )

    async def _next_ids(self, counter: str, count: int = 1, *, session: Any = None) -> list[int]:
        document = await self.counters.find_one_and_update(
            {"_id": counter},
            {"$inc": {"seq": count}},
            upsert=True,
            return_document=ReturnDocument.AFTER,
            session=session,
        )
        if document is None:
            raise ShopError("Could not allocate an ID")
        end = int(document["seq"])
        return list(range(end - count + 1, end + 1))

    async def _load_order(self, order_id: int, *, session: Any = None) -> Order:
        document = await self.orders.find_one({"_id": order_id}, session=session)
        if document is None:
            raise ShopError("Order not found")
        user_document = await self.users.find_one(
            {"_id": int(document["user_id"])}, session=session
        )
        product_document = await self.products.find_one(
            {"_id": int(document["product_id"])}, session=session
        )
        if user_document is None or product_document is None:
            raise ShopError("Order relation not found")
        stock_documents = (
            await self.stock_items.find({"reserved_order_id": order_id}, session=session)
            .sort("_id", ASCENDING)
            .to_list(length=None)
        )
        return self._order_from_document(
            document,
            user=self._user_from_document(user_document),
            product=self._product_from_document(product_document),
            stock_items=[self._stock_from_document(item) for item in stock_documents],
        )

    async def _load_deposit(self, deposit_id: int, *, session: Any = None) -> Deposit:
        document = await self.deposits.find_one({"_id": deposit_id}, session=session)
        if document is None:
            raise ShopError("Deposit not found")
        user_document = await self.users.find_one(
            {"_id": int(document["user_id"])}, session=session
        )
        if user_document is None:
            raise ShopError("Deposit user not found")
        return self._deposit_from_document(document, user=self._user_from_document(user_document))

    async def _in_transaction(self, operation: Callable[[Any], Awaitable[T]]) -> T:
        if not self.use_transactions:
            return await operation(None)
        async with self.client.start_session() as session:
            return await session.with_transaction(operation)

    @staticmethod
    def _user_from_document(document: Document) -> User:
        return User(
            id=int(document["_id"]),
            telegram_id=int(document["telegram_id"]),
            username=document.get("username"),
            full_name=str(document["full_name"]),
            language=str(document.get("language", Language.EN.value)),
            balance_cents=int(document.get("balance_cents", 0)),
            created_at=ShopService._datetime(document.get("created_at")) or utc_now(),
            updated_at=ShopService._datetime(document.get("updated_at")) or utc_now(),
        )

    @staticmethod
    def _product_from_document(document: Document) -> Product:
        return Product(
            id=int(document["_id"]),
            name_en=str(document["name_en"]),
            name_km=document.get("name_km"),
            description_en=str(document.get("description_en", "")),
            description_km=document.get("description_km"),
            price_cents=int(document["price_cents"]),
            warranty=str(document.get("warranty", "No warranty")),
            active=bool(document.get("active", True)),
            created_at=ShopService._datetime(document.get("created_at")) or utc_now(),
            updated_at=ShopService._datetime(document.get("updated_at")) or utc_now(),
        )

    @staticmethod
    def _stock_from_document(document: Document) -> StockItem:
        return StockItem(
            id=int(document["_id"]),
            product_id=int(document["product_id"]),
            content=str(document["content"]),
            reserved_order_id=(
                int(document["reserved_order_id"])
                if document.get("reserved_order_id") is not None
                else None
            ),
            sold_at=ShopService._datetime(document.get("sold_at")),
            created_at=ShopService._datetime(document.get("created_at")) or utc_now(),
        )

    @staticmethod
    def _order_from_document(
        document: Document,
        *,
        user: User,
        product: Product,
        stock_items: list[StockItem],
    ) -> Order:
        return Order(
            id=int(document["_id"]),
            user_id=int(document["user_id"]),
            product_id=int(document["product_id"]),
            quantity=int(document["quantity"]),
            unit_price_cents=int(document["unit_price_cents"]),
            total_cents=int(document["total_cents"]),
            payment_method=str(document["payment_method"]),
            status=str(document["status"]),
            payment_proof_file_id=document.get("payment_proof_file_id"),
            admin_note=document.get("admin_note"),
            expires_at=ShopService._datetime(document.get("expires_at")),
            created_at=ShopService._datetime(document.get("created_at")) or utc_now(),
            completed_at=ShopService._datetime(document.get("completed_at")),
            user=user,
            product=product,
            stock_items=stock_items,
        )

    @staticmethod
    def _deposit_from_document(document: Document, *, user: User) -> Deposit:
        return Deposit(
            id=int(document["_id"]),
            user_id=int(document["user_id"]),
            amount_cents=int(document["amount_cents"]),
            status=str(document["status"]),
            payment_proof_file_id=document.get("payment_proof_file_id"),
            admin_note=document.get("admin_note"),
            created_at=ShopService._datetime(document.get("created_at")) or utc_now(),
            reviewed_at=ShopService._datetime(document.get("reviewed_at")),
            user=user,
        )

    @staticmethod
    def _datetime(value: object) -> datetime | None:
        if not isinstance(value, datetime):
            return None
        if value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        return value.astimezone(UTC)

    @staticmethod
    def _optional_text(value: str | None) -> str | None:
        if value is None:
            return None
        cleaned = value.strip()
        return cleaned or None


def product_name(product: Product | None, language: str) -> str:
    if product is None:
        return "Unknown product"
    if language == Language.KM.value and product.name_km:
        return product.name_km
    return product.name_en


def product_description(product: Product | None, language: str) -> str:
    if product is None:
        return ""
    if language == Language.KM.value and product.description_km:
        return product.description_km
    return product.description_en


def delivery_text(order: Order) -> str:
    return "\n\n".join(item.content for item in order.stock_items)
