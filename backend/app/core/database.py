import os
from typing import AsyncGenerator
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from sqlalchemy.orm import declarative_base

from app.core.config import settings

# Ensure upload directory exists for local files
os.makedirs(settings.UPLOAD_DIR, exist_ok=True)

# Async engine
engine = create_async_engine(
    settings.async_database_url,
    connect_args=settings.database_connect_args,
    pool_pre_ping=True,   # Neon suspends idle compute; drop dead connections instead of erroring
    pool_recycle=300,
    pool_size=5,
    max_overflow=5,
    echo=False,  # SQL logging is controlled via LOG_LEVEL=DEBUG
    future=True
)

# Async session factory
AsyncSessionLocal = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autocommit=False,
    autoflush=False
)

Base = declarative_base()


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """Dependency that yields an async database session per request."""
    async with AsyncSessionLocal() as session:
        try:
            yield session
        finally:
            await session.close()


def add_missing_columns(sync_conn) -> None:
    """Lightweight additive migration for dev databases created before a column existed
    (create_all never alters existing tables). Use Alembic for anything beyond adding columns."""
    from sqlalchemy import inspect, text
    inspector = inspect(sync_conn)
    for table in Base.metadata.sorted_tables:
        if not inspector.has_table(table.name):
            continue
        existing = {c["name"] for c in inspector.get_columns(table.name)}
        for column in table.columns:
            if column.name not in existing:
                col_type = column.type.compile(sync_conn.dialect)
                sync_conn.execute(text(f'ALTER TABLE {table.name} ADD COLUMN {column.name} {col_type}'))
