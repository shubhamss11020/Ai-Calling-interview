import logging
import os
import time
from sqlalchemy import text
import uuid
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from app.core.config import settings
from app.core.database import engine, Base, add_missing_columns
from app.api.v1.router import api_router
from app.api.ws import router as ws_router
from app.core.errors import register_error_handlers
from app.core.tracing import configure_tracing
from app.core.logging import setup_logging, request_id_ctx

setup_logging(settings.ENVIRONMENT, settings.LOG_LEVEL)
configure_tracing()
logger = logging.getLogger("app.http")


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Initialize database tables on startup
    async with engine.begin() as conn:
        await conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))  # pgvector (supported on Neon)
        await conn.run_sync(Base.metadata.create_all)
        await conn.run_sync(add_missing_columns)
    yield
    await engine.dispose()


app = FastAPI(
    title=settings.PROJECT_NAME,
    version=settings.VERSION,
    description="Enterprise AI Recruitment Engine & Voice Calling Agent Orchestrator",
    openapi_url=f"{settings.API_V1_STR}/openapi.json",
    docs_url="/docs",
    redoc_url="/redoc",
    lifespan=lifespan
)

register_error_handlers(app)


LOCAL_HOSTS = {"localhost", "127.0.0.1", "[::1]", "testserver", "t"}
PUBLIC_ALLOWED_PREFIXES = ("/api/v1/interviews/telephony/vobiz/", "/health")


@app.middleware("http")
async def restrict_public_hosts(request: Request, call_next):
    """Safe by default: only localhost (and FULL_API_HOSTS) can use the full, unauthenticated API. Anything arriving
    through a public tunnel is limited to the Vobiz webhooks and health checks."""
    host = (request.headers.get("host") or "").split(":")[0].lower()
    if host in LOCAL_HOSTS or host in {h.lower() for h in settings.FULL_API_HOSTS} or not host:
        return await call_next(request)
    if request.url.path.startswith(PUBLIC_ALLOWED_PREFIXES):
        return await call_next(request)
    logging.getLogger("app.http").warning("Blocked %s %s from public host %s", request.method, request.url.path, host)
    return JSONResponse(status_code=403, content={"error": {"code": "FORBIDDEN_HOST", "message": "Not available on this host."}})


@app.middleware("http")
async def request_context(request: Request, call_next):
    """Assign/propagate X-Request-ID, log one line per request with status and latency."""
    rid = request.headers.get("X-Request-ID") or uuid.uuid4().hex[:12]
    token = request_id_ctx.set(rid)
    start = time.perf_counter()
    try:
        response = await call_next(request)
    except Exception:
        logger.exception("%s %s crashed rid=%s", request.method, request.url.path, rid)
        response = JSONResponse(status_code=500, content={"error": {
            "code": "INTERNAL_ERROR", "request_id": rid,
            "message": "Internal server error. Quote the request_id when reporting this."}})
    finally:
        ms = (time.perf_counter() - start) * 1000
        request_id_ctx.reset(token)
    response.headers["X-Request-ID"] = rid
    level = logging.WARNING if response.status_code >= 400 else logging.INFO
    logging.getLogger("app.http").log(level, "%s %s -> %s (%.0fms) rid=%s", request.method, request.url.path, response.status_code, ms, rid)
    return response


# CORS Middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.BACKEND_CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Mount local uploads directory
os.makedirs(settings.UPLOAD_DIR, exist_ok=True)
app.mount("/uploads", StaticFiles(directory=settings.UPLOAD_DIR), name="uploads")

# Include API Router
app.include_router(api_router, prefix=settings.API_V1_STR)
app.include_router(ws_router)


@app.get("/health", tags=["Health"])
async def health_check():
    return {
        "status": "healthy",
        "service": settings.PROJECT_NAME,
        "version": settings.VERSION,
        "environment": settings.ENVIRONMENT
    }


@app.get("/health/ready", tags=["Health"])
async def readiness_check():
    """Dependency check: database + Redis. Returns 503 with per-component status when degraded."""
    from sqlalchemy import text
    checks = {}
    try:
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
        checks["database"] = "ok"
    except Exception as exc:
        logger.error("Readiness: database check failed: %s", exc)
        checks["database"] = f"error: {exc.__class__.__name__}"
    try:
        import redis
        redis.Redis.from_url(settings.REDIS_URL, socket_connect_timeout=3, socket_timeout=3).ping()
        checks["redis"] = "ok"
    except Exception as exc:
        checks["redis"] = f"unavailable: {exc.__class__.__name__}"  # optional in dev (in-memory fallbacks)
    checks["llm_keys"] = {"groq": bool(settings.GROQ_API_KEY), "gemini": bool(settings.GEMINI_API_KEY)}
    checks["telephony"] = "configured" if settings.VOBIZ_AUTH_ID and settings.VOBIZ_AUTH_TOKEN else "simulated"
    ok = checks["database"] == "ok"
    return JSONResponse(status_code=200 if ok else 503, content={"status": "ready" if ok else "degraded", "checks": checks})


@app.get("/", tags=["Root"])
async def root():
    return {
        "message": f"Welcome to {settings.PROJECT_NAME} API. Explore the interactive documentation at /docs",
        "docs_url": "/docs",
        "api_v1": settings.API_V1_STR
    }


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app.main:app", host="0.0.0.0", port=8000, reload=True)
