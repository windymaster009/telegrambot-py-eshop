from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum


def utc_now() -> datetime:
    return datetime.now(UTC)


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
    CANCELLED = "cancelled"
    EXPIRED = "expired"


class RefundStatus(StrEnum):
    PENDING = "pending"
    PAID = "paid"
    REJECTED = "rejected"


@dataclass(slots=True)
class User:
    id: int
    telegram_id: int
    username: str | None
    full_name: str
    language: str = Language.EN.value
    balance_cents: int = 0
    created_at: datetime = field(default_factory=utc_now)
    updated_at: datetime = field(default_factory=utc_now)


@dataclass(slots=True)
class Product:
    id: int
    name_en: str
    name_km: str | None
    description_en: str
    description_km: str | None
    price_cents: int
    warranty: str
    active: bool = True
    created_at: datetime = field(default_factory=utc_now)
    updated_at: datetime = field(default_factory=utc_now)


@dataclass(slots=True)
class StockItem:
    id: int
    product_id: int
    content: str
    reserved_order_id: int | None = None
    sold_at: datetime | None = None
    created_at: datetime = field(default_factory=utc_now)


@dataclass(slots=True)
class Order:
    id: int
    user_id: int
    product_id: int
    quantity: int
    unit_price_cents: int
    total_cents: int
    payment_method: str
    status: str
    payment_proof_file_id: str | None = None
    admin_note: str | None = None
    expires_at: datetime | None = None
    created_at: datetime = field(default_factory=utc_now)
    completed_at: datetime | None = None
    user: User | None = None
    product: Product | None = None
    stock_items: list[StockItem] = field(default_factory=list)


@dataclass(slots=True)
class Deposit:
    id: int
    user_id: int
    amount_cents: int
    status: str
    requested_amount_cents: int = 0
    payment_proof_file_id: str | None = None
    admin_note: str | None = None
    expires_at: datetime | None = None
    aba_transaction_id: str | None = None
    aba_payer_name: str | None = None
    matched_at: datetime | None = None
    created_at: datetime = field(default_factory=utc_now)
    reviewed_at: datetime | None = None
    user: User | None = None


@dataclass(slots=True)
class RefundRequest:
    id: int
    user_id: int
    amount_cents: int
    status: str
    qr_file_id: str
    qr_file_type: str = "photo"
    admin_note: str | None = None
    reviewed_by: int | None = None
    created_at: datetime = field(default_factory=utc_now)
    reviewed_at: datetime | None = None
    user: User | None = None
