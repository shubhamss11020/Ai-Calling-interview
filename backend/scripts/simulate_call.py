"""Simulated Vobiz call: streams synthetic candidate speech into the live voice endpoint and measures latency.

    python -m scripts.simulate_call [--base http://127.0.0.1:8000] [--keep]

Needs the backend running and real Deepgram/Groq keys in .env. It creates a throw-away job/candidate/interview in the
database (named "ZZ latency probe") and deletes them afterwards unless --keep is passed. Reports, per turn:
  * end-of-speech -> first AI audio  (the number the caller actually feels)
  * what the AI said (transcribed back with Deepgram)
and checks that talking over the AI flushes its queued audio (barge-in).
"""
import argparse
import asyncio
import base64
import json
import time
from typing import List, Optional, Tuple

import httpx
import websockets
from sqlalchemy import text

from app.core.config import settings
from app.services.telephony_service import sign_interview
from app.voice.audio import frame_bytes, pcm_seconds, swap_endian16

RATE = settings.STREAM_SAMPLE_RATE
FRAME = frame_bytes(RATE)
DG = {"Authorization": f"Token {settings.DEEPGRAM_API_KEY}"}

ANSWERS = [
    "I have about three years of experience building Python backends with FastAPI, mostly payment APIs on PostgreSQL.",
    "We used Redis as a cache in front of the database and invalidated the keys whenever a record changed.",
]
INTERRUPTION = "Sorry to jump in, but let me add that we also used Redis queues for background jobs."


async def synth(client: httpx.AsyncClient, text_: str, voice: str = "aura-orion-en") -> bytes:
    r = await client.post(
        f"https://api.deepgram.com/v1/speak?model={voice}&encoding=linear16&sample_rate={RATE}&container=none",
        headers={**DG, "Content-Type": "application/json"}, json={"text": text_})
    r.raise_for_status()
    return r.content


async def transcribe(client: httpx.AsyncClient, pcm: bytes) -> str:
    r = await client.post(
        f"https://api.deepgram.com/v1/listen?model=nova-3&encoding=linear16&sample_rate={RATE}&channels=1&language=en",
        headers={**DG, "Content-Type": "application/octet-stream"}, content=pcm)
    try:
        return r.json()["results"]["channels"][0]["alternatives"][0]["transcript"]
    except Exception:
        return f"<{r.status_code}>"


class FakeVobiz:
    """Plays the carrier's role: sends caller audio frames in real time, records what the server plays back."""

    def __init__(self, ws):
        self.ws = ws
        self.events: List[Tuple[float, dict]] = []
        self.t0 = time.monotonic()
        self._recv = asyncio.create_task(self._receive())

    async def _receive(self):
        try:
            async for raw in self.ws:
                self.events.append((time.monotonic(), json.loads(raw)))
        except Exception:
            pass

    async def send_audio(self, pcm_le: bytes, realtime: bool = True):
        for i in range(0, len(pcm_le), FRAME):
            frame = pcm_le[i : i + FRAME].ljust(FRAME, b"\x00")
            await self.ws.send(json.dumps({"event": "media", "media": {
                "track": "inbound", "timestamp": int((time.monotonic() - self.t0) * 1000),
                "payload": base64.b64encode(swap_endian16(frame)).decode()}}))  # Vobiz inbound L16 is big-endian
            if realtime:
                await asyncio.sleep(0.02)

    def play_frames_since(self, t: float) -> List[Tuple[float, bytes]]:
        return [(ts, base64.b64decode(m["media"]["payload"])) for ts, m in self.events
                if ts >= t and m.get("event") == "playAudio"]

    def count(self, event: str, since: float) -> int:
        return sum(1 for ts, m in self.events if ts >= since and m.get("event") == event)


async def wait_quiet(v: FakeVobiz, since: float, quiet: float = 1.5, timeout: float = 25.0) -> float:
    """Wait until playAudio frames stop arriving; return the timestamp of the first frame (or -1)."""
    t_end = time.monotonic() + timeout
    while time.monotonic() < t_end:
        await asyncio.sleep(0.2)
        frames = v.play_frames_since(since)
        if frames and time.monotonic() - frames[-1][0] > quiet:
            return frames[0][0]
    frames = v.play_frames_since(since)
    return frames[0][0] if frames else -1.0


async def main(base: str, keep: bool) -> None:
    from app.core.database import engine

    api = f"{base}/api/v1"
    ids = {}
    async with httpx.AsyncClient(timeout=90) as c:
        job = (await c.post(f"{api}/jobs", json={
            "title": "ZZ latency probe", "description": "Python backend with Redis caching.",
            "required_skills": ["Python", "Redis", "PostgreSQL"], "min_experience_years": 1,
            "interview_config": {"max_questions": 4}})).json()
        ids["job"] = job["id"]
        cand = (await c.post(f"{api}/candidates", json={
            "full_name": "ZZ Probe", "email": "zz-probe@invalid.example", "phone": "+919000000000",
            "extracted_skills": ["Python", "Redis", "PostgreSQL"], "total_experience_years": 4})).json()
        ids["cand"] = cand["id"]
        app_r = (await c.post(f"{api}/applications/jobs/{job['id']}", json={"candidate_id": cand["id"]})).json()
        await c.patch(f"{api}/applications/{app_r['id']}/status", json={"status": "SHORTLISTED"})
        itw = (await c.post(f"{api}/interviews/practice", json={"application_id": app_r["id"]})).json()
        started = (await c.post(f"{api}/interviews/{itw['id']}/practice/start")).json()
        print(f"interview {itw['id'][:8]} IN_PROGRESS; opening line: {started['say'][:70]!r}")

        speech = [await synth(c, a) for a in ANSWERS]
        interruption = await synth(c, INTERRUPTION)

        ws_url = base.replace("http", "ws", 1) + f"/ws/vobiz/{itw['id']}/{sign_interview(itw['id'])}"
        print("connecting", ws_url.rsplit("/", 1)[0] + "/<token>")
        async with websockets.connect(ws_url, max_size=None) as ws:
            v = FakeVobiz(ws)
            t_start = time.monotonic()
            await ws.send(json.dumps({"event": "start", "start": {
                "streamId": "sim-stream", "callId": "sim-call",
                "mediaFormat": {"encoding": "audio/x-l16", "sampleRate": RATE}}}))
            # keep silence flowing like a real phone line while we wait for the opening
            first_open = await wait_quiet(v, t_start)
            opening_pcm = b"".join(f for _, f in v.play_frames_since(t_start))
            print(f"\nOPENING  first audio after {first_open - t_start:.2f}s, {pcm_seconds(len(opening_pcm), RATE):.1f}s of speech: "
                  f"{await transcribe(c, opening_pcm)!r}")
            await asyncio.sleep(pcm_seconds(len(opening_pcm), RATE) + 0.3)  # let the opening finish 'playing'

            results = []
            for n, pcm in enumerate(speech, 1):
                t_before = time.monotonic()
                await v.send_audio(pcm)
                t_spoke = time.monotonic()
                # candidate stops talking: phone line keeps sending silence frames
                silence = b"\x00" * FRAME
                for _ in range(300):  # up to 6 s of silence, break when AI audio arrives
                    await ws.send(json.dumps({"event": "media", "media": {"track": "inbound", "payload": base64.b64encode(silence).decode()}}))
                    await asyncio.sleep(0.02)
                    if v.play_frames_since(t_spoke):
                        break
                frames = v.play_frames_since(t_spoke)
                latency = frames[0][0] - t_spoke if frames else float("nan")
                await wait_quiet(v, t_spoke, quiet=1.2)
                reply_pcm = b"".join(f for _, f in v.play_frames_since(t_spoke))
                said = await transcribe(c, reply_pcm)
                results.append(latency)
                print(f"\nTURN {n}  candidate: {ANSWERS[n-1][:60]!r}...\n"
                      f"         end-of-speech -> first AI audio: {latency:.2f}s   "
                      f"({pcm_seconds(len(reply_pcm), RATE):.1f}s reply)\n         AI said: {said!r}")
                await asyncio.sleep(pcm_seconds(len(reply_pcm), RATE) * 0.35)  # AI is mid-sentence now

                if n == 1:  # barge-in: talk over the AI while its audio is still queued
                    t_int = time.monotonic()
                    await v.send_audio(interruption)
                    await asyncio.sleep(0.6)
                    cleared = v.count("clearAudio", t_int)
                    print(f"\nBARGE-IN clearAudio events received: {cleared}  ({'OK' if cleared else 'NOT TRIGGERED'})")
                    for _ in range(300):
                        await ws.send(json.dumps({"event": "media", "media": {"track": "inbound", "payload": base64.b64encode(silence).decode()}}))
                        await asyncio.sleep(0.02)
                        if v.play_frames_since(t_int + 0.6):
                            break
                    await wait_quiet(v, t_int + 0.6, quiet=1.2)

            good = [r for r in results if r == r]
            if good:
                print(f"\nLATENCY end-of-speech -> AI audio: avg {sum(good)/len(good):.2f}s, max {max(good):.2f}s over {len(good)} turns")
            await ws.close()

        await asyncio.sleep(1.5)
        itw_after = (await c.get(f"{api}/interviews/{itw['id']}")).json()
        print(f"\nsaved transcript turns: {len(itw_after['transcript'])}")
        for t in itw_after["transcript"]:
            print(f"   {t['speaker'][:9]:9} {t['text'][:100]}")

        if not keep:
            async with engine.begin() as conn:
                await conn.execute(text("DELETE FROM jobs WHERE id = :j"), {"j": ids["job"]})
                await conn.execute(text("DELETE FROM candidates WHERE id = :c"), {"c": ids["cand"]})
            print("\n(probe data deleted)")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://127.0.0.1:8000")
    ap.add_argument("--keep", action="store_true")
    a = ap.parse_args()
    asyncio.run(main(a.base, a.keep))
