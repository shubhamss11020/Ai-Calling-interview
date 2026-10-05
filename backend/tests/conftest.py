"""Test environment.

DB tests run in one of two safe modes:
  * TEST_DATABASE_URL set  -> use that dedicated Neon branch directly.
  * otherwise, if DATABASE_URL exists in backend/.env -> run inside a throwaway schema on that database
    (SQLAlchemy schema_translate_map; dropped afterwards). Production tables are never touched.
With neither, DB tests are skipped. Network-backed LLM/embedding keys are always blanked for tests.
"""
import asyncio
import os
import uuid
from pathlib import Path

import pytest
from dotenv import dotenv_values

_env_file = dotenv_values(Path(__file__).resolve().parents[1] / ".env")
_real_url = os.environ.get("DATABASE_URL") or _env_file.get("DATABASE_URL") or ""
_test_url = os.environ.get("TEST_DATABASE_URL", "")
_looks_real = _real_url.startswith(("postgres://", "postgresql")) and "USER:PASSWORD" not in _real_url

if _test_url:
    assert _test_url != _real_url, "TEST_DATABASE_URL must differ from DATABASE_URL"
    DB_MODE = "branch"
    os.environ["DATABASE_URL"] = _test_url
elif _looks_real:
    DB_MODE = "schema"
    os.environ["DATABASE_URL"] = _real_url
else:
    DB_MODE = "none"
    # Settings validation needs *a* Postgres URL at import time; non-DB tests never connect.
    os.environ["DATABASE_URL"] = "postgresql://unused:unused@127.0.0.1:1/unused"

os.environ.setdefault("REDIS_URL", "rediss://default:unused@unused.upstash.io:6379")
os.environ["ASYNC_PROCESSING"] = "false"
os.environ["GROQ_API_KEY"] = ""
os.environ["GEMINI_API_KEY"] = ""
os.environ["LANGSMITH_TRACING"] = "false"

# Settings has env_ignore_empty=True, so blanking os.environ would silently fall back to the real .env values.
# Override on the settings object itself: tests must never reach real LLMs, Redis or the telephony carrier.
from app.core.config import settings as _settings  # noqa: E402

for _key in ("GROQ_API_KEY", "GEMINI_API_KEY", "OPENAI_API_KEY", "VOBIZ_AUTH_ID", "VOBIZ_AUTH_TOKEN",
             "VOBIZ_PHONE_NUMBER", "DEEPGRAM_API_KEY", "ELEVENLABS_API_KEY", "LANGSMITH_API_KEY"):
    setattr(_settings, _key, "")
_settings.REDIS_URL = "rediss://default:unused@unused.upstash.io:6379"  # limiter falls back to in-process
_settings.ASYNC_PROCESSING = False
_settings.LANGSMITH_TRACING = False
_settings.PUBLIC_BASE_URL = "https://tests.invalid"

_skip_no_db = pytest.mark.skipif(DB_MODE == "none", reason="no Postgres configured (set DATABASE_URL in .env or TEST_DATABASE_URL)")


def requires_db(fn):
    """Skip without a database; otherwise run inside the isolated schema (created lazily, once per session)."""
    return pytest.mark.usefixtures("_db_isolation")(_skip_no_db(fn))


@pytest.fixture(scope="session")
def _db_isolation():
    """In 'schema' mode, point the app's engine and session factory at a throwaway schema."""
    if DB_MODE != "schema":
        yield
        return

    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import create_async_engine
    from sqlalchemy.pool import NullPool

    import app.core.database as database
    from app.core.config import settings

    schema = f"test_{uuid.uuid4().hex[:10]}"
    admin = create_async_engine(settings.async_database_url, connect_args=settings.database_connect_args, poolclass=NullPool)

    async def _create():
        async with admin.begin() as conn:
            await conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
            await conn.execute(text(f'CREATE SCHEMA "{schema}"'))

    async def _drop():
        async with admin.begin() as conn:
            await conn.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
        await admin.dispose()

    asyncio.run(_create())
    # NullPool: each pytest-asyncio test has its own event loop, so connections must not be shared across tests
    scoped = create_async_engine(
        settings.async_database_url, connect_args=settings.database_connect_args, poolclass=NullPool
    ).execution_options(schema_translate_map={None: schema})

    async def _tables():
        import app.models  # noqa: F401  (register every table on Base)
        async with scoped.begin() as conn:
            await conn.run_sync(database.Base.metadata.create_all)

    asyncio.run(_tables())
    original_engine = database.engine
    database.engine = scoped
    database.AsyncSessionLocal.configure(bind=scoped)
    try:
        import app.main as main
        main.engine = scoped
    except Exception:  # app import is exercised by the tests themselves
        pass
    try:
        yield
    finally:
        database.engine = original_engine
        asyncio.run(_drop())
