from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, TypeVar

from aiogram.types import User as TelegramUser
from pymongo import ASCENDING, DESCENDING, ReturnDocument
from pymongo.errors import DuplicateKeyError

from app.aba_payments import AbaPayment
from app.models import (
    Deposit,
    DepositStatus,
    Language,
    Order,
    OrderStatus,
    PaymentMethod,
    Product,
    RefundRequest,
    RefundStatus,
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


class ActiveRefundExists(ShopError):
    def __init__(self, refund_id: int) -> None:
        self.refund_id = refund_id
        super().__init__("A refund request is already pending")


@dataclass(frozen=True)
class ProductView:
    product: Product
    stock: int


@dataclass(frozen=True)
class TopupMatchResult:
    transaction_id: str
    duplicate: bool
    deposit: Deposit | None


class ShopService:
    def __init__(
        self,
        database: Any,
        client: Any,
        payment_expiry_minutes: int,
        *,
        topup_expiry_minutes: int | None = None,
        auto_topup_enabled: bool = False,
        use_transactions: bool = True,
    ) -> None:
        self.database = database
        self.client = client
        self.payment_expiry_minutes = payment_expiry_minutes
        self.topup_expiry_minutes = (
            topup_expiry_minutes
            if topup_expiry_minutes is not None
            else payment_expiry_minutes
        )
        self.auto_topup_enabled = auto_topup_enabled
        self.use_transactions = use_transactions

        self.users = database["users"]
        self.products = database["products"]
        self.stock_items = database["stock_items"]
        self.orders = database["orders"]
        self.deposits = database["deposits"]
        self.refunds = database["refunds"]
        self.payment_slots = database["payment_slots"]
        self.payment_events = database["payment_events"]
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

    async def complete_order_for_test(
        self,
        telegram_id: int,
        order_id: int,
        *,
        reviewed_by: int,
    ) -> Order:
        async def operation(session: Any) -> Order | None:
            document = await self.orders.find_one(
                {
                    "_id": order_id,
                    "user_id": telegram_id,
                    "status": OrderStatus.AWAITING_PAYMENT.value,
                },
                session=session,
            )
            if document is None:
                return None

            now = utc_now()
            expires_at = self._datetime(document.get("expires_at"))
            if expires_at is not None and expires_at <= now:
                await self._release_stock(order_id, session=session)
                await self.orders.update_one(
                    {"_id": order_id, "status": OrderStatus.AWAITING_PAYMENT.value},
                    {
                        "$set": {
                            "status": OrderStatus.CANCELLED.value,
                            "admin_note": "[TEST] Order expired before simulation",
                        }
                    },
                    session=session,
                )
                return None

            stock_documents = await self.stock_items.find(
                {"reserved_order_id": order_id, "sold_at": None}, session=session
            ).to_list(length=None)
            if len(stock_documents) != int(document["quantity"]):
                raise NotEnoughStock
            stock_update = await self.stock_items.update_many(
                {"reserved_order_id": order_id, "sold_at": None},
                {"$set": {"sold_at": now}},
                session=session,
            )
            if stock_update.modified_count != int(document["quantity"]):
                raise NotEnoughStock

            updated = await self.orders.find_one_and_update(
                {
                    "_id": order_id,
                    "user_id": telegram_id,
                    "status": OrderStatus.AWAITING_PAYMENT.value,
                },
                {
                    "$set": {
                        "status": OrderStatus.COMPLETED.value,
                        "payment_proof_file_id": None,
                        "admin_note": (
                            f"[TEST] Payment simulated by Telegram admin {reviewed_by}; "
                            "no bank payment received"
                        ),
                        "expires_at": None,
                        "completed_at": now,
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
        if amount_cents > 99_999_900:
            raise ShopError("Deposit amount is too large")
        if await self.users.find_one({"_id": telegram_id}) is None:
            raise ShopError("User not found. Send /start first.")
        # Expire every stale queue before allocating a payment amount so slots
        # held by other customers can enter their short safety quarantine.
        await self.expire_deposits()
        now = utc_now()
        if self.auto_topup_enabled:
            existing = await self.deposits.find_one(
                {
                    "user_id": telegram_id,
                    "status": {
                        "$in": [
                            DepositStatus.AWAITING_PROOF.value,
                            DepositStatus.AWAITING_REVIEW.value,
                        ]
                    },
                    "expires_at": {"$gt": now},
                    "created_at": {
                        "$gt": now - timedelta(minutes=self.topup_expiry_minutes)
                    },
                },
                sort=[("created_at", DESCENDING)],
            )
            if existing is not None:
                return await self._load_deposit(int(existing["_id"]))

        deposit_id = (await self._next_ids("deposits"))[0]
        payable_cents = amount_cents
        expires_at = None
        if self.auto_topup_enabled:
            payable_cents, expires_at = await self._reserve_topup_amount(
                amount_cents, deposit_id, now
            )
        await self.deposits.insert_one(
            {
                "_id": deposit_id,
                "user_id": telegram_id,
                "requested_amount_cents": amount_cents,
                "amount_cents": payable_cents,
                "status": DepositStatus.AWAITING_PROOF.value,
                "payment_proof_file_id": None,
                "admin_note": None,
                "expires_at": expires_at,
                "aba_transaction_id": None,
                "aba_payer_name": None,
                "matched_at": None,
                "created_at": now,
                "reviewed_at": None,
            }
        )
        return await self._load_deposit(deposit_id)

    async def get_user_deposit(self, telegram_id: int, deposit_id: int) -> Deposit | None:
        await self.expire_deposits(user_id=telegram_id)
        document = await self.deposits.find_one(
            {"_id": deposit_id, "user_id": telegram_id}
        )
        if document is None:
            return None
        return await self._load_deposit(deposit_id)

    async def pending_deposit_for_user(self, telegram_id: int) -> Deposit | None:
        await self.expire_deposits(user_id=telegram_id)
        document = await self.deposits.find_one(
            {
                "user_id": telegram_id,
                "status": DepositStatus.AWAITING_PROOF.value,
                "$or": [
                    {"expires_at": None},
                    {"expires_at": {"$gt": utc_now()}},
                ],
            },
            sort=[("created_at", DESCENDING)],
        )
        if document is None:
            return None
        return await self._load_deposit(int(document["_id"]))

    async def submit_deposit_proof(
        self, telegram_id: int, deposit_id: int, file_id: str
    ) -> Deposit:
        await self.expire_deposits(user_id=telegram_id)
        document = await self.deposits.find_one_and_update(
            {
                "_id": deposit_id,
                "user_id": telegram_id,
                "status": DepositStatus.AWAITING_PROOF.value,
                "$or": [
                    {"expires_at": None},
                    {"expires_at": {"$gt": utc_now()}},
                ],
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

    async def cancel_deposit(self, telegram_id: int, deposit_id: int) -> Deposit:
        """Cancel an unpaid top-up and quarantine its amount against late payments."""
        await self.expire_deposits(user_id=telegram_id)
        now = utc_now()
        document = await self.deposits.find_one_and_update(
            {
                "_id": deposit_id,
                "user_id": telegram_id,
                "status": DepositStatus.AWAITING_PROOF.value,
                "aba_transaction_id": None,
            },
            {
                "$set": {
                    "status": DepositStatus.CANCELLED.value,
                    "admin_note": "Cancelled by customer before payment confirmation",
                    "reviewed_at": now,
                }
            },
            return_document=ReturnDocument.AFTER,
        )
        if document is None:
            raise AlreadyProcessed
        await self._quarantine_topup_slot(document, now=now)
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
        await self.expire_deposits()
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
            await self._release_topup_slot(updated, session=session)
            return await self._load_deposit(deposit_id, session=session)

        deposit = await self._in_transaction(operation)
        if deposit is None:
            raise AlreadyProcessed
        return deposit

    async def approve_deposit_for_test(
        self,
        telegram_id: int,
        deposit_id: int,
        *,
        reviewed_by: int,
    ) -> Deposit:
        await self.expire_deposits(user_id=telegram_id)

        async def operation(session: Any) -> Deposit | None:
            now = utc_now()
            document = await self.deposits.find_one(
                {
                    "_id": deposit_id,
                    "user_id": telegram_id,
                    "status": DepositStatus.AWAITING_PROOF.value,
                    "$or": [
                        {"expires_at": None},
                        {"expires_at": {"$gt": now}},
                    ],
                },
                session=session,
            )
            if document is None:
                return None

            user_update = await self.users.update_one(
                {"_id": telegram_id},
                {
                    "$inc": {"balance_cents": int(document["amount_cents"])},
                    "$set": {"updated_at": now},
                },
                session=session,
            )
            if user_update.modified_count != 1:
                raise ShopError("User not found")

            updated = await self.deposits.find_one_and_update(
                {
                    "_id": deposit_id,
                    "user_id": telegram_id,
                    "status": DepositStatus.AWAITING_PROOF.value,
                },
                {
                    "$set": {
                        "status": DepositStatus.APPROVED.value,
                        "payment_proof_file_id": None,
                        "admin_note": (
                            f"[TEST] Top-up simulated by Telegram admin {reviewed_by}; "
                            "no ABA payment received"
                        ),
                        "reviewed_at": now,
                    }
                },
                return_document=ReturnDocument.AFTER,
                session=session,
            )
            if updated is None:
                return None
            await self._release_topup_slot(updated, session=session)
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
        await self._quarantine_topup_slot(document)
        return await self._load_deposit(deposit_id)

    async def create_refund_request(
        self,
        telegram_id: int,
        amount_cents: int,
        qr_file_id: str,
        *,
        qr_file_type: str = "photo",
    ) -> RefundRequest:
        if amount_cents <= 0:
            raise ShopError("Refund amount must be positive")
        if amount_cents > 99_999_900:
            raise ShopError("Refund amount is too large")
        if not qr_file_id.strip():
            raise ShopError("A receiving QR image is required")
        if qr_file_type not in {"photo", "document"}:
            raise ShopError("Unsupported QR file type")

        async def operation(session: Any) -> RefundRequest:
            existing = await self.refunds.find_one(
                {"user_id": telegram_id, "status": RefundStatus.PENDING.value},
                session=session,
            )
            if existing is not None:
                raise ActiveRefundExists(int(existing["_id"]))

            user_document = await self.users.find_one({"_id": telegram_id}, session=session)
            if user_document is None:
                raise ShopError("User not found. Send /start first.")
            balance_cents = int(user_document.get("balance_cents", 0))
            if balance_cents < amount_cents:
                raise InsufficientBalance(balance_cents, amount_cents)

            refund_id = (await self._next_ids("refunds", session=session))[0]
            now = utc_now()
            await self.refunds.insert_one(
                {
                    "_id": refund_id,
                    "user_id": telegram_id,
                    "amount_cents": amount_cents,
                    "status": RefundStatus.PENDING.value,
                    "qr_file_id": qr_file_id.strip(),
                    "qr_file_type": qr_file_type,
                    "admin_note": None,
                    "reviewed_by": None,
                    "created_at": now,
                    "reviewed_at": None,
                },
                session=session,
            )

            updated_user = await self.users.find_one_and_update(
                {"_id": telegram_id, "balance_cents": {"$gte": amount_cents}},
                {
                    "$inc": {"balance_cents": -amount_cents},
                    "$set": {"updated_at": now},
                },
                return_document=ReturnDocument.AFTER,
                session=session,
            )
            if updated_user is None:
                await self.refunds.delete_one({"_id": refund_id}, session=session)
                current = await self.users.find_one({"_id": telegram_id}, session=session)
                current_balance = int(current.get("balance_cents", 0)) if current else 0
                raise InsufficientBalance(current_balance, amount_cents)
            return await self._load_refund(refund_id, session=session)

        try:
            return await self._in_transaction(operation)
        except DuplicateKeyError as exc:
            active = await self.refunds.find_one(
                {"user_id": telegram_id, "status": RefundStatus.PENDING.value}
            )
            raise ActiveRefundExists(int(active["_id"]) if active else 0) from exc

    async def complete_refund_for_test(
        self,
        telegram_id: int,
        amount_cents: int,
        *,
        reviewed_by: int,
        test_key: str,
    ) -> RefundRequest:
        if amount_cents <= 0:
            raise ShopError("Refund amount must be positive")
        if amount_cents > 99_999_900:
            raise ShopError("Refund amount is too large")
        cleaned_test_key = test_key.strip()
        if not cleaned_test_key or len(cleaned_test_key) > 200:
            raise ShopError("Invalid refund test key")

        async def operation(session: Any) -> RefundRequest:
            previous = await self.refunds.find_one(
                {"test_key": cleaned_test_key},
                session=session,
            )
            if previous is not None:
                if (
                    int(previous["user_id"]) != telegram_id
                    or int(previous["amount_cents"]) != amount_cents
                ):
                    raise ShopError("Refund test key does not match this request")
                return await self._load_refund(int(previous["_id"]), session=session)

            existing = await self.refunds.find_one(
                {"user_id": telegram_id, "status": RefundStatus.PENDING.value},
                session=session,
            )
            if existing is not None:
                raise ActiveRefundExists(int(existing["_id"]))

            user_document = await self.users.find_one({"_id": telegram_id}, session=session)
            if user_document is None:
                raise ShopError("User not found. Send /start first.")
            balance_cents = int(user_document.get("balance_cents", 0))
            if balance_cents < amount_cents:
                raise InsufficientBalance(balance_cents, amount_cents)

            refund_id = (await self._next_ids("refunds", session=session))[0]
            now = utc_now()
            await self.refunds.insert_one(
                {
                    "_id": refund_id,
                    "user_id": telegram_id,
                    "amount_cents": amount_cents,
                    "status": RefundStatus.PAID.value,
                    "qr_file_id": "ADMIN_TEST_KEYWORD",
                    "qr_file_type": "test",
                    "test_key": cleaned_test_key,
                    "admin_note": (
                        f"[TEST] Refund simulated by Telegram admin {reviewed_by}; "
                        "no ABA transfer sent"
                    ),
                    "reviewed_by": reviewed_by,
                    "created_at": now,
                    "reviewed_at": now,
                },
                session=session,
            )

            updated_user = await self.users.find_one_and_update(
                {"_id": telegram_id, "balance_cents": {"$gte": amount_cents}},
                {
                    "$inc": {"balance_cents": -amount_cents},
                    "$set": {"updated_at": now},
                },
                return_document=ReturnDocument.AFTER,
                session=session,
            )
            if updated_user is None:
                await self.refunds.delete_one({"_id": refund_id}, session=session)
                current = await self.users.find_one({"_id": telegram_id}, session=session)
                current_balance = int(current.get("balance_cents", 0)) if current else 0
                raise InsufficientBalance(current_balance, amount_cents)
            return await self._load_refund(refund_id, session=session)

        try:
            return await self._in_transaction(operation)
        except DuplicateKeyError as exc:
            previous = await self.refunds.find_one({"test_key": cleaned_test_key})
            if (
                previous is None
                or int(previous["user_id"]) != telegram_id
                or int(previous["amount_cents"]) != amount_cents
            ):
                raise ShopError("Refund test could not be completed") from exc
            return await self._load_refund(int(previous["_id"]))

    async def get_user_refund(self, telegram_id: int, refund_id: int) -> RefundRequest | None:
        document = await self.refunds.find_one({"_id": refund_id, "user_id": telegram_id})
        if document is None:
            return None
        return await self._load_refund(refund_id)

    async def pending_refund_for_user(self, telegram_id: int) -> RefundRequest | None:
        document = await self.refunds.find_one(
            {"user_id": telegram_id, "status": RefundStatus.PENDING.value},
            sort=[("created_at", DESCENDING)],
        )
        if document is None:
            return None
        return await self._load_refund(int(document["_id"]))

    async def list_user_refunds(self, telegram_id: int, limit: int = 10) -> list[RefundRequest]:
        documents = await (
            self.refunds.find({"user_id": telegram_id})
            .sort("created_at", DESCENDING)
            .limit(limit)
            .to_list(length=limit)
        )
        return [await self._load_refund(int(document["_id"])) for document in documents]

    async def pending_refunds(self, limit: int = 20) -> list[RefundRequest]:
        return await self.admin_refunds(status=RefundStatus.PENDING.value, limit=limit)

    async def admin_refunds(
        self, status: str | None = None, limit: int = 100
    ) -> list[RefundRequest]:
        query: Document = {} if status is None else {"status": status}
        documents = await (
            self.refunds.find(query)
            .sort("created_at", DESCENDING)
            .limit(limit)
            .to_list(length=limit)
        )
        return [await self._load_refund(int(document["_id"])) for document in documents]

    async def mark_refund_paid(
        self, refund_id: int, *, reviewed_by: int | None = None
    ) -> RefundRequest:
        document = await self.refunds.find_one_and_update(
            {"_id": refund_id, "status": RefundStatus.PENDING.value},
            {
                "$set": {
                    "status": RefundStatus.PAID.value,
                    "reviewed_by": reviewed_by,
                    "reviewed_at": utc_now(),
                }
            },
            return_document=ReturnDocument.AFTER,
        )
        if document is None:
            raise AlreadyProcessed
        return await self._load_refund(refund_id)

    async def reject_refund(
        self,
        refund_id: int,
        note: str | None = None,
        *,
        reviewed_by: int | None = None,
    ) -> RefundRequest:
        return await self._restore_refund_balance(
            refund_id,
            RefundStatus.REJECTED,
            note=note,
            reviewed_by=reviewed_by,
        )

    async def _restore_refund_balance(
        self,
        refund_id: int,
        status: RefundStatus,
        *,
        note: str | None = None,
        reviewed_by: int | None = None,
    ) -> RefundRequest:
        async def operation(session: Any) -> RefundRequest | None:
            query: Document = {
                "_id": refund_id,
                "status": RefundStatus.PENDING.value,
            }
            now = utc_now()
            document = await self.refunds.find_one_and_update(
                query,
                {
                    "$set": {
                        "status": status.value,
                        "admin_note": self._optional_text(note),
                        "reviewed_by": reviewed_by,
                        "reviewed_at": now,
                    }
                },
                return_document=ReturnDocument.AFTER,
                session=session,
            )
            if document is None:
                return None
            user_update = await self.users.update_one(
                {"_id": int(document["user_id"])},
                {
                    "$inc": {"balance_cents": int(document["amount_cents"])},
                    "$set": {"updated_at": now},
                },
                session=session,
            )
            if user_update.modified_count != 1:
                raise ShopError("Refund user not found")
            return await self._load_refund(refund_id, session=session)

        refund = await self._in_transaction(operation)
        if refund is None:
            raise AlreadyProcessed
        return refund

    async def process_aba_topup(self, payment: AbaPayment) -> TopupMatchResult:
        await self.expire_deposits()

        async def operation(session: Any) -> TopupMatchResult:
            now = utc_now()
            existing = await self.payment_events.find_one(
                {"_id": payment.transaction_id}, session=session
            )
            if existing is not None:
                if existing.get("matched_deposit_id") is not None:
                    deposit = await self._load_deposit(
                        int(existing["matched_deposit_id"]), session=session
                    )
                    return TopupMatchResult(payment.transaction_id, True, deposit)
                return TopupMatchResult(payment.transaction_id, True, None)

            await self.payment_events.insert_one(
                {
                    "_id": payment.transaction_id,
                    "status": "unmatched",
                    "currency": payment.currency,
                    "amount_minor": payment.amount_minor,
                    "payer_name": payment.payer_name,
                    "payer_account": payment.payer_account,
                    "apv": payment.apv,
                    "channel": payment.channel,
                    "merchant": payment.merchant,
                    "paid_at_text": payment.paid_at_text,
                    "received_at": now,
                    "matched_deposit_id": None,
                    "matched_at": None,
                },
                session=session,
            )

            if payment.currency != "USD":
                return TopupMatchResult(payment.transaction_id, False, None)

            deposit_document = await self.deposits.find_one(
                {
                    "amount_cents": payment.amount_minor,
                    "status": {
                        "$in": [
                            DepositStatus.AWAITING_PROOF.value,
                            DepositStatus.AWAITING_REVIEW.value,
                        ]
                    },
                    "expires_at": {"$gt": now},
                    "created_at": {
                        "$gt": now - timedelta(minutes=self.topup_expiry_minutes)
                    },
                    "aba_transaction_id": None,
                },
                sort=[("created_at", ASCENDING)],
                session=session,
            )
            if deposit_document is None:
                return TopupMatchResult(payment.transaction_id, False, None)

            deposit_id = int(deposit_document["_id"])
            updated = await self.deposits.find_one_and_update(
                {
                    "_id": deposit_id,
                    "status": {
                        "$in": [
                            DepositStatus.AWAITING_PROOF.value,
                            DepositStatus.AWAITING_REVIEW.value,
                        ]
                    },
                    "expires_at": {"$gt": now},
                    "created_at": {
                        "$gt": now - timedelta(minutes=self.topup_expiry_minutes)
                    },
                    "aba_transaction_id": None,
                },
                {
                    "$set": {
                        "status": DepositStatus.APPROVED.value,
                        "aba_transaction_id": payment.transaction_id,
                        "aba_payer_name": payment.payer_name,
                        "matched_at": now,
                        "reviewed_at": now,
                        "admin_note": "Automatically confirmed by ABA PayWay",
                    }
                },
                return_document=ReturnDocument.AFTER,
                session=session,
            )
            if updated is None:
                return TopupMatchResult(payment.transaction_id, False, None)

            user_update = await self.users.update_one(
                {"_id": int(updated["user_id"])},
                {
                    "$inc": {"balance_cents": int(updated["amount_cents"])},
                    "$set": {"updated_at": now},
                },
                session=session,
            )
            if user_update.modified_count != 1:
                raise ShopError("Deposit user not found")

            await self.payment_events.update_one(
                {"_id": payment.transaction_id, "matched_deposit_id": None},
                {
                    "$set": {
                        "status": "matched",
                        "matched_deposit_id": deposit_id,
                        "matched_at": now,
                    }
                },
                session=session,
            )
            await self._release_topup_slot(updated, session=session)
            deposit = await self._load_deposit(deposit_id, session=session)
            return TopupMatchResult(payment.transaction_id, False, deposit)

        try:
            return await self._in_transaction(operation)
        except DuplicateKeyError:
            existing = await self.payment_events.find_one({"_id": payment.transaction_id})
            if existing and existing.get("matched_deposit_id") is not None:
                deposit = await self._load_deposit(int(existing["matched_deposit_id"]))
                return TopupMatchResult(payment.transaction_id, True, deposit)
            return TopupMatchResult(payment.transaction_id, True, None)

    async def expire_deposits(self, *, user_id: int | None = None) -> int:
        now = utc_now()
        query: Document = {
            "status": DepositStatus.AWAITING_PROOF.value,
            "expires_at": {"$ne": None},
            "$or": [
                {"expires_at": {"$lte": now}},
                {
                    "created_at": {
                        "$lte": now - timedelta(minutes=self.topup_expiry_minutes)
                    }
                },
            ],
        }
        if user_id is not None:
            query["user_id"] = user_id
        documents = await self.deposits.find(query).to_list(length=None)
        expired = 0
        for document in documents:
            updated = await self.deposits.find_one_and_update(
                {
                    "_id": int(document["_id"]),
                    "status": DepositStatus.AWAITING_PROOF.value,
                    "$or": query["$or"],
                },
                {
                    "$set": {
                        "status": DepositStatus.EXPIRED.value,
                        "admin_note": (
                            "Automatic top-up expired after "
                            f"{self.topup_expiry_minutes} minutes"
                        ),
                        "reviewed_at": now,
                    }
                },
                return_document=ReturnDocument.AFTER,
            )
            if updated is None:
                continue
            await self._quarantine_topup_slot(updated, now=now)
            expired += 1
        return expired

    async def expired_deposits_awaiting_notification(
        self, limit: int = 100
    ) -> list[Deposit]:
        documents = await (
            self.deposits.find(
                {
                    "status": DepositStatus.EXPIRED.value,
                    "expiry_notification_processed_at": None,
                }
            )
            .sort("reviewed_at", ASCENDING)
            .limit(limit)
            .to_list(length=limit)
        )
        return [
            await self._load_deposit(int(document["_id"])) for document in documents
        ]

    async def mark_expiry_notification_processed(
        self, deposit_id: int, *, delivered: bool
    ) -> None:
        await self.deposits.update_one(
            {
                "_id": deposit_id,
                "status": DepositStatus.EXPIRED.value,
                "expiry_notification_processed_at": None,
            },
            {
                "$set": {
                    "expiry_notification_processed_at": utc_now(),
                    "expiry_notification_delivered": delivered,
                }
            },
        )

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

    async def _reserve_topup_amount(
        self, requested_cents: int, deposit_id: int, now: datetime
    ) -> tuple[int, datetime]:
        expires_at = now + timedelta(minutes=self.topup_expiry_minutes)
        # This is only a fail-safe TTL. Successful payments delete their slot
        # immediately; cancelled/expired queues shorten it to a 15-minute hold.
        release_at = now + timedelta(hours=24)
        for offset in range(1, 100):
            payable_cents = requested_cents + offset
            slot_id = f"USD:{payable_cents}"
            await self._reclaim_topup_slot(slot_id, now)
            try:
                await self.payment_slots.insert_one(
                    {
                        "_id": slot_id,
                        "deposit_id": deposit_id,
                        "expires_at": expires_at,
                        "release_at": release_at,
                    }
                )
            except DuplicateKeyError:
                continue
            return payable_cents, expires_at
        raise ShopError("Too many pending top-ups for this amount. Please try again later.")

    async def _release_topup_slot(
        self, deposit_document: Document, *, session: Any = None
    ) -> None:
        """Immediately make a successfully paid amount available for reuse."""
        await self.payment_slots.delete_one(
            {
                "_id": f'USD:{int(deposit_document["amount_cents"])}',
                "deposit_id": int(deposit_document["_id"]),
            },
            session=session,
        )

    async def _quarantine_topup_slot(
        self,
        deposit_document: Document,
        *,
        now: datetime | None = None,
        session: Any = None,
    ) -> None:
        """Keep a failed queue's amount briefly so a late ABA message cannot hit a new queue."""
        reference_time = now or utc_now()
        release_at = reference_time + timedelta(minutes=self.topup_expiry_minutes)
        await self.payment_slots.update_one(
            {
                "_id": f'USD:{int(deposit_document["amount_cents"])}',
                "deposit_id": int(deposit_document["_id"]),
                "$or": [
                    {"release_at": {"$gt": release_at}},
                    {"release_at": {"$exists": False}},
                ],
            },
            {"$set": {"release_at": release_at}},
            session=session,
        )

    async def _reclaim_topup_slot(self, slot_id: str, now: datetime) -> None:
        """Remove a reusable slot, including slots left by older deployed versions."""
        slot = await self.payment_slots.find_one({"_id": slot_id})
        if slot is None:
            return

        deposit_id = slot.get("deposit_id")
        release_at = self._datetime(slot.get("release_at"))
        if release_at is not None and release_at <= now:
            await self.payment_slots.delete_one(
                {"_id": slot_id, "deposit_id": deposit_id}
            )
            return

        deposit_document = await self.deposits.find_one({"_id": deposit_id})
        if deposit_document is None:
            await self.payment_slots.delete_one(
                {"_id": slot_id, "deposit_id": deposit_id}
            )
            return

        if deposit_document.get("status") == DepositStatus.APPROVED.value:
            await self._release_topup_slot(deposit_document)
            return

        if deposit_document.get("status") in {
            DepositStatus.CANCELLED.value,
            DepositStatus.EXPIRED.value,
            DepositStatus.REJECTED.value,
        }:
            terminal_at = (
                self._datetime(deposit_document.get("reviewed_at"))
                or self._datetime(deposit_document.get("expires_at"))
                or self._datetime(deposit_document.get("created_at"))
            )
            if terminal_at is None:
                return
            safe_release_at = terminal_at + timedelta(
                minutes=self.topup_expiry_minutes
            )
            if safe_release_at <= now:
                await self.payment_slots.delete_one(
                    {"_id": slot_id, "deposit_id": deposit_id}
                )
                return
            if release_at is None or release_at > safe_release_at:
                await self.payment_slots.update_one(
                    {"_id": slot_id, "deposit_id": deposit_id},
                    {"$set": {"release_at": safe_release_at}},
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

    async def _load_refund(self, refund_id: int, *, session: Any = None) -> RefundRequest:
        document = await self.refunds.find_one({"_id": refund_id}, session=session)
        if document is None:
            raise ShopError("Refund request not found")
        user_document = await self.users.find_one(
            {"_id": int(document["user_id"])}, session=session
        )
        if user_document is None:
            raise ShopError("Refund user not found")
        return self._refund_from_document(
            document,
            user=self._user_from_document(user_document),
        )

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
            requested_amount_cents=int(
                document.get("requested_amount_cents", document["amount_cents"])
            ),
            payment_proof_file_id=document.get("payment_proof_file_id"),
            admin_note=document.get("admin_note"),
            expires_at=ShopService._datetime(document.get("expires_at")),
            aba_transaction_id=document.get("aba_transaction_id"),
            aba_payer_name=document.get("aba_payer_name"),
            matched_at=ShopService._datetime(document.get("matched_at")),
            created_at=ShopService._datetime(document.get("created_at")) or utc_now(),
            reviewed_at=ShopService._datetime(document.get("reviewed_at")),
            user=user,
        )

    @staticmethod
    def _refund_from_document(document: Document, *, user: User) -> RefundRequest:
        return RefundRequest(
            id=int(document["_id"]),
            user_id=int(document["user_id"]),
            amount_cents=int(document["amount_cents"]),
            status=str(document["status"]),
            qr_file_id=str(document["qr_file_id"]),
            qr_file_type=str(document.get("qr_file_type", "photo")),
            admin_note=document.get("admin_note"),
            reviewed_by=(
                int(document["reviewed_by"]) if document.get("reviewed_by") is not None else None
            ),
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
