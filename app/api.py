from __future__ import annotations

import logging
import mimetypes
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from io import BytesIO
from secrets import compare_digest
from typing import Annotated, Any

from aiogram import Bot
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramAPIError
from fastapi import APIRouter, Depends, FastAPI, Header, HTTPException, Query, Request, Response
from fastapi.responses import JSONResponse

from app.api_schemas import (
    DepositActionResponse,
    DepositResponse,
    OrderActionResponse,
    OrderResponse,
    ProductResponse,
    ProductWrite,
    RejectWrite,
    StockAddedResponse,
    StockWrite,
    UserResponse,
)
from app.config import Settings, get_settings
from app.database import Database
from app.models import Deposit, DepositStatus, Order, OrderStatus, User
from app.notifications import (
    send_deposit_approved,
    send_deposit_rejected,
    send_order_approved,
    send_order_rejected,
)
from app.services import (
    AlreadyProcessed,
    InsufficientBalance,
    NotEnoughStock,
    ProductView,
    ShopError,
    ShopService,
)

logger = logging.getLogger(__name__)


def create_app(
    *,
    settings: Settings | None = None,
    database: Database | None = None,
    service: ShopService | None = None,
    bot: Bot | None = None,
) -> FastAPI:
    @asynccontextmanager
    async def lifespan(application: FastAPI) -> AsyncIterator[None]:
        resolved_settings = settings or get_settings()
        active_database = database
        active_service = service
        active_bot = bot
        owns_database = False
        owns_bot = False

        if active_service is None:
            if active_database is None:
                active_database = Database(resolved_settings)
                owns_database = True
            await active_database.connect()
            active_service = ShopService(
                active_database.db,
                active_database.client,
                resolved_settings.payment_expiry_minutes,
                auto_topup_enabled=resolved_settings.auto_topup_enabled,
            )
        if active_bot is None:
            active_bot = Bot(
                resolved_settings.bot_token.get_secret_value(),
                default=DefaultBotProperties(parse_mode=ParseMode.HTML),
            )
            owns_bot = True

        application.state.settings = resolved_settings
        application.state.database = active_database
        application.state.service = active_service
        application.state.bot = active_bot
        try:
            yield
        finally:
            if owns_bot:
                await active_bot.session.close()
            if owns_database and active_database is not None:
                await active_database.close()

    application = FastAPI(
        title="Windy Shop API",
        version="1.0.0",
        description="Shared API for the Telegram shop and its admin website.",
        lifespan=lifespan,
    )

    @application.exception_handler(ShopError)
    async def shop_error_handler(_request: Request, error: ShopError) -> JSONResponse:
        if isinstance(error, (AlreadyProcessed, NotEnoughStock, InsufficientBalance)):
            status_code = 409
        else:
            status_code = 400
        return JSONResponse(status_code=status_code, content={"detail": str(error)})

    @application.get("/health")
    async def health(request: Request) -> dict[str, str]:
        active_database: Database | None = request.app.state.database
        if active_database is not None:
            await active_database.ping()
        return {"status": "ok"}

    public = APIRouter(prefix="/api/v1")

    @public.get("/products", response_model=list[ProductResponse])
    async def products(service: ServiceDependency) -> list[ProductResponse]:
        views = await service.list_products()
        return [product_response(view) for view in views]

    @public.get("/products/{product_id}", response_model=ProductResponse)
    async def product(product_id: int, service: ServiceDependency) -> ProductResponse:
        view = await service.get_product(product_id)
        if view is None:
            raise HTTPException(status_code=404, detail="Product not found")
        return product_response(view)

    admin = APIRouter(
        prefix="/api/v1/admin",
        dependencies=[Depends(require_api_key)],
    )

    @admin.get("/products", response_model=list[ProductResponse])
    async def admin_products(service: ServiceDependency) -> list[ProductResponse]:
        views = await service.list_products(include_inactive=True)
        return [product_response(view) for view in views]

    @admin.post("/products", response_model=ProductResponse, status_code=201)
    async def create_product(payload: ProductWrite, service: ServiceDependency) -> ProductResponse:
        created = await service.add_product(**payload.model_dump())
        view = await service.get_product(created.id, include_inactive=True)
        if view is None:
            raise HTTPException(status_code=500, detail="Product was not created")
        return product_response(view)

    @admin.put("/products/{product_id}", response_model=ProductResponse)
    async def update_product(
        product_id: int, payload: ProductWrite, service: ServiceDependency
    ) -> ProductResponse:
        await service.update_product(product_id, **payload.model_dump())
        view = await service.get_product(product_id, include_inactive=True)
        if view is None:
            raise HTTPException(status_code=404, detail="Product not found")
        return product_response(view)

    @admin.post("/products/{product_id}/toggle", response_model=ProductResponse)
    async def toggle_product(product_id: int, service: ServiceDependency) -> ProductResponse:
        await service.toggle_product(product_id)
        view = await service.get_product(product_id, include_inactive=True)
        if view is None:
            raise HTTPException(status_code=404, detail="Product not found")
        return product_response(view)

    @admin.post("/products/{product_id}/stock", response_model=StockAddedResponse)
    async def add_stock(
        product_id: int, payload: StockWrite, service: ServiceDependency
    ) -> StockAddedResponse:
        added = await service.add_stock(product_id, payload.items)
        return StockAddedResponse(product_id=product_id, added=added)

    @admin.get("/orders", response_model=list[OrderResponse])
    async def list_orders(
        service: ServiceDependency,
        status: OrderStatus | None = None,
        limit: Annotated[int, Query(ge=1, le=500)] = 100,
    ) -> list[OrderResponse]:
        orders = await service.admin_orders(status.value if status else None, limit)
        return [await order_response(service, order) for order in orders]

    @admin.post("/orders/{order_id}/approve", response_model=OrderActionResponse)
    async def approve_order(
        order_id: int, service: ServiceDependency, bot: BotDependency
    ) -> OrderActionResponse:
        order = await service.approve_order(order_id)
        sent = await try_notification(send_order_approved, bot, order)
        return OrderActionResponse(
            order=await order_response(service, order), notification_sent=sent
        )

    @admin.post("/orders/{order_id}/reject", response_model=OrderActionResponse)
    async def reject_order(
        order_id: int,
        payload: RejectWrite,
        service: ServiceDependency,
        bot: BotDependency,
    ) -> OrderActionResponse:
        order = await service.reject_order(order_id, payload.note)
        sent = await try_notification(send_order_rejected, bot, order)
        return OrderActionResponse(
            order=await order_response(service, order), notification_sent=sent
        )

    @admin.get("/deposits", response_model=list[DepositResponse])
    async def list_deposits(
        service: ServiceDependency,
        status: DepositStatus | None = None,
        limit: Annotated[int, Query(ge=1, le=500)] = 100,
    ) -> list[DepositResponse]:
        deposits = await service.admin_deposits(status.value if status else None, limit)
        return [deposit_response(deposit) for deposit in deposits]

    @admin.post("/deposits/{deposit_id}/approve", response_model=DepositActionResponse)
    async def approve_deposit(
        deposit_id: int, service: ServiceDependency, bot: BotDependency
    ) -> DepositActionResponse:
        deposit = await service.approve_deposit(deposit_id)
        sent = await try_notification(send_deposit_approved, bot, deposit)
        return DepositActionResponse(deposit=deposit_response(deposit), notification_sent=sent)

    @admin.post("/deposits/{deposit_id}/reject", response_model=DepositActionResponse)
    async def reject_deposit(
        deposit_id: int,
        payload: RejectWrite,
        service: ServiceDependency,
        bot: BotDependency,
    ) -> DepositActionResponse:
        deposit = await service.reject_deposit(deposit_id, payload.note)
        sent = await try_notification(send_deposit_rejected, bot, deposit)
        return DepositActionResponse(deposit=deposit_response(deposit), notification_sent=sent)

    @admin.get("/users", response_model=list[UserResponse])
    async def list_users(
        service: ServiceDependency,
        limit: Annotated[int, Query(ge=1, le=500)] = 100,
    ) -> list[UserResponse]:
        return [user_response(user) for user in await service.list_users(limit)]

    @admin.get("/telegram-files/{file_id}")
    async def telegram_file(file_id: str, bot: BotDependency) -> Response:
        telegram_file = await bot.get_file(file_id)
        if not telegram_file.file_path:
            raise HTTPException(status_code=404, detail="Telegram file not found")
        destination = BytesIO()
        await bot.download_file(telegram_file.file_path, destination=destination)
        media_type = mimetypes.guess_type(telegram_file.file_path)[0]
        return Response(destination.getvalue(), media_type=media_type or "application/octet-stream")

    application.include_router(public)
    application.include_router(admin)
    return application


async def get_service(request: Request) -> ShopService:
    return request.app.state.service


async def get_bot(request: Request) -> Bot:
    return request.app.state.bot


async def require_api_key(
    request: Request,
    x_api_key: Annotated[str | None, Header(alias="X-API-Key")] = None,
) -> None:
    settings: Settings = request.app.state.settings
    expected = settings.api_key.get_secret_value()
    if not x_api_key or not compare_digest(x_api_key, expected):
        raise HTTPException(status_code=401, detail="Invalid API key")


ServiceDependency = Annotated[ShopService, Depends(get_service)]
BotDependency = Annotated[Bot, Depends(get_bot)]


def user_response(user: User) -> UserResponse:
    return UserResponse(
        telegram_id=user.telegram_id,
        username=user.username,
        full_name=user.full_name,
        language=user.language,
        balance_cents=user.balance_cents,
        created_at=user.created_at,
    )


def product_response(view: ProductView) -> ProductResponse:
    product = view.product
    return ProductResponse(
        id=product.id,
        name_en=product.name_en,
        name_km=product.name_km,
        description_en=product.description_en,
        description_km=product.description_km,
        price_cents=product.price_cents,
        warranty=product.warranty,
        active=product.active,
        stock=view.stock,
        created_at=product.created_at,
        updated_at=product.updated_at,
    )


async def order_response(service: ShopService, order: Order) -> OrderResponse:
    if order.user is None or order.product is None:
        raise RuntimeError("Order relations are not loaded")
    view = await service.get_product(order.product.id, include_inactive=True)
    if view is None:
        raise RuntimeError("Order product was deleted")
    return OrderResponse(
        id=order.id,
        user=user_response(order.user),
        product=product_response(view),
        quantity=order.quantity,
        unit_price_cents=order.unit_price_cents,
        total_cents=order.total_cents,
        payment_method=order.payment_method,
        status=order.status,
        payment_proof_file_id=order.payment_proof_file_id,
        admin_note=order.admin_note,
        expires_at=order.expires_at,
        created_at=order.created_at,
        completed_at=order.completed_at,
    )


def deposit_response(deposit: Deposit) -> DepositResponse:
    if deposit.user is None:
        raise RuntimeError("Deposit user is not loaded")
    return DepositResponse(
        id=deposit.id,
        user=user_response(deposit.user),
        requested_amount_cents=deposit.requested_amount_cents,
        amount_cents=deposit.amount_cents,
        status=deposit.status,
        payment_proof_file_id=deposit.payment_proof_file_id,
        admin_note=deposit.admin_note,
        expires_at=deposit.expires_at,
        aba_transaction_id=deposit.aba_transaction_id,
        aba_payer_name=deposit.aba_payer_name,
        matched_at=deposit.matched_at,
        created_at=deposit.created_at,
        reviewed_at=deposit.reviewed_at,
    )


async def try_notification(
    sender: Any,
    bot: Bot,
    value: Order | Deposit,
) -> bool:
    try:
        await sender(bot, value)
    except (TelegramAPIError, OSError):
        logger.exception("Database action succeeded, but Telegram notification failed")
        return False
    return True


app = create_app()
