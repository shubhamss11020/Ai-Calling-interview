"""Live phone interview over a Vobiz bidirectional audio stream.

    caller audio (Vobiz, L16 big-endian) -> byte-swap -> Deepgram live STT -> end-of-turn -> interviewer agent (in memory,
    one LLM call) -> Deepgram Aura TTS (streamed) -> playAudio frames back to the caller.

Latency design (target < 2.5 s from the candidate finishing a sentence to the AI starting its reply):
  * nothing on the hot path touches the database or embeddings: job knowledge and a resume brief are preloaded once;
  * the agent runs in `fast` mode (heuristic assessment, a single LLM call);
  * the reply is streamed to the caller as soon as the first audio bytes exist;
  * if the reply isn't ready in VOICE_FILLER_AFTER_SECONDS, a pre-synthesised "let me think" filler plays instantly;
  * barge-in: if the candidate talks over the AI, queued audio is flushed (clearAudio) and the AI listens.
Persistence (transcript + agent memory) happens in the background after each turn.
"""
import asyncio
import base64
import json
import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from fastapi import WebSocket
from sqlalchemy import select, update
from sqlalchemy.orm import selectinload

from app.agents.interview_agent import run_turn
from app.core.config import settings
from app.core.database import AsyncSessionLocal
from app.models.application import Application
from app.models.interview import Interview
from app.models.knowledge import JobKnowledge
from app.voice import tts
from app.voice.audio import frame_bytes, pcm_seconds, swap_endian16
from app.voice.stt import DeepgramLive, SttEvent

logger = logging.getLogger(__name__)

MAX_SILENT_PROMPTS = 2
MIN_WORDS_TO_INTERRUPT = 3  # fewer words while the AI is talking = backchannel ("mm-hmm"), not a barge-in


@dataclass
class SessionContext:
    interview_id: str
    job_id: str
    job_title: str
    candidate_id: str
    required_skills: List[str]
    max_turns: int
    transcript: List[Dict[str, Any]]
    memory: Dict[str, Any]
    knowledge: List[Dict[str, Any]] = field(default_factory=list)
    resume_brief: str = ""


async def load_context(interview_id: str) -> Optional[SessionContext]:
    """One-time DB read at call start; everything the live loop needs is then held in memory."""
    async with AsyncSessionLocal() as db:
        res = await db.execute(
            select(Interview)
            .where(Interview.id == interview_id)
            .options(
                selectinload(Interview.application).selectinload(Application.job),
                selectinload(Interview.application).selectinload(Application.candidate),
            )
        )
        itw = res.scalars().first()
        if not itw or itw.status != "IN_PROGRESS":
            logger.warning("Stream rejected: interview=%s status=%s", interview_id, itw.status if itw else None)
            return None
        job, cand = itw.application.job, itw.application.candidate
        rows = (await db.execute(
            select(JobKnowledge.kind, JobKnowledge.topic, JobKnowledge.content)
            .where(JobKnowledge.job_id == job.id).limit(60)
        )).all()
        knowledge = [{"kind": r[0], "topic": r[1], "content": r[2]} for r in rows]
        skills = ", ".join(cand.extracted_skills or [])
        brief = f"Skills: {skills}. {cand.total_experience_years or 0} years experience. {(cand.raw_resume_text or '')[:700]}"
        return SessionContext(
            interview_id=itw.id, job_id=job.id, job_title=job.title, candidate_id=cand.id,
            required_skills=list(job.required_skills or []),
            max_turns=int((job.interview_config or {}).get("max_questions", 6)),
            transcript=list(itw.transcript or []), memory=dict(itw.agent_state or {}),
            knowledge=knowledge, resume_brief=brief,
        )


class VoiceSession:
    def __init__(self, ws: WebSocket, ctx: SessionContext):
        self.ws = ws
        self.ctx = ctx
        self.stt = DeepgramLive(self._on_stt, keyterms=ctx.required_skills)
        self.tts = tts.TtsSession()
        self.stream_id: Optional[str] = None
        self.encoding = "audio/x-l16"
        self.rate = settings.STREAM_SAMPLE_RATE
        self.t_start = time.monotonic()
        self.audio_end_at = 0.0       # monotonic time when audio already sent to the caller finishes playing
        self.last_speech = time.monotonic()
        self.turn_end_at = time.monotonic()
        self.buffer: List[str] = []
        self.pending: List[str] = []
        self.silences = 0
        self.filler_i = 0
        self.began = False
        self.finished = False
        self.turn_running = False
        self.speak_task: Optional[asyncio.Task] = None
        self.turn_task: Optional[asyncio.Task] = None
        self._tasks: List[asyncio.Task] = []
        self._persist_lock = asyncio.Lock()
        self._warned_codec = False
        self._seen_events: set = set()

    # ------------------------------------------------------------------ lifecycle

    async def run(self) -> None:
        await self.ws.accept()
        logger.info("interview=%s stream connected", self.ctx.interview_id)
        self._tasks.append(asyncio.create_task(self.stt.connect()))
        self._tasks.append(asyncio.create_task(self._warm_tts()))
        self._tasks.append(asyncio.create_task(tts.prewarm_fillers()))
        self._tasks.append(asyncio.create_task(self._watchdog()))
        try:
            async for raw in self.ws.iter_text():
                try:
                    await self._on_vobiz(json.loads(raw))
                except json.JSONDecodeError:
                    continue
        except Exception as exc:
            logger.info("interview=%s stream ended: %s", self.ctx.interview_id, exc)
        finally:
            await self._shutdown()

    async def _shutdown(self) -> None:
        self.finished = True
        for t in [self.speak_task, self.turn_task, *self._tasks]:
            if t and not t.done():
                t.cancel()
        await self.stt.close()
        await self.tts.close()
        await self._persist()
        logger.info("interview=%s stream closed after %.0fs", self.ctx.interview_id, time.monotonic() - self.t_start)

    async def _on_vobiz(self, msg: Dict[str, Any]) -> None:
        event = msg.get("event")
        if event not in self._seen_events:  # first sight of each event type: makes carrier-side surprises visible in logs
            self._seen_events.add(event)
            logger.info("interview=%s first Vobiz %r event: %s", self.ctx.interview_id, event,
                        json.dumps({k: (v if k != "media" else "...") for k, v in msg.items()})[:300])
        if event == "start":
            start = msg.get("start") or msg
            self.stream_id = start.get("streamId") or msg.get("streamId")
            fmt = start.get("mediaFormat") or {}
            self.encoding = fmt.get("encoding") or self.encoding
            self.rate = int(fmt.get("sampleRate") or self.rate)
            logger.info("interview=%s stream start: id=%s format=%s@%s", self.ctx.interview_id, self.stream_id, self.encoding, self.rate)
            if not self.began:
                self.began = True
                self._tasks.append(asyncio.create_task(self._begin()))
        elif event == "media":
            media = msg.get("media") or {}
            if media.get("track", "inbound") == "outbound":
                return
            if not self.began:  # tolerate a carrier that streams audio without a 'start' handshake
                self.began = True
                self._tasks.append(asyncio.create_task(self._begin()))
            payload = base64.b64decode(media.get("payload") or "")
            if "l16" in self.encoding.lower():
                payload = swap_endian16(payload)
            elif not self._warned_codec:
                self._warned_codec = True
                logger.error("Unsupported inbound codec %s; expected audio/x-l16", self.encoding)
            await self.stt.send_audio(payload)
        elif event == "stop":
            self.finished = True

    async def _warm_tts(self) -> None:
        try:
            await self.tts.ensure_connected()
        except Exception as exc:
            logger.warning("interview=%s TTS websocket warm-up failed: %s", self.ctx.interview_id, exc)

    async def _begin(self) -> None:
        opening = next((t["text"] for t in reversed(self.ctx.transcript) if t.get("speaker") == "AI Interviewer"),
                       "Hello, let's begin. Please tell me about your background.")
        await self._speak(opening)

    # ------------------------------------------------------------------ speaking

    async def _send(self, payload: Dict[str, Any]) -> None:
        if self.finished:
            return
        try:
            await self.ws.send_text(json.dumps(payload))
        except Exception:
            self.finished = True

    async def _send_pcm(self, pcm: bytes) -> None:
        """Queue PCM16LE for the caller in 20 ms frames and track when it will finish playing."""
        size = frame_bytes(self.rate)
        for i in range(0, len(pcm), size):
            await self._send({
                "event": "playAudio",
                "streamId": self.stream_id,
                "media": {
                    "contentType": self.encoding,
                    "sampleRate": self.rate,
                    "payload": base64.b64encode(pcm[i : i + size]).decode(),
                },
            })
        now = time.monotonic()
        self.audio_end_at = max(self.audio_end_at, now) + pcm_seconds(len(pcm), self.rate)

    async def _speak(self, text: str) -> None:
        """Stream TTS audio to the caller. Returns once the audio is queued (not when it finishes playing)."""
        if self.speak_task and not self.speak_task.done():
            self.speak_task.cancel()
        self.speak_task = asyncio.create_task(self._speak_impl(text))
        await self.speak_task

    async def _speak_impl(self, text: str) -> None:
        """Stream TTS audio (persistent websocket) to the caller as it is generated."""
        t0 = time.monotonic()
        first = True
        buf = b""
        size = frame_bytes(self.rate)
        stream = self.tts.stream(text)
        try:
            async for chunk in stream:
                if first:
                    now = time.monotonic()
                    logger.info(
                        "interview=%s TTS first audio in %.2fs | candidate stopped talking -> AI audio: %.2fs",
                        self.ctx.interview_id, now - t0, now - self.last_speech,
                    )
                    first = False
                buf += chunk
                whole = len(buf) // size * size
                if whole:
                    await self._send_pcm(buf[:whole])
                    buf = buf[whole:]
            if buf:
                await self._send_pcm(buf)
        except asyncio.CancelledError:
            return  # barge-in or shutdown: swallow so callers awaiting this task are not cancelled
        except Exception as exc:
            logger.error("interview=%s TTS failed: %s", self.ctx.interview_id, exc)
        finally:
            await stream.aclose()

    async def _barge_in(self) -> None:
        logger.info("interview=%s barge-in: candidate interrupted the AI", self.ctx.interview_id)
        if self.speak_task and not self.speak_task.done():
            self.speak_task.cancel()
        await self._send({"event": "clearAudio", "streamId": self.stream_id})
        self.audio_end_at = time.monotonic()

    # ------------------------------------------------------------------ listening

    def _ai_audio_playing(self) -> bool:
        return time.monotonic() < self.audio_end_at

    async def _on_stt(self, ev: SttEvent) -> None:
        now = time.monotonic()
        if ev.kind == "speech_started":
            self.last_speech = now
            return
        if ev.kind == "utterance_end":
            if self.buffer:
                await self._commit()
            return
        if ev.text:
            self.last_speech = now
            if self._ai_audio_playing() and len(ev.text.split()) >= MIN_WORDS_TO_INTERRUPT:
                await self._barge_in()
        if ev.is_final and ev.text:
            self.buffer.append(ev.text)
        if ev.speech_final and self.buffer:
            await self._commit()

    async def _commit(self) -> None:
        text = " ".join(self.buffer).strip()
        self.buffer = []
        if not text or self.finished:
            return
        if self._ai_audio_playing() and len(text.split()) < MIN_WORDS_TO_INTERRUPT:
            return  # backchannel while the AI is talking
        self.pending.append(text)
        if not self.turn_running:
            self.turn_task = asyncio.create_task(self._turn_loop())

    # ------------------------------------------------------------------ turns

    async def _turn_loop(self) -> None:
        self.turn_running = True
        try:
            while self.pending and not self.finished:
                text = " ".join(self.pending)
                self.pending.clear()
                await self._handle_turn(text)
        except asyncio.CancelledError:
            pass
        except Exception:
            logger.exception("interview=%s turn failed", self.ctx.interview_id)
            await self._speak("Sorry, could you say that again?")
        finally:
            self.turn_running = False
            self.turn_end_at = time.monotonic()

    def _elapsed(self) -> int:
        return int(time.monotonic() - self.t_start)

    async def _agent_reply(self, text: str):
        from app.services.interview_orchestrator import Turn

        ctx = self.ctx
        self.silences = 0
        ctx.memory["silences"] = 0
        ctx.transcript.append({"speaker": "Candidate", "text": text, "timestamp": self._elapsed()})
        history = [
            {"role": "assistant" if t["speaker"] == "AI Interviewer" else "user", "content": t["text"]}
            for t in ctx.transcript
        ]
        state = await run_turn({
            "job_id": ctx.job_id, "job_title": ctx.job_title, "candidate_id": ctx.candidate_id,
            "required_skills": ctx.required_skills, "history": history, "max_turns": ctx.max_turns,
            "ai_turns": sum(1 for t in ctx.transcript if t.get("speaker") == "AI Interviewer"),
            "covered_skills": ctx.memory.get("covered_skills", []),
            "followups_used": ctx.memory.get("followups_used", 0),
            "valid_answers": ctx.memory.get("valid_answers", 0),
            "fast": True, "preloaded_knowledge": ctx.knowledge, "resume_brief": ctx.resume_brief,
        }, db=None)
        reply = state["reply"]
        ctx.transcript.append({"speaker": "AI Interviewer", "text": reply, "timestamp": self._elapsed()})
        ctx.memory.update(
            covered_skills=state.get("covered_skills", []),
            followups_used=state.get("followups_used", 0),
            valid_answers=state.get("valid_answers", 0),
        )
        return Turn(reply, done=bool(state.get("done")))

    async def _handle_turn(self, text: str) -> None:
        t0 = time.monotonic()
        reply_task = asyncio.create_task(self._agent_reply(text))
        try:
            turn = await asyncio.wait_for(asyncio.shield(reply_task), settings.VOICE_FILLER_AFTER_SECONDS)
        except asyncio.TimeoutError:
            filler = tts.cached_filler(self.filler_i)
            self.filler_i += 1
            if filler and not self._ai_audio_playing():
                await self._send_pcm(filler)
            turn = await reply_task
        logger.info("interview=%s reply ready in %.2fs", self.ctx.interview_id, time.monotonic() - t0)
        self._schedule_persist()
        await self._speak(turn.say)
        if turn.done:
            await self._finish_after_audio()

    async def _finish_after_audio(self) -> None:
        """Let the closing line finish playing, then end the stream (Vobiz then runs <Hangup/>)."""
        await asyncio.sleep(max(0.0, self.audio_end_at - time.monotonic()) + 0.6)
        self.finished = True
        await self._persist()
        try:
            await self.ws.close()
        except Exception:
            pass

    # ------------------------------------------------------------------ silence / limits

    async def _watchdog(self) -> None:
        try:
            while not self.finished:
                await asyncio.sleep(0.5)
                now = time.monotonic()
                if now - self.t_start > settings.MAX_CALL_SECONDS:
                    await self._end_call("We have reached the time limit for this interview. Thank you. Goodbye.")
                    return
                if not self.began or self.turn_running or now < self.audio_end_at:
                    continue
                idle_since = max(self.last_speech, self.audio_end_at, self.turn_end_at)
                if now - idle_since > settings.VOICE_SILENCE_SECONDS:
                    await self._handle_silence()
        except asyncio.CancelledError:
            pass

    async def _handle_silence(self) -> None:
        self.silences += 1
        self.ctx.memory["silences"] = self.silences
        self.turn_end_at = time.monotonic()
        if self.silences >= MAX_SILENT_PROMPTS:
            await self._end_call("I am not able to hear you, so I will end the call here. Please call back to try again. Goodbye.")
            return
        last_q = next((t["text"] for t in reversed(self.ctx.transcript) if t.get("speaker") == "AI Interviewer"), "Please go ahead.")
        await self._speak("Sorry, I did not catch that. " + last_q)

    async def _end_call(self, text: str) -> None:
        self.ctx.transcript.append({"speaker": "AI Interviewer", "text": text, "timestamp": self._elapsed()})
        await self._speak(text)
        await self._finish_after_audio()

    # ------------------------------------------------------------------ persistence

    def _schedule_persist(self) -> None:
        self._tasks.append(asyncio.create_task(self._persist()))

    async def _persist(self) -> None:
        async with self._persist_lock:
            try:
                async with AsyncSessionLocal() as db:
                    await db.execute(
                        update(Interview).where(Interview.id == self.ctx.interview_id)
                        .values(transcript=list(self.ctx.transcript), agent_state=dict(self.ctx.memory))
                    )
                    await db.commit()
            except Exception as exc:
                logger.error("interview=%s persist failed: %s", self.ctx.interview_id, exc)
