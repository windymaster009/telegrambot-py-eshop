from datetime import datetime

from pydantic import BaseModel, Field


class ProductWrite(BaseModel):
    name_en: str = Field(min_length=1, max_length=180)
    price_cents: int = Field(gt=0)
    warranty: str = Field(default="No warranty", max_length=120)
    description_en: str = Field(default="", max_length=10_000)
    name_km: str | None = Field(default=None, max_length=180)
    description_km: str | None = Field(default=None, max_length=10_000)


class StockWrite(BaseModel):
    items: list[str] = Field(min_length=1, max_length=1_000)


class RejectWrite(BaseModel):
    note: str | None = Field(default=None, max_length=2_000)


class UserResponse(BaseModel):
    telegram_id: int
    username: str | None
    full_name: str
    language: str
    balance_cents: int
    created_at: datetime


class ProductResponse(BaseModel):
    id: int
    name_en: str
    name_km: str | None
    description_en: str
    description_km: str | None
    price_cents: int
    warranty: str
    active: bool
    stock: int
    created_at: datetime
    updated_at: datetime


class OrderResponse(BaseModel):
    id: int
    user: UserResponse
    product: ProductResponse
    quantity: int
    unit_price_cents: int
    total_cents: int
    payment_method: str
    status: str
    payment_proof_file_id: str | None
    admin_note: str | None
    expires_at: datetime | None
    created_at: datetime
    completed_at: datetime | None


class DepositResponse(BaseModel):
    id: int
    user: UserResponse
    requested_amount_cents: int
    amount_cents: int
    status: str
    payment_proof_file_id: str | None
    admin_note: str | None
    expires_at: datetime | None
    aba_transaction_id: str | None
    aba_payer_name: str | None
    matched_at: datetime | None
    created_at: datetime
    reviewed_at: datetime | None


class StockAddedResponse(BaseModel):
    product_id: int
    added: int


class OrderActionResponse(BaseModel):
    order: OrderResponse
    notification_sent: bool


class DepositActionResponse(BaseModel):
    deposit: DepositResponse
    notification_sent: bool
