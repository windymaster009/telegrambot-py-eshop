from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from sqlalchemy import BigInteger, Boolean, DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class Language(StrEnum):
    EN = "en"
    KM = "km"


class OrderStatus(StrEnum):
    AWAITING_PAYMENT = "awaiting_payment"
    AWAITING_REVIEW = "awaiting_review"
    COMPLETED = "completed"
    REJECTED = "rejected"
    CANCELLED = "cancelled"


class PaymentMethod(StrEnum):
    QR = "qr"
    BALANCE = "balance"


class DepositStatus(StrEnum):
    AWAITING_PROOF = "awaiting_proof"
    AWAITING_REVIEW = "awaiting_review"
    APPROVED = "approved"
    REJECTED = "rejected"


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    telegram_id: Mapped[int] = mapped_column(BigInteger, unique=True, index=True)
    username: Mapped[str | None] = mapped_column(String(64))
    full_name: Mapped[str] = mapped_column(String(160))
    language: Mapped[str] = mapped_column(String(2), default=Language.EN.value)
    balance_cents: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )


class Product(Base):
    __tablename__ = "products"

    id: Mapped[int] = mapped_column(primary_key=True)
    name_en: Mapped[str] = mapped_column(String(180))
    name_km: Mapped[str | None] = mapped_column(String(180))
    description_en: Mapped[str] = mapped_column(Text, default="")
    description_km: Mapped[str | None] = mapped_column(Text)
    price_cents: Mapped[int] = mapped_column(Integer)
    warranty: Mapped[str] = mapped_column(String(120), default="No warranty")
    active: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    stock_items: Mapped[list[StockItem]] = relationship(back_populates="product")
    orders: Mapped[list[Order]] = relationship(back_populates="product")


class StockItem(Base):
    __tablename__ = "stock_items"

    id: Mapped[int] = mapped_column(primary_key=True)
    product_id: Mapped[int] = mapped_column(ForeignKey("products.id"), index=True)
    content: Mapped[str] = mapped_column(Text)
    reserved_order_id: Mapped[int | None] = mapped_column(
        ForeignKey("orders.id", use_alter=True), nullable=True, index=True
    )
    sold_at: Mapped[datetime | None] = mapped_column(DateTime)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    product: Mapped[Product] = relationship(back_populates="stock_items")
    reserved_order: Mapped[Order | None] = relationship(
        foreign_keys=[reserved_order_id], back_populates="stock_items"
    )


class Order(Base):
    __tablename__ = "orders"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    product_id: Mapped[int] = mapped_column(ForeignKey("products.id"), index=True)
    quantity: Mapped[int] = mapped_column(Integer)
    unit_price_cents: Mapped[int] = mapped_column(Integer)
    total_cents: Mapped[int] = mapped_column(Integer)
    payment_method: Mapped[str] = mapped_column(String(16))
    status: Mapped[str] = mapped_column(String(32), index=True)
    payment_proof_file_id: Mapped[str | None] = mapped_column(String(255))
    admin_note: Mapped[str | None] = mapped_column(Text)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    completed_at: Mapped[datetime | None] = mapped_column(DateTime)

    user: Mapped[User] = relationship()
    product: Mapped[Product] = relationship(back_populates="orders")
    stock_items: Mapped[list[StockItem]] = relationship(
        foreign_keys=[StockItem.reserved_order_id], back_populates="reserved_order"
    )


class Deposit(Base):
    __tablename__ = "deposits"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    amount_cents: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(32), index=True)
    payment_proof_file_id: Mapped[str | None] = mapped_column(String(255))
    admin_note: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime)

    user: Mapped[User] = relationship()
