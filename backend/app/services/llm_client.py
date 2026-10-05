"""Shared LLM access with the Groq -> Gemini fallback chain. Returns None if every provider fails
so callers can drop to their deterministic heuristic."""
import json
import re
from typing import Any, Dict, Optional

import logging
from collections import Counter

import httpx

from app.core.config import settings
from app.core.tracing import trace

logger = logging.getLogger(__name__)

# Counts real provider answers vs. heuristic fallbacks so live evals can prove the LLM was actually used.
llm_stats: Counter = Counter()


class LLMRateLimited(Exception):
    """Raised when a provider answers HTTP 429 so Celery can back off and retry."""


def _extract_json(raw: str) -> Optional[Dict[str, Any]]:
    try:
        return json.loads(raw)
    except Exception:
        match = re.search(r"\{.*\}", raw, re.DOTALL)
        if match:
            try:
                return json.loads(match.group(0))
            except Exception:
                return None
    return None


@trace("llm.complete_json", run_type="llm")
async def complete_json(system: str, user: str, timeout: float = 20.0) -> Optional[Dict[str, Any]]:
    rate_limited = False

    if settings.GROQ_API_KEY:
        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                res = await client.post(
                    "https://api.groq.com/openai/v1/chat/completions",
                    headers={"Authorization": f"Bearer {settings.GROQ_API_KEY}"},
                    json={
                        "model": settings.GROQ_MODEL,
                        "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
                        "response_format": {"type": "json_object"},
                        "temperature": 0.2,
                        **({"reasoning_effort": "low"} if "gpt-oss" in settings.GROQ_MODEL else {}),
                    },
                )
            if res.status_code == 429:
                rate_limited = True
                logger.warning("Groq rate limited (429); trying next provider")
            elif res.status_code == 200:
                parsed = _extract_json(res.json()["choices"][0]["message"]["content"])
                if parsed is not None:
                    llm_stats["json_ok"] += 1
                    return parsed
                logger.warning("Groq returned unparseable JSON; trying next provider")
            else:
                logger.warning("Groq HTTP %s: %s", res.status_code, res.text[:200])
        except Exception as exc:
            logger.warning("Groq call failed: %s: %s", exc.__class__.__name__, exc)

    if settings.GEMINI_API_KEY:
        try:
            url = f"https://generativelanguage.googleapis.com/v1beta/models/{settings.GEMINI_MODEL}:generateContent"
            async with httpx.AsyncClient(timeout=timeout) as client:
                res = await client.post(
                    url,
                    headers={"x-goog-api-key": settings.GEMINI_API_KEY},  # header, not ?key=, so it never lands in logs
                    json={
                        "systemInstruction": {"parts": [{"text": system}]},
                        "contents": [{"role": "user", "parts": [{"text": user}]}],
                        "generationConfig": {
                            "responseMimeType": "application/json",
                            "temperature": 0.2,
                            "thinkingConfig": {"thinkingBudget": 0},
                        },
                    },
                )
            if res.status_code == 429:
                rate_limited = True
                logger.warning("Gemini rate limited (429)")
            elif res.status_code == 200:
                parsed = _extract_json(res.json()["candidates"][0]["content"]["parts"][0]["text"])
                if parsed is not None:
                    llm_stats["json_ok"] += 1
                    return parsed
                logger.warning("Gemini returned unparseable JSON")
            else:
                logger.warning("Gemini HTTP %s: %s", res.status_code, res.text[:200])
        except Exception as exc:
            logger.warning("Gemini call failed: %s: %s", exc.__class__.__name__, exc)

    if rate_limited:
        logger.error("All LLM providers rate limited")
        raise LLMRateLimited("All configured LLM providers are rate limited")
    llm_stats["json_fallback"] += 1
    logger.info("No LLM provider produced a result; caller will use its heuristic fallback")
    return None
