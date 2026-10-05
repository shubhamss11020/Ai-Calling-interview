"""Streaming text-to-speech via Deepgram Aura (linear16, 16 kHz). `aura-asteria-en` returns its first audio in
~0.35 s (the newer aura-2 voices take ~1.4 s, too slow for a phone conversation). One shared keep-alive HTTP client
avoids a TLS handshake on every utterance."""
import logging
import re
from typing import AsyncIterator, Dict, Optional

import httpx

from app.core.config import settings
from app.core.tracing import trace

logger = logging.getLogger(__name__)

_client: Optional[httpx.AsyncClient] = None
_filler_cache: Dict[str, bytes] = {}

FILLERS = ["Mm-hmm, let me think about that.", "Okay, one moment.", "Good, let me consider that."]


def _http() -> httpx.AsyncClient:
    global _client
    if _client is None or _client.is_closed:
        _client = httpx.AsyncClient(
            timeout=httpx.Timeout(20.0, connect=5.0),
            limits=httpx.Limits(max_keepalive_connections=4, keepalive_expiry=60),
        )
    return _client


async def stream_tts(text: str) -> AsyncIterator[bytes]:
    """Yield PCM16LE chunks (even-length) as they arrive. Raises on provider errors so callers can degrade."""
    if not settings.DEEPGRAM_API_KEY:
        raise RuntimeError("DEEPGRAM_API_KEY not set")
    url = (
        f"https://api.deepgram.com/v1/speak?model={settings.DEEPGRAM_TTS_MODEL}"
        f"&encoding=linear16&sample_rate={settings.STREAM_SAMPLE_RATE}&container=none"
    )
    headers = {"Authorization": f"Token {settings.DEEPGRAM_API_KEY}", "Content-Type": "application/json"}
    carry = b""
    async with _http().stream("POST", url, headers=headers, json={"text": text[:1900]}) as res:
        if res.status_code != 200:
            body = (await res.aread())[:200]
            raise RuntimeError(f"Deepgram TTS HTTP {res.status_code}: {body!r}")
        async for chunk in res.aiter_bytes():
            data = carry + chunk
            usable = len(data) // 2 * 2
            carry = data[usable:]
            if usable:
                yield data[:usable]


@trace("voice.tts_full", run_type="tool")
async def synthesize(text: str) -> bytes:
    return b"".join([c async for c in stream_tts(text)])


def split_sentences(text: str) -> list:
    """Split into speakable sentences so the first (short) one can start playing while the rest synthesize."""
    parts = [p.strip() for p in re.split(r"(?<=[.!?])\s+", text.strip()) if p.strip()]
    return parts or [text.strip()]


def tighten_reply(text: str, max_words: int = 30) -> str:
    """Phone replies must be short: keep whole sentences up to ~max_words (always at least one)."""
    kept, words = [], 0
    for sentence in split_sentences(text):
        n = len(sentence.split())
        if kept and words + n > max_words:
            break
        kept.append(sentence)
        words += n
    return " ".join(kept)


async def prewarm_fillers() -> None:
    """Synthesize the 'thinking' fillers once so they can be played instantly mid-call."""
    for text in FILLERS:
        if text not in _filler_cache:
            try:
                _filler_cache[text] = await synthesize(text)
            except Exception as exc:
                logger.warning("Filler prewarm failed: %s", exc)
                return


def cached_filler(index: int) -> Optional[bytes]:
    if not _filler_cache:
        return None
    return _filler_cache.get(FILLERS[index % len(FILLERS)])


# ----------------------------------------------------------------------------- persistent WebSocket TTS
# One connection per call: no TLS handshake per utterance (~1 s each when the server is far from Deepgram).
import asyncio  # noqa: E402
import json  # noqa: E402

import websockets  # noqa: E402


class TtsSession:
    """Deepgram Aura over a persistent WebSocket. Falls back to per-request HTTPS if the socket is unusable."""

    def __init__(self) -> None:
        self._ws = None
        self._lock = asyncio.Lock()
        self._connecting: Optional[asyncio.Task] = None

    def _url(self) -> str:
        return (
            f"wss://api.deepgram.com/v1/speak?model={settings.DEEPGRAM_TTS_MODEL}"
            f"&encoding=linear16&sample_rate={settings.STREAM_SAMPLE_RATE}"
        )

    async def connect(self) -> None:
        self._ws = await websockets.connect(
            self._url(),
            additional_headers={"Authorization": f"Token {settings.DEEPGRAM_API_KEY}"},
            max_size=None,
            open_timeout=8,
            ping_interval=None,  # Deepgram's TTS socket doesn't need client pings; avoids spurious keepalive timeouts
        )
        logger.info("Deepgram TTS websocket connected")

    async def ensure_connected(self) -> None:
        async with self._lock:
            if not self._usable():
                await self.connect()

    def _usable(self) -> bool:
        ws = self._ws
        return ws is not None and ws.state.name == "OPEN"

    async def close(self) -> None:
        if self._ws is not None:
            try:
                await self._ws.send(json.dumps({"type": "Close"}))
                await self._ws.close()
            except Exception:
                pass

    async def stream(self, text: str) -> AsyncIterator[bytes]:
        """Yield PCM16LE chunks for `text` (sentences are pipelined server-side, so the first one starts early)."""
        async with self._lock:
            if not self._usable():
                try:
                    await self.connect()
                except Exception as exc:
                    logger.warning("TTS websocket unavailable (%s); using HTTPS", exc)
                    async for chunk in stream_tts(text):
                        yield chunk
                    return

            ws = self._ws
            completed = False
            carry = b""
            try:
                for sentence in split_sentences(text):
                    await ws.send(json.dumps({"type": "Speak", "text": sentence[:1900]}))
                await ws.send(json.dumps({"type": "Flush"}))
                while True:
                    msg = await asyncio.wait_for(ws.recv(), timeout=12)
                    if isinstance(msg, bytes):
                        data = carry + msg
                        usable = len(data) // 2 * 2
                        carry = data[usable:]
                        if usable:
                            yield data[:usable]
                        continue
                    kind = json.loads(msg).get("type")
                    if kind == "Flushed":
                        completed = True
                        return
                    if kind in ("Warning", "Error"):
                        logger.warning("Deepgram TTS %s: %s", kind, msg[:200])
            finally:
                if not completed:
                    await self._reset(ws)

    async def _reset(self, ws) -> None:
        """Interrupted/failed utterance: tell Deepgram to drop what's queued and discard until it confirms."""
        try:
            await ws.send(json.dumps({"type": "Clear"}))
            async with asyncio.timeout(2):
                while True:
                    msg = await ws.recv()
                    if isinstance(msg, str) and json.loads(msg).get("type") == "Cleared":
                        return
        except Exception:
            self._ws = None  # force a clean reconnect next time
