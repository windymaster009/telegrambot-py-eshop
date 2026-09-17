from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from aiogram.types import User as TelegramUser
from sqlalchemy import Select, func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

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
)


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
        self, sessions: async_sessionmaker[AsyncSession], payment_expiry_minutes: int
    ) -> None:
        self.sessions = sessions
        self.payment_expiry_minutes = payment_expiry_minutes

    async def get_or_create_user(self, telegram_user: TelegramUser) -> User:
        async with self.sessions.begin() as session:
            user = await session.scalar(
                select(User).where(User.telegram_id == telegram_user.id).with_for_update()
            )
            full_name = telegram_user.full_name[:160] or str(telegram_user.id)
            if user is None:
                user = User(
                    telegram_id=telegram_user.id,
                    username=telegram_user.username,
                    full_name=full_name,
                    language=Language.EN.value,
                )
                session.add(user)
                await session.flush()
            else:
                user.username = telegram_user.username
                user.full_name = full_name
            return user

    async def set_language(self, telegram_id: int, language: Language) -> User:
        async with self.sessions.begin() as session:
            user = await self._user_by_telegram_id(session, telegram_id, lock=True)
            user.language = language.value
            return user

    async def get_user(self, telegram_id: int) -> User:
        async with self.sessions() as session:
            return await self._user_by_telegram_id(session, telegram_id)

    async def list_products(self, *, include_inactive: bool = False) -> list[ProductView]:
        async with self.sessions() as session:
            stock_count = (
                select(func.count(StockItem.id))
                .where(
                    StockItem.product_id == Product.id,
                    StockItem.sold_at.is_(None),
                    StockItem.reserved_order_id.is_(None),
                )
                .correlate(Product)
                .scalar_subquery()
            )
            query: Select[tuple[Product, int]] = select(Product, stock_count.label("stock"))
            if not include_inactive:
                query = query.where(Product.active.is_(True))
            rows = (await session.execute(query.order_by(Product.id))).all()
            return [ProductView(product=row[0], stock=int(row[1])) for row in rows]

    async def get_product(
        self, product_id: int, *, include_inactive: bool = False
    ) -> ProductView | None:
        async with self.sessions() as session:
            query = select(Product).where(Product.id == product_id)
            if not include_inactive:
                query = query.where(Product.active.is_(True))
            product = await session.scalar(query)
            if product is None:
                return None
            return ProductView(
                product=product, stock=await self._available_stock(session, product_id)
            )

    async def buy_with_balance(self, telegram_id: int, product_id: int, quantity: int) -> Order:
        if quantity < 1:
            raise NotEnoughStock
        async with self.sessions.begin() as session:
            user = await self._user_by_telegram_id(session, telegram_id, lock=True)
            product = await session.scalar(
                select(Product)
                .where(Product.id == product_id, Product.active.is_(True))
                .with_for_update()
            )
            if product is None:
                raise NotEnoughStock
            total = product.price_cents * quantity
            if user.balance_cents < total:
                raise InsufficientBalance(user.balance_cents, total)
            stock = await self._take_available_stock(session, product_id, quantity)
            order = Order(
                user_id=user.id,
                product_id=product.id,
                quantity=quantity,
                unit_price_cents=product.price_cents,
                total_cents=total,
                payment_method=PaymentMethod.BALANCE.value,
                status=OrderStatus.COMPLETED.value,
                completed_at=datetime.now(UTC).replace(tzinfo=None),
            )
            session.add(order)
            await session.flush()
            now = datetime.now(UTC).replace(tzinfo=None)
            for item in stock:
                item.reserved_order_id = order.id
                item.sold_at = now
            user.balance_cents -= total
            await session.flush()
            return await self._load_order(session, order.id)

    async def create_qr_order(self, telegram_id: int, product_id: int, quantity: int) -> Order:
        if quantity < 1:
            raise NotEnoughStock
        async with self.sessions.begin() as session:
            user = await self._user_by_telegram_id(session, telegram_id, lock=True)
            product = await session.scalar(
                select(Product)
                .where(Product.id == product_id, Product.active.is_(True))
                .with_for_update()
            )
            if product is None:
                raise NotEnoughStock
            stock = await self._take_available_stock(session, product_id, quantity)
            now = datetime.now(UTC).replace(tzinfo=None)
            order = Order(
                user_id=user.id,
                product_id=product.id,
                quantity=quantity,
                unit_price_cents=product.price_cents,
                total_cents=product.price_cents * quantity,
                payment_method=PaymentMethod.QR.value,
                status=OrderStatus.AWAITING_PAYMENT.value,
                expires_at=now + timedelta(minutes=self.payment_expiry_minutes),
            )
            session.add(order)
            await session.flush()
            for item in stock:
                item.reserved_order_id = order.id
            await session.flush()
            return await self._load_order(session, order.id)

    async def submit_order_proof(self, telegram_id: int, order_id: int, file_id: str) -> Order:
        async with self.sessions.begin() as session:
            user = await self._user_by_telegram_id(session, telegram_id)
            order = await session.scalar(
                select(Order)
                .where(Order.id == order_id, Order.user_id == user.id)
                .with_for_update()
            )
            if order is None or order.status != OrderStatus.AWAITING_PAYMENT.value:
                raise AlreadyProcessed
            now = datetime.now(UTC).replace(tzinfo=None)
            if order.expires_at and order.expires_at < now:
                await self._cancel_order(session, order)
                raise AlreadyProcessed
            order.payment_proof_file_id = file_id
            order.status = OrderStatus.AWAITING_REVIEW.value
            return await self._load_order(session, order.id)

    async def list_orders(self, telegram_id: int, limit: int = 10) -> list[Order]:
        async with self.sessions() as session:
            user = await self._user_by_telegram_id(session, telegram_id)
            query = (
                select(Order)
                .where(Order.user_id == user.id)
                .order_by(Order.created_at.desc())
                .limit(limit)
            )
            orders = list((await session.scalars(query)).all())
            for order in orders:
                await session.refresh(order, ["product"])
            return orders

    async def create_deposit(self, telegram_id: int, amount_cents: int) -> Deposit:
        async with self.sessions.begin() as session:
            user = await self._user_by_telegram_id(session, telegram_id)
            deposit = Deposit(
                user_id=user.id,
                amount_cents=amount_cents,
                status=DepositStatus.AWAITING_PROOF.value,
            )
            session.add(deposit)
            await session.flush()
            return deposit

    async def submit_deposit_proof(
        self, telegram_id: int, deposit_id: int, file_id: str
    ) -> Deposit:
        async with self.sessions.begin() as session:
            user = await self._user_by_telegram_id(session, telegram_id)
            deposit = await session.scalar(
                select(Deposit)
                .where(Deposit.id == deposit_id, Deposit.user_id == user.id)
                .with_for_update()
            )
            if deposit is None or deposit.status != DepositStatus.AWAITING_PROOF.value:
                raise AlreadyProcessed
            deposit.payment_proof_file_id = file_id
            deposit.status = DepositStatus.AWAITING_REVIEW.value
            await session.refresh(deposit, ["user"])
            return deposit

    async def add_product(
        self,
        name_en: str,
        price_cents: int,
        warranty: str,
        description_en: str,
        name_km: str | None = None,
        description_km: str | None = None,
    ) -> Product:
        async with self.sessions.begin() as session:
            product = Product(
                name_en=name_en,
                name_km=name_km,
                price_cents=price_cents,
                warranty=warranty,
                description_en=description_en,
                description_km=description_km,
            )
            session.add(product)
            await session.flush()
            return product

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
        async with self.sessions.begin() as session:
            product = await session.get(Product, product_id, with_for_update=True)
            if product is None:
                raise ShopError("Product not found")
            product.name_en = name_en
            product.price_cents = price_cents
            product.warranty = warranty
            product.description_en = description_en
            product.name_km = name_km
            product.description_km = description_km
            return product

    async def add_stock(self, product_id: int, contents: list[str]) -> int:
        cleaned = [item.strip() for item in contents if item.strip()]
        if not cleaned:
            return 0
        async with self.sessions.begin() as session:
            product = await session.get(Product, product_id)
            if product is None:
                raise ShopError("Product not found")
            session.add_all(StockItem(product_id=product_id, content=item) for item in cleaned)
            return len(cleaned)

    async def toggle_product(self, product_id: int) -> Product:
        async with self.sessions.begin() as session:
            product = await session.get(Product, product_id, with_for_update=True)
            if product is None:
                raise ShopError("Product not found")
            product.active = not product.active
            return product

    async def pending_orders(self, limit: int = 20) -> list[Order]:
        async with self.sessions() as session:
            query = (
                select(Order)
                .where(Order.status == OrderStatus.AWAITING_REVIEW.value)
                .order_by(Order.created_at)
                .limit(limit)
            )
            orders = list((await session.scalars(query)).all())
            for order in orders:
                await session.refresh(order, ["user", "product"])
            return orders

    async def approve_order(self, order_id: int) -> Order:
        async with self.sessions.begin() as session:
            order = await session.get(Order, order_id, with_for_update=True)
            if order is None or order.status != OrderStatus.AWAITING_REVIEW.value:
                raise AlreadyProcessed
            await session.refresh(order, ["stock_items"])
            if len(order.stock_items) != order.quantity:
                raise NotEnoughStock
            now = datetime.now(UTC).replace(tzinfo=None)
            for item in order.stock_items:
                item.sold_at = now
            order.status = OrderStatus.COMPLETED.value
            order.completed_at = now
            return await self._load_order(session, order.id)

    async def reject_order(self, order_id: int, note: str | None = None) -> Order:
        async with self.sessions.begin() as session:
            order = await session.get(Order, order_id, with_for_update=True)
            if order is None or order.status != OrderStatus.AWAITING_REVIEW.value:
                raise AlreadyProcessed
            await session.refresh(order, ["stock_items"])
            for item in order.stock_items:
                item.reserved_order_id = None
            order.status = OrderStatus.REJECTED.value
            order.admin_note = note
            return await self._load_order(session, order.id)

    async def pending_deposits(self, limit: int = 20) -> list[Deposit]:
        async with self.sessions() as session:
            query = (
                select(Deposit)
                .where(Deposit.status == DepositStatus.AWAITING_REVIEW.value)
                .order_by(Deposit.created_at)
                .limit(limit)
            )
            deposits = list((await session.scalars(query)).all())
            for deposit in deposits:
                await session.refresh(deposit, ["user"])
            return deposits

    async def approve_deposit(self, deposit_id: int) -> Deposit:
        async with self.sessions.begin() as session:
            deposit = await session.get(Deposit, deposit_id, with_for_update=True)
            if deposit is None or deposit.status != DepositStatus.AWAITING_REVIEW.value:
                raise AlreadyProcessed
            user = await session.get(User, deposit.user_id, with_for_update=True)
            if user is None:
                raise ShopError("User not found")
            user.balance_cents += deposit.amount_cents
            deposit.status = DepositStatus.APPROVED.value
            deposit.reviewed_at = datetime.now(UTC).replace(tzinfo=None)
            await session.refresh(deposit, ["user"])
            return deposit

    async def reject_deposit(self, deposit_id: int, note: str | None = None) -> Deposit:
        async with self.sessions.begin() as session:
            deposit = await session.get(Deposit, deposit_id, with_for_update=True)
            if deposit is None or deposit.status != DepositStatus.AWAITING_REVIEW.value:
                raise AlreadyProcessed
            deposit.status = DepositStatus.REJECTED.value
            deposit.admin_note = note
            deposit.reviewed_at = datetime.now(UTC).replace(tzinfo=None)
            await session.refresh(deposit, ["user"])
            return deposit

    async def expire_orders(self) -> int:
        now = datetime.now(UTC).replace(tzinfo=None)
        async with self.sessions.begin() as session:
            orders = list(
                (
                    await session.scalars(
                        select(Order)
                        .where(
                            Order.status == OrderStatus.AWAITING_PAYMENT.value,
                            Order.expires_at < now,
                        )
                        .with_for_update()
                    )
                ).all()
            )
            for order in orders:
                await self._cancel_order(session, order)
            return len(orders)

    async def _cancel_order(self, session: AsyncSession, order: Order) -> None:
        await session.refresh(order, ["stock_items"])
        for item in order.stock_items:
            item.reserved_order_id = None
        order.status = OrderStatus.CANCELLED.value

    async def _user_by_telegram_id(
        self, session: AsyncSession, telegram_id: int, *, lock: bool = False
    ) -> User:
        query = select(User).where(User.telegram_id == telegram_id)
        if lock:
            query = query.with_for_update()
        user = await session.scalar(query)
        if user is None:
            raise ShopError("User not found. Send /start first.")
        return user

    async def _available_stock(self, session: AsyncSession, product_id: int) -> int:
        count = await session.scalar(
            select(func.count(StockItem.id)).where(
                StockItem.product_id == product_id,
                StockItem.sold_at.is_(None),
                StockItem.reserved_order_id.is_(None),
            )
        )
        return int(count or 0)

    async def _take_available_stock(
        self, session: AsyncSession, product_id: int, quantity: int
    ) -> list[StockItem]:
        query = (
            select(StockItem)
            .where(
                StockItem.product_id == product_id,
                StockItem.sold_at.is_(None),
                StockItem.reserved_order_id.is_(None),
            )
            .order_by(StockItem.id)
            .limit(quantity)
            .with_for_update(skip_locked=True)
        )
        items = list((await session.scalars(query)).all())
        if len(items) != quantity:
            raise NotEnoughStock
        return items

    async def _load_order(self, session: AsyncSession, order_id: int) -> Order:
        order = await session.get(Order, order_id)
        if order is None:
            raise ShopError("Order not found")
        await session.refresh(order, ["user", "product", "stock_items"])
        return order


def product_name(product: Product, language: str) -> str:
    if language == Language.KM.value and product.name_km:
        return product.name_km
    return product.name_en


def product_description(product: Product, language: str) -> str:
    if language == Language.KM.value and product.description_km:
        return product.description_km
    return product.description_en


def delivery_text(order: Order) -> str:
    return "\n\n".join(item.content for item in order.stock_items)
