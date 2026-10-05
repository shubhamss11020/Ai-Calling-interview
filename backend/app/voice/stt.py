"""Live speech-to-text over Deepgram's streaming WebSocket (nova-3, linear16)."""
import asyncio
import json
import logging
from dataclasses import dataclass
from typing import Awaitable, Callable, Iterable, Optional
from urllib.parse import urlencode

import websockets

from app.core.config import settings

logger = logging.getLogger(__name__)


@dataclass
class SttEvent:
    kind: str                 # "speech_started" | "transcript" | "utterance_end"
    text: str = ""
    is_final: bool = False
    speech_final: bool = False


class DeepgramLive:
    def __init__(self, on_event: Callable[[SttEvent], Awaitable[None]], keyterms: Iterable[str] = ()):
        self._on_event = on_event
        self._keyterms = [k for k in keyterms if k][:20]
        self._ws = None
        self._recv_task: Optional[asyncio.Task] = None
        self._keepalive_task: Optional[asyncio.Task] = None
        self._closing = False
        self.connected = asyncio.Event()

    def _url(self) -> str:
        params = [
            ("model", settings.DEEPGRAM_STT_MODEL),
            ("language", "en"),
            ("encoding", "linear16"),
            ("sample_rate", settings.STREAM_SAMPLE_RATE),
            ("channels", 1),
            ("interim_results", "true"),
            ("endpointing", settings.VOICE_ENDPOINTING_MS),
            ("utterance_end_ms", 1000),
            ("vad_events", "true"),
            ("smart_format", "true"),
        ]
        if settings.DEEPGRAM_STT_MODEL.startswith("nova-3"):
            params += [("keyterm", k) for k in self._keyterms]  # biases recognition toward the role's tech terms
        return "wss://api.deepgram.com/v1/listen?" + urlencode(params)

    async def connect(self) -> None:
        self._ws = await websockets.connect(
            self._url(),
            additional_headers={"Authorization": f"Token {settings.DEEPGRAM_API_KEY}"},
            max_size=None,
            open_timeout=8,
        )
        self.connected.set()
        self._recv_task = asyncio.create_task(self._receive())
        self._keepalive_task = asyncio.create_task(self._keepalive())
        logger.info("Deepgram live STT connected")

    async def send_audio(self, pcm: bytes) -> None:
        if self._ws is None or self._closing:
            return
        try:
            await self._ws.send(pcm)
        except Exception as exc:
            logger.warning("Deepgram send failed (%s); reconnecting", exc)
            await self._reconnect()

    async def _reconnect(self) -> None:
        if self._closing:
            return
        self.connected.clear()
        for attempt in range(3):
            try:
                await self.connect()
                return
            except Exception as exc:
                logger.warning("Deepgram reconnect %s/3 failed: %s", attempt + 1, exc)
                await asyncio.sleep(0.5)
        logger.error("Deepgram STT unavailable; call will rely on silence handling")

    async def _keepalive(self) -> None:
        while not self._closing:
            await asyncio.sleep(5)
            try:
                if self._ws is not None:
                    await self._ws.send(json.dumps({"type": "KeepAlive"}))
            except Exception:
                return

    async def _receive(self) -> None:
        try:
            async for raw in self._ws:
                if isinstance(raw, bytes):
                    continue
                msg = json.loads(raw)
                kind = msg.get("type")
                if kind == "SpeechStarted":
                    await self._on_event(SttEvent("speech_started"))
                elif kind == "UtteranceEnd":
                    await self._on_event(SttEvent("utterance_end"))
                elif kind == "Results":
                    alt = (msg.get("channel", {}).get("alternatives") or [{}])[0]
                    text = (alt.get("transcript") or "").strip()
                    if text or msg.get("speech_final"):
                        await self._on_event(SttEvent(
                            "transcript", text, bool(msg.get("is_final")), bool(msg.get("speech_final"))))
        except Exception as exc:
            if not self._closing:
                logger.warning("Deepgram receive loop ended: %s", exc)
                asyncio.create_task(self._reconnect())

    async def close(self) -> None:
        self._closing = True
        for task in (self._keepalive_task, self._recv_task):
            if task:
                task.cancel()
        if self._ws is not None:
            try:
                await self._ws.send(json.dumps({"type": "CloseStream"}))
                await self._ws.close()
            except Exception:
                pass
