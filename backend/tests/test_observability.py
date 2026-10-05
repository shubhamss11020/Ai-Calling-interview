import os


import logging
import pytest
from tests.conftest import requires_db
from httpx import AsyncClient, ASGITransport


@pytest.fixture
def app():
    from app.main import app
    return app


@pytest.mark.asyncio
async def test_request_id_echoed_and_generated(app):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        assert (await c.get("/health", headers={"X-Request-ID": "abc123"})).headers["X-Request-ID"] == "abc123"
        assert len((await c.get("/health")).headers["X-Request-ID"]) == 12


@requires_db
@pytest.mark.asyncio
async def test_error_shape_has_request_id_and_legacy_detail(app):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        r = await c.get("/api/v1/interviews/does-not-exist", headers={"X-Request-ID": "rid-1"})
    body = r.json()
    assert r.status_code == 404
    assert body["error"]["request_id"] == "rid-1" and body["detail"] == "Interview not found"


@pytest.mark.asyncio
async def test_validation_error_shape(app):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        r = await c.post("/api/v1/interviews/dispatch-batch", json={})
    assert r.status_code == 422 and r.json()["error"]["code"] == "VALIDATION_ERROR"


@requires_db
@pytest.mark.asyncio
async def test_readiness_reports_components(app):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        r = await c.get("/health/ready")
    assert r.status_code == 200
    assert r.json()["checks"]["database"] == "ok" and "redis" in r.json()["checks"]


@pytest.mark.asyncio
async def test_unhandled_exception_returns_500_with_request_id(app, caplog):
    @app.get("/_boom")
    async def boom():
        raise RuntimeError("kaboom")

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        with caplog.at_level(logging.ERROR):
            r = await c.get("/_boom", headers={"X-Request-ID": "rid-boom"})
    assert r.status_code == 500
    assert r.json()["error"]["request_id"] == "rid-boom"
    assert any("kaboom" in (rec.exc_text or "") or "crashed" in rec.getMessage() for rec in caplog.records)


@pytest.mark.asyncio
async def test_public_tunnel_host_only_reaches_webhooks_and_health(app):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="https://abc123.ngrok-free.app") as c:
        assert (await c.get("/health")).status_code == 200
        assert (await c.get("/api/v1/jobs")).status_code == 403
        assert (await c.get("/api/v1/candidates")).status_code == 403
        assert (await c.get("/docs")).status_code == 403
        # webhooks stay reachable (token check happens inside the handler)
        r = await c.post("/api/v1/interviews/telephony/vobiz/answer?interview_id=x&token=bad")
        assert r.status_code == 200 and "could not be verified" in r.text
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://localhost:8000") as c:
        assert (await c.get("/docs")).status_code == 200
