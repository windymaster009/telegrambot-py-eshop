from collections.abc import AsyncIterator

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.config import Settings
from app.models import Base


class Database:
    def __init__(self, settings: Settings) -> None:
        connect_args = {"timeout": 30} if settings.database_url.startswith("sqlite") else {}
        self.engine = create_async_engine(settings.database_url, connect_args=connect_args)
        self.sessions = async_sessionmaker(self.engine, expire_on_commit=False)

    async def create_schema(self) -> None:
        async with self.engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)

    async def session(self) -> AsyncIterator[AsyncSession]:
        async with self.sessions() as session:
            yield session

    async def close(self) -> None:
        await self.engine.dispose()
