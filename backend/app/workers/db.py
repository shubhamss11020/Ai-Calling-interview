"""Run async DB work from sync Celery tasks. A fresh NullPool engine per call avoids reusing
connections bound to a previous (closed) event loop."""
import asyncio
from typing import Awaitable, Callable, TypeVar

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.core.config import settings

T = TypeVar("T")


def run_with_session(fn: Callable[[AsyncSession], Awaitable[T]]) -> T:
    async def _run() -> T:
        engine = create_async_engine(
            settings.async_database_url, connect_args=settings.database_connect_args, poolclass=NullPool
        )
        try:
            async with async_sessionmaker(engine, expire_on_commit=False)() as session:
                return await fn(session)
        finally:
            await engine.dispose()

    return asyncio.run(_run())
