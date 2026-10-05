import asyncio
import os

os.environ.setdefault("GROQ_API_KEY", "")

import pytest
from tests.conftest import requires_db
from httpx import AsyncClient, ASGITransport

from app.services.resume_service import resume_service
from app.services import state_machine as sm
from app.services.interview_service import CallSlotLimiter, interview_service
from app.services.evaluation_service import _heuristic
from app.core.config import settings


def test_experience_from_date_ranges_and_overlap():
    assert resume_service.estimate_experience_years("Worked from 2021 to 2024") == 3.0
    assert resume_service.estimate_experience_years("2019-2022 Acme\n2021-2023 Side gig") == 4.0
    assert resume_service.estimate_experience_years("4+ yrs of Python") == 4.0
    assert resume_service.estimate_experience_years("no dates here") == 0.0


def test_phone_normalization():
    assert resume_service.normalize_phone("98765 43210") == "+919876543210"
    assert resume_service.normalize_phone("+1 (415) 555-2671") == "+14155552671"
    assert resume_service.normalize_phone("12345") is None


def test_scanned_resume_flagged():
    assert resume_service.parse_resume_text("   ")["parse_flag"] == "SCANNED_DOCUMENT_REQUIRES_OCR"


def test_state_machine():
    assert sm.can_transition("SUITABLE", "SHORTLISTED")
    assert not sm.can_transition("SUITABLE", "EVALUATED")
    assert not sm.can_transition("REJECTED", "SUITABLE")


def test_pin_is_6_digits():
    assert len(interview_service.generate_pin_code()) == 6


def test_call_slot_limiter_caps_per_company(monkeypatch):
    monkeypatch.setattr(settings, "MAX_CONCURRENT_CALLS_PER_COMPANY", 2)
    limiter = CallSlotLimiter()
    limiter._redis_checked = True  # force in-memory
    assert limiter.acquire("c1", "a") and limiter.acquire("c1", "b")
    assert not limiter.acquire("c1", "c")
    assert limiter.acquire("c2", "x")
    limiter.release("c1", "a")
    assert limiter.acquire("c1", "c")


def test_heuristic_scorecard():
    t = [{"speaker": "Candidate", "text": "I used FastAPI and Redis in production for years"}]
    out = _heuristic(t, ["FastAPI", "Redis", "Go"])
    assert out["overall_score"] > 0 and "FastAPI" in out["summary"]


@requires_db
@pytest.mark.asyncio
async def test_end_to_end_flow():
    from app.main import app
    from app.core.database import engine, Base

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        job = (await c.post("/api/v1/jobs", json={
            "title": "Backend Engineer", "description": "Python APIs", "required_skills": ["Python", "FastAPI"],
            "min_experience_years": 3})).json()

        # Stage 1 fail-fast: too junior
        r = await c.post(f"/api/v1/applications/jobs/{job['id']}", json={
            "full_name": "Junior", "email": "j@x.com", "phone": "9876500001"})
        assert r.json()["status"] == "REJECTED"
        assert r.json()["screening"]["deterministic_passed"] is False

        # Dedup: same email again returns the same application
        r2 = await c.post(f"/api/v1/applications/jobs/{job['id']}", json={"full_name": "Junior", "email": "J@x.com"})
        assert r2.json()["id"] == r.json()["id"]

        # Qualified candidate
        cand = (await c.post("/api/v1/candidates", json={
            "full_name": "Senior", "email": "s@x.com", "phone": "9876500002",
            "extracted_skills": ["Python", "FastAPI"], "total_experience_years": 5})).json()
        app_r = (await c.post(f"/api/v1/applications/jobs/{job['id']}", json={"candidate_id": cand["id"]})).json()
        assert app_r["status"] == "SUITABLE"

        # Illegal transition rejected, legal accepted
        bad = await c.patch(f"/api/v1/applications/{app_r['id']}/status", json={"status": "EVALUATED"})
        assert bad.status_code == 409
        await c.patch(f"/api/v1/applications/{app_r['id']}/status", json={"status": "SHORTLISTED"})

        # Not-shortlisted applications can't be dispatched
        assert (await c.post("/api/v1/interviews/dispatch-batch", json={"application_ids": [r.json()["id"]]})).status_code == 409

        itw = (await c.post("/api/v1/interviews/dispatch-batch", json={"application_ids": [app_r["id"]]})).json()[0]
        assert itw["status"] == "CALLING" and len(itw["pin_code"]) == 6

        from app.services.telephony_service import sign_interview

        tok = sign_interview(itw["id"])
        base = f"/api/v1/interviews/telephony/vobiz"
        auth = f"interview_id={itw['id']}&token={tok}"

        # forged webhook (no/invalid token) is rejected
        forged = await c.post(f"{base}/speech?interview_id={itw['id']}&token=bad&Speech=hi")
        assert "could not be verified" in forged.text

        # answer -> PIN prompt as VobizXML
        ans = await c.post(f"{base}/answer?{auth}")
        assert ans.headers["content-type"].startswith("application/xml") and "<Gather" in ans.text and 'inputType="dtmf"' in ans.text

        # wrong PIN, then right PIN -> speech Gather
        w = await c.post(f"{base}/gather-pin?{auth}&attempt=1&Digits=000000%23")
        assert "2 attempts remaining" in w.text
        ok = await c.post(f"{base}/gather-pin?{auth}&attempt=2&Digits={itw['pin_code']}")
        assert 'inputType="speech"' in ok.text and "<Redirect" in ok.text

        # candidate answers (form-encoded like Vobiz sends); interviewer replies with another Gather
        turn = await c.post(f"{base}/speech?{auth}", data={"Speech": "I built FastAPI services in Python", "InputType": "speech"})
        assert 'inputType="speech"' in turn.text or "<Hangup/>" in turn.text

        # silence is handled without calling the agent
        quiet = await c.post(f"{base}/speech?{auth}&silent=1")
        assert "did not catch that" in quiet.text

        # Short call => interrupted, not completed (Vobiz posts Duration)
        h = (await c.post(f"{base}/hangup?{auth}", data={"Event": "Hangup", "Duration": "30", "Status": "completed"})).json()
        assert h["status"] == "CALL_INTERRUPTED"

        # Candidate resumes with PIN, call finishes normally => evaluated
        await c.post(f"{base}/gather-pin?{auth}&attempt=1&Digits={itw['pin_code']}")
        h = (await c.post(f"{base}/hangup?{auth}", data={"Duration": "600"})).json()
        assert h["status"] == "COMPLETED"
        done = (await c.get(f"/api/v1/interviews/{itw['id']}")).json()
        assert done["result"]["overall_score"] > 0
        assert (await c.get(f"/api/v1/applications/{app_r['id']}")).json()["status"] == "EVALUATED"


@requires_db
@pytest.mark.asyncio
async def test_practice_interview_runs_without_a_phone():
    from app.main import app

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        job = (await c.post("/api/v1/jobs", json={
            "title": "Practice Role", "description": "Python APIs", "required_skills": ["Python", "Redis"],
            "min_experience_years": 1, "interview_config": {"max_questions": 3}})).json()
        cand = (await c.post("/api/v1/candidates", json={
            "full_name": "Pat", "email": "pat@x.com", "phone": "9876500009",
            "extracted_skills": ["Python", "Redis"], "total_experience_years": 4})).json()
        app_r = (await c.post(f"/api/v1/applications/jobs/{job['id']}", json={"candidate_id": cand["id"]})).json()
        assert app_r["status"] == "SUITABLE"

        # must be shortlisted first
        assert (await c.post("/api/v1/interviews/practice", json={"application_id": app_r["id"]})).status_code == 409
        await c.patch(f"/api/v1/applications/{app_r['id']}/status", json={"status": "SHORTLISTED"})
        itw = (await c.post("/api/v1/interviews/practice", json={"application_id": app_r["id"]})).json()
        assert itw["call_direction"] == "PRACTICE"

        start = (await c.post(f"/api/v1/interviews/{itw['id']}/practice/start")).json()
        assert "Practice Role" in start["say"] and start["done"] is False

        answers = [
            "I build Python services and use Redis for caching hot data with short expiry and write through invalidation.",
            "I tuned slow endpoints by profiling and adding indexes, and I use Redis queues for background work too.",
            "I wrote integration tests against a real database and monitor latency with structured logs and traces.",
            "Happy to discuss more about my experience with distributed systems and incident response work.",
        ]
        last = None
        for a in answers:
            last = (await c.post(f"/api/v1/interviews/{itw['id']}/practice/turn", json={"answer": a})).json()
            if last["done"]:
                break
        assert last["done"] is True
        assert last["interview"]["result"]["overall_score"] > 0
        assert (await c.get(f"/api/v1/applications/{app_r['id']}")).json()["status"] == "EVALUATED"

        listed = (await c.get(f"/api/v1/interviews?application_id={app_r['id']}")).json()
        assert len(listed) == 1 and len(listed[0]["transcript"]) >= 6
