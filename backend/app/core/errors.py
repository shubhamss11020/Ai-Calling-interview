"""Uniform error responses: {"error": {"code", "message", "request_id", "details"?}}.
Unhandled exceptions are logged with a stack trace and return a generic 500 carrying the request ID."""
import logging
from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from app.core.logging import request_id_ctx

logger = logging.getLogger("app.errors")


def _body(code: str, message, details=None) -> dict:
    err = {"code": code, "message": message, "request_id": request_id_ctx.get()}
    if details is not None:
        err["details"] = details
    return {"error": err}


def register_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(HTTPException)
    async def http_exc(_: Request, exc: HTTPException):
        if exc.status_code >= 500:
            logger.error("HTTP %s: %s", exc.status_code, exc.detail)
        detail = exc.detail
        message = detail if isinstance(detail, str) else (detail.get("message", "Request failed") if isinstance(detail, dict) else str(detail))
        return JSONResponse(
            status_code=exc.status_code,
            content={**_body(f"HTTP_{exc.status_code}", message, detail if not isinstance(detail, str) else None),
                     "detail": detail},  # keep FastAPI's "detail" key for existing clients
            headers=getattr(exc, "headers", None),
        )

    @app.exception_handler(RequestValidationError)
    async def validation_exc(_: Request, exc: RequestValidationError):
        errors = exc.errors()
        logger.warning("Validation failed: %s", [(e["loc"], e["msg"]) for e in errors])
        return JSONResponse(
            status_code=422,
            content={**_body("VALIDATION_ERROR", "Request validation failed", errors), "detail": errors},
        )

    @app.exception_handler(Exception)
    async def unhandled_exc(request: Request, exc: Exception):
        logger.exception("Unhandled error on %s %s", request.method, request.url.path)
        return JSONResponse(
            status_code=500,
            content=_body("INTERNAL_ERROR", "Internal server error. Quote the request_id when reporting this."),
            headers={"X-Request-ID": request_id_ctx.get()},
        )
