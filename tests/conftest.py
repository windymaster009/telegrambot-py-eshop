import pytest
from mongomock_motor import AsyncMongoMockClient

from app.services import ShopService


@pytest.fixture
async def shop():
    client = AsyncMongoMockClient()
    service = ShopService(
        client.eshop,
        client,
        payment_expiry_minutes=30,
        use_transactions=False,
    )
    yield service
    client.close()
