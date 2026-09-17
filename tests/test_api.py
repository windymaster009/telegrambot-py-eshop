from types import SimpleNamespace

import httpx
import pytest

from app.api import create_app
from app.config import Settings
from app.services import ShopService


@pytest.fixture
def api_settings() -> Settings:
    return Settings(
        _env_file=None,
        bot_token="123456:TEST_TOKEN",
        admin_ids="123456789",
        mongo_uri="mongodb://localhost:27017",
        mongo_db_name="eshop",
        api_key="test-api-key-that-is-at-least-32-characters",
    )


async def test_public_products_and_protected_admin_api(
    shop: ShopService, api_settings: Settings
) -> None:
    await shop.add_product("API Product", 1299, "30 days", "Managed through the API")
    app = create_app(
        settings=api_settings,
        service=shop,
        bot=SimpleNamespace(),  # No Telegram action is used in this test.
    )
    transport = httpx.ASGITransport(app=app)

    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            public_response = await client.get("/api/v1/products")
            unauthorized = await client.get("/api/v1/admin/products")
            authorized = await client.get(
                "/api/v1/admin/products",
                headers={"X-API-Key": api_settings.api_key.get_secret_value()},
            )

    assert public_response.status_code == 200
    assert public_response.json()[0]["name_en"] == "API Product"
    assert unauthorized.status_code == 401
    assert authorized.status_code == 200
