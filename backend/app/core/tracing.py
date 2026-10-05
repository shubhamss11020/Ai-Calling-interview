"""LangSmith tracing (works with plain functions; no LangChain required).

Enable with LANGSMITH_TRACING=true and LANGSMITH_API_KEY. When disabled, `trace` returns the function untouched
so there is zero overhead and no network calls. Long strings (resumes, transcripts) are truncated before being
sent, so candidate PII does not leave the system in full.
"""
import functools
import logging
import os
from typing import Any, Callable, Dict

from app.core.config import settings

logger = logging.getLogger(__name__)

_MAX_TRACE_CHARS = 400


def configure_tracing() -> bool:
    """langsmith reads os.environ, but pydantic loads .env privately, so mirror the values across."""
    enabled = bool(settings.LANGSMITH_TRACING and settings.LANGSMITH_API_KEY)
    if enabled:
        os.environ["LANGSMITH_TRACING"] = "true"
        os.environ["LANGSMITH_API_KEY"] = settings.LANGSMITH_API_KEY
        os.environ["LANGSMITH_PROJECT"] = settings.LANGSMITH_PROJECT
        if settings.LANGSMITH_ENDPOINT:
            os.environ["LANGSMITH_ENDPOINT"] = settings.LANGSMITH_ENDPOINT
        logger.info("LangSmith tracing enabled (project=%s)", settings.LANGSMITH_PROJECT)
    else:
        os.environ["LANGSMITH_TRACING"] = "false"
    return enabled


def _shorten(value: Any) -> Any:
    if isinstance(value, str):
        return value if len(value) <= _MAX_TRACE_CHARS else value[:_MAX_TRACE_CHARS] + f"…[+{len(value) - _MAX_TRACE_CHARS} chars]"
    if isinstance(value, dict):
        return {k: _shorten(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_shorten(v) for v in value[:20]]
    return value


def _process_inputs(inputs: Dict[str, Any]) -> Dict[str, Any]:
    # drop non-serialisable handles such as DB sessions
    return {k: _shorten(v) for k, v in inputs.items() if k not in ("db", "config", "session")}


def trace(name: str, run_type: str = "chain") -> Callable:
    """Decorator: record the call in LangSmith when tracing is enabled, otherwise a no-op."""

    def decorator(fn: Callable) -> Callable:
        if not (settings.LANGSMITH_TRACING and settings.LANGSMITH_API_KEY):
            return fn
        try:
            from langsmith import traceable
        except ImportError:  # pragma: no cover
            logger.warning("langsmith not installed; tracing disabled")
            return fn
        return functools.wraps(fn)(
            traceable(name=name, run_type=run_type, process_inputs=_process_inputs, process_outputs=_shorten_outputs)(fn)
        )

    return decorator


def _shorten_outputs(outputs: Any) -> Any:
    return _shorten(outputs if isinstance(outputs, dict) else {"output": outputs})
