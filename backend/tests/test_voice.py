"""Voice session logic with fake Deepgram/TTS/Vobiz (no network)."""
import asyncio
import base64
import json

import pytest

from app.core.config import settings
from app.voice import session as vs
from app.voice.audio import frame_bytes, iter_frames, pcm_seconds, swap_endian16
from app.voice.stt import SttEvent
from app.voice.tts import split_sentences, tighten_reply


# ----------------------------------------------------------------------------- pure helpers

def test_swap_endian_roundtrip_and_values():
    le = (1).to_bytes(2, "little", signed=True) + (-2).to_bytes(2, "little", signed=True)
    be = swap_endian16(le)
    assert be == (1).to_bytes(2, "big", signed=True) + (-2).to_bytes(2, "big", signed=True)
    assert swap_endian16(be) == le
    assert swap_endian16(b"\x01\x02\x03") == swap_endian16(b"\x01\x02")  # odd trailing byte dropped


def test_frame_math():
    assert frame_bytes(16000) == 640
    assert [len(f) for f in iter_frames(b"x" * 1500, 640)] == [640, 640, 220]
    assert pcm_seconds(32000, 16000) == 1.0


def test_split_and_tighten():
    assert split_sentences("Great. Tell me more? Why!") == ["Great.", "Tell me more?", "Why!"]
    long = "That sounds good. " + "Can you explain how you handled retries and backoff in that queue consumer? " * 3
    out = tighten_reply(long, max_words=20)
    assert len(out.split()) <= 20 + 15 and out.startswith("That sounds good.")
    assert tighten_reply("One very long sentence " + "word " * 60) != ""  # always keeps at least one sentence


# ----------------------------------------------------------------------------- session with fakes

class FakeWS:
    def __init__(self):
        self.inbox: asyncio.Queue = asyncio.Queue()
        self.sent = []
        self.closed = False

    async def accept(self):
        pass

    async def iter_text(self):
        while True:
            item = await self.inbox.get()
            if item is None:
                return
            yield item

    async def send_text(self, text):
        self.sent.append(json.loads(text))

    async def close(self, code=1000):
        self.closed = True
        await self.inbox.put(None)

    def events(self, name):
        return [m for m in self.sent if m.get("event") == name]


class FakeStt:
    def __init__(self, on_event, keyterms=()):
        self.audio = []

    async def connect(self):
        pass

    async def send_audio(self, pcm):
        self.audio.append(pcm)

    async def close(self):
        pass


class FakeTts:
    def __init__(self):
        self.said = []

    async def ensure_connected(self):
        pass

    async def close(self):
        pass

    async def stream(self, text):
        self.said.append(text)
        for _ in range(3):
            yield b"\x01\x00" * 3200  # 0.2 s of audio per chunk
            await asyncio.sleep(0)


def make_ctx(**kw):
    base = dict(
        interview_id="int-1", job_id="job-1", job_title="Backend Engineer", candidate_id="c-1",
        required_skills=["Python", "Redis"], max_turns=4,
        transcript=[{"speaker": "AI Interviewer", "text": "Welcome. Tell me about your background.", "timestamp": 0}],
        memory={}, knowledge=[], resume_brief="Skills: Python.")
    base.update(kw)
    return vs.SessionContext(**base)


@pytest.fixture
def harness(monkeypatch):
    monkeypatch.setattr(vs, "DeepgramLive", FakeStt)
    monkeypatch.setattr(vs.tts, "TtsSession", FakeTts)
    monkeypatch.setattr(vs.tts, "prewarm_fillers", lambda: asyncio.sleep(0))
    monkeypatch.setattr(vs.tts, "cached_filler", lambda i: None)
    monkeypatch.setattr(vs.VoiceSession, "_persist", lambda self: asyncio.sleep(0))
    replies = {"text": "Good. Which Redis data structures did you use?", "done": False}

    async def fake_run_turn(state, db=None):
        assert state["fast"] is True and db is None  # live path must never touch the DB
        return {"reply": replies["text"], "done": replies["done"], "covered_skills": ["Python"],
                "followups_used": 0, "valid_answers": 1}

    monkeypatch.setattr(vs, "run_turn", fake_run_turn)

    async def start(**ctx_kw):
        ws = FakeWS()
        session = vs.VoiceSession(ws, make_ctx(**ctx_kw))
        task = asyncio.create_task(session.run())
        await ws.inbox.put(json.dumps({"event": "start", "start": {
            "streamId": "s1", "callId": "c1", "mediaFormat": {"encoding": "audio/x-l16", "sampleRate": 16000}}}))
        await asyncio.sleep(0.1)
        return ws, session, task

    return start, replies


async def say(session, text, final=True):
    await session._on_stt(SttEvent("transcript", text, is_final=final, speech_final=final))
    await asyncio.sleep(0.2)


@pytest.mark.asyncio
async def test_opening_is_spoken_in_20ms_frames_matching_stream_format(harness):
    start, _ = harness
    ws, session, task = await start()
    frames = ws.events("playAudio")
    assert frames, "opening line was not played"
    assert all(len(base64.b64decode(f["media"]["payload"])) <= 640 for f in frames)
    assert frames[0]["media"]["contentType"] == "audio/x-l16" and frames[0]["media"]["sampleRate"] == 16000
    assert frames[0]["streamId"] == "s1"
    await ws.close()
    await task


@pytest.mark.asyncio
async def test_inbound_audio_is_byteswapped_for_deepgram(harness):
    start, _ = harness
    ws, session, task = await start()
    be = (258).to_bytes(2, "big", signed=True) * 320
    await ws.inbox.put(json.dumps({"event": "media", "media": {"track": "inbound", "payload": base64.b64encode(be).decode()}}))
    await asyncio.sleep(0.1)
    assert session.stt.audio[0] == (258).to_bytes(2, "little", signed=True) * 320
    await ws.close()
    await task


@pytest.mark.asyncio
async def test_candidate_turn_produces_spoken_reply_and_transcript(harness):
    start, replies = harness
    ws, session, task = await start()
    session.audio_end_at = 0  # opening finished playing
    before = len(ws.events("playAudio"))
    await say(session, "I use Python and Redis for caching at work every day")
    assert len(ws.events("playAudio")) > before
    assert session.ctx.transcript[-2]["speaker"] == "Candidate"
    assert session.ctx.transcript[-1]["text"] == replies["text"]
    assert session.ctx.memory["covered_skills"] == ["Python"]
    await ws.close()
    await task


@pytest.mark.asyncio
async def test_barge_in_flushes_audio_but_backchannel_is_ignored(harness):
    start, replies = harness
    ws, session, task = await start()
    assert session._ai_audio_playing()  # opening audio still "playing" on the carrier's clock
    turns_before = len(session.ctx.transcript)
    await say(session, "mm hmm")  # 2 words while the AI talks = backchannel
    assert not ws.events("clearAudio") and len(session.ctx.transcript) == turns_before
    await say(session, "actually let me add something important here", final=False)  # interim, >=3 words
    assert ws.events("clearAudio"), "talking over the AI must flush its queued audio"
    assert not session._ai_audio_playing()
    await ws.close()
    await task


@pytest.mark.asyncio
async def test_silence_reasks_then_ends_call(harness, monkeypatch):
    monkeypatch.setattr(settings, "VOICE_SILENCE_SECONDS", 0.3)
    start, _ = harness
    ws, session, task = await start()
    session.audio_end_at = 0
    for _ in range(100):  # up to 10 s for: wait, re-ask, wait, closing line, close
        if ws.closed:
            break
        await asyncio.sleep(0.1)
    spoken = " ".join(session.tts.said)
    assert "did not catch that" in spoken
    assert "not able to hear you" in spoken
    assert ws.closed, "call must end after repeated silence"
    await task


@pytest.mark.asyncio
async def test_done_reply_closes_the_stream_after_speaking(harness):
    start, replies = harness
    replies.update(text="That's all my questions. Thank you. Goodbye.", done=True)
    ws, session, task = await start()
    session.audio_end_at = 0
    await say(session, "I have used Python and Redis extensively in production systems")
    await asyncio.wait_for(task, timeout=5)
    assert ws.closed and ws.events("playAudio")
