"""Text embeddings via Gemini (free tier). Returns None when unavailable so RAG callers can degrade gracefully."""
import logging
import math
from typing import List, Optional

import httpx

from app.core.config import settings
from app.core.tracing import trace

logger = logging.getLogger(__name__)

_BASE = "https://generativelanguage.googleapis.com/v1beta"
_BATCH = 50  # Gemini batchEmbedContents limit is 100; stay well under


def _normalise(vec: List[float]) -> List[float]:
    """Truncated (outputDimensionality < native) Gemini embeddings must be re-normalised for cosine search."""
    norm = math.sqrt(sum(x * x for x in vec)) or 1.0
    return [x / norm for x in vec]


def embeddings_available() -> bool:
    return bool(settings.RAG_ENABLED and settings.GEMINI_API_KEY)


@trace("embed_texts", run_type="embedding")
async def embed_texts(texts: List[str], task: str = "RETRIEVAL_DOCUMENT") -> Optional[List[List[float]]]:
    """Embed `texts` (task: RETRIEVAL_DOCUMENT for stored content, RETRIEVAL_QUERY for search queries)."""
    if not texts:
        return []
    if not embeddings_available():
        return None

    model = f"models/{settings.EMBEDDING_MODEL}"
    out: List[List[float]] = []
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            for i in range(0, len(texts), _BATCH):
                batch = texts[i : i + _BATCH]
                res = await client.post(
                    f"{_BASE}/{model}:batchEmbedContents",
                    headers={"x-goog-api-key": settings.GEMINI_API_KEY},
                    json={
                        "requests": [
                            {
                                "model": model,
                                "content": {"parts": [{"text": t[:8000]}]},
                                "taskType": task,
                                "outputDimensionality": settings.EMBEDDING_DIM,
                            }
                            for t in batch
                        ]
                    },
                )
                if res.status_code != 200:
                    logger.warning("Embedding HTTP %s: %s", res.status_code, res.text[:200])
                    return None
                out.extend(_normalise(e["values"]) for e in res.json()["embeddings"])
    except Exception as exc:
        logger.warning("Embedding call failed: %s: %s", exc.__class__.__name__, exc)
        return None
    if len(out) != len(texts):
        logger.warning("Embedding count mismatch (%s != %s)", len(out), len(texts))
        return None
    return out


async def embed_query(text: str) -> Optional[List[float]]:
    vecs = await embed_texts([text], task="RETRIEVAL_QUERY")
    return vecs[0] if vecs else None
