from typing import Dict, List, Optional
from datetime import datetime, timezone
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.core.config import settings
from app.core.database import get_db
from app.models.application import Application
from app.models.interview import Interview
from app.schemas.interview import (
    InboundPinVerifyRequest, InterviewResponse, PracticeCreate, PracticeTurnRequest, PracticeTurnResponse,
)
from app.schemas.application import BatchInterviewTrigger
from app.services import state_machine as sm
from app.services.interview_service import interview_service, call_slot_limiter
from app.services.interview_orchestrator import (
    begin_interview, dispatch_next_queued, handle_hangup, process_turn, run_evaluation,
)
from app.services.notification_service import send_pin_sms
from app.services.telephony_service import (
    public_url_problem, verify_interview_token, vobiz_service, webhook_url, ws_stream_url, xml_stream, xml_gather_pin, xml_gather_speech, xml_say_and_hangup,
)

import logging

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/interviews", tags=["AI Interviews (Phase 2)"])

ACTIVE_STATUSES = ("QUEUED", "CALLING", "IN_PROGRESS", "CALL_INTERRUPTED")


async def _get_interview(db: AsyncSession, interview_id: str) -> Optional[Interview]:
    res = await db.execute(
        select(Interview)
        .where(Interview.id == interview_id)
        .options(
            selectinload(Interview.result),
            selectinload(Interview.application).selectinload(Application.candidate),
            selectinload(Interview.application).selectinload(Application.job),
        )
        .execution_options(populate_existing=True)  # sessions don't expire on commit: always re-read fresh state
    )
    return res.scalars().first()


@router.post("/dispatch-batch", response_model=List[InterviewResponse])
async def dispatch_batch_interviews(payload: BatchInterviewTrigger, db: AsyncSession = Depends(get_db)):
    """
    Phase 1 -> Phase 2 boundary. For each SHORTLISTED application: create a PIN + interview,
    move the application to INTERVIEW_QUEUED, then dial subject to the per-company concurrency cap.
    Interviews beyond the cap stay QUEUED and are dialled as earlier calls finish.
    """
    if vobiz_service.configured and (problem := public_url_problem()):
        raise HTTPException(status_code=503, detail=f"Cannot start calls: {problem}")

    created: List[Interview] = []
    skipped = []
    company_ids = set()

    for app_id in payload.application_ids:
        res = await db.execute(
            select(Application)
            .where(Application.id == app_id)
            .options(selectinload(Application.candidate), selectinload(Application.job))
        )
        app = res.scalars().first()
        if not app:
            skipped.append({"application_id": app_id, "reason": "not found"})
            continue
        if app.status != sm.SHORTLISTED:
            skipped.append({"application_id": app_id, "reason": f"status is {app.status}, must be SHORTLISTED"})
            continue
        active = await db.execute(
            select(Interview.id).where(Interview.application_id == app.id, Interview.status.in_(ACTIVE_STATUSES))
        )
        if active.first():
            skipped.append({"application_id": app_id, "reason": "interview already active"})
            continue

        interview = Interview(
            application_id=app.id,
            pin_code=await interview_service.generate_unique_pin(db),
            status="QUEUED",
            call_direction="OUTBOUND",
        )
        db.add(interview)
        app.status = sm.INTERVIEW_QUEUED
        await db.flush()
        created.append(interview)
        company_ids.add(app.job.company_id)

    if not created:
        raise HTTPException(status_code=409, detail={"message": "No interviews dispatched", "skipped": skipped})

    await db.commit()
    for company_id in company_ids:
        await dispatch_next_queued(db, company_id)

    out = []
    for itw in created:
        await db.refresh(itw)
        out.append(await _get_interview(db, itw.id))
    return out


async def _params(request: Request) -> Dict[str, str]:
    """Vobiz posts form-encoded data (JSON tolerated). Query-string values win, so identifiers we put in the
    URL (interview_id, token, attempt) cannot be overridden by the body."""
    data: Dict[str, str] = {}
    try:
        if "application/json" in request.headers.get("content-type", ""):
            data.update({k: str(v) for k, v in (await request.json()).items()})
        else:
            data.update({k: str(v) for k, v in (await request.form()).items()})
    except Exception as exc:
        logger.debug("Could not parse webhook body: %s", exc)
    data.update(dict(request.query_params))
    return data


def _xml(body: str) -> Response:
    return Response(content=body, media_type="application/xml")


def _forbidden_xml() -> Response:
    logger.warning("Rejected Vobiz webhook with missing/invalid token")
    return _xml(xml_say_and_hangup("This request could not be verified. Goodbye."))


@router.post("/telephony/vobiz/answer")
async def vobiz_answer_webhook(request: Request):
    """Call answered (outbound dial or candidate calling in): greet and ask for the PIN."""
    p = await _params(request)
    interview_id = p.get("interview_id")
    if interview_id and not verify_interview_token(interview_id, p.get("token")):
        return _forbidden_xml()
    logger.info("Vobiz answer: interview=%s call=%s from=%s", interview_id, p.get("CallUUID"), p.get("From"))
    return _xml(xml_gather_pin(
        "Welcome to your A I technical screening interview. "
        f"Please enter your {settings.PIN_LENGTH} digit PIN followed by the pound key.",
        webhook_url("gather-pin", interview_id, attempt=1),
    ))


@router.post("/telephony/vobiz/gather-pin")
async def vobiz_gather_pin_webhook(request: Request, db: AsyncSession = Depends(get_db)):
    """Keypad PIN check: PIN_MAX_ATTEMPTS tries, then a polite disconnect."""
    p = await _params(request)
    interview_id = p.get("interview_id")
    if interview_id and not verify_interview_token(interview_id, p.get("token")):
        return _forbidden_xml()
    attempt = int(p.get("attempt", "1") or 1)
    pin = p.get("Digits", "").strip("#").strip()

    stmt = select(Interview.id).where(Interview.pin_code == pin, Interview.status.in_(ACTIVE_STATUSES))
    if interview_id:
        stmt = stmt.where(Interview.id == interview_id)
    found = (await db.execute(stmt)).first() if pin else None
    interview = await _get_interview(db, found[0]) if found else None

    if not interview:
        logger.warning("Wrong PIN on call (interview_id=%s attempt=%s)", interview_id, attempt)
        known = await _get_interview(db, interview_id) if interview_id else None
        if known:
            known.pin_attempts = (known.pin_attempts or 0) + 1
            await db.commit()
        remaining = settings.PIN_MAX_ATTEMPTS - attempt
        if remaining <= 0:
            if known and known.application.candidate and known.application.candidate.phone:
                await send_pin_sms(known.application.candidate.phone, known.pin_code)  # no-op unless SMS_ENABLED
            return _xml(xml_say_and_hangup(
                "That PIN is still not valid. Please check your invitation and call back to try again. Goodbye."
            ))
        plural = "s" if remaining != 1 else ""
        return _xml(xml_gather_pin(
            f"That PIN is not valid. You have {remaining} attempt{plural} remaining. Please try again.",
            webhook_url("gather-pin", interview_id, attempt=attempt + 1),
        ))

    logger.info("interview=%s PIN verified call=%s", interview.id, p.get("CallUUID"))
    opening = await begin_interview(db, interview, call_id=p.get("CallUUID"))
    job = interview.application.job

    if settings.VOICE_MODE == "stream" and settings.DEEPGRAM_API_KEY:
        # Live audio stream: Deepgram STT/TTS in app/voice. The opening line is spoken by the session itself.
        return _xml(xml_stream(ws_stream_url(interview.id), "Thank you. Connecting you to your interview now."))

    return _xml(xml_gather_speech(
        opening,
        webhook_url("speech", interview.id),
        webhook_url("speech", interview.id, silent=1),
        hints=job.required_skills or [],
    ))


@router.post("/telephony/vobiz/speech")
async def vobiz_speech_webhook(request: Request, db: AsyncSession = Depends(get_db)):
    """One conversational turn: store the candidate's transcribed answer (`Speech`), run the interviewer agent."""
    p = await _params(request)
    interview_id = p.get("interview_id", "")
    if not verify_interview_token(interview_id, p.get("token")):
        return _forbidden_xml()
    speech = (p.get("Speech") or p.get("SpeechResult") or "").strip()

    interview = await _get_interview(db, interview_id)
    if not interview or interview.status != "IN_PROGRESS":
        logger.warning("Speech webhook for inactive interview=%s (status=%s)", interview_id, interview.status if interview else None)
        return _xml(xml_say_and_hangup("This interview session is not active. Goodbye."))

    job = interview.application.job
    hints = job.required_skills or []
    speech_url = webhook_url("speech", interview.id)
    retry_url = webhook_url("speech", interview.id, silent=1)

    turn = await process_turn(db, interview, speech)
    if turn.done:
        return _xml(xml_say_and_hangup(turn.say))
    return _xml(xml_gather_speech(turn.say, speech_url, retry_url, hints))


@router.post("/telephony/vobiz/hangup")
async def vobiz_hangup_webhook(request: Request, db: AsyncSession = Depends(get_db)):
    """Call ended (Vobiz posts Event, CallUUID, Status, Duration, StartTime, EndTime...). Free the call slot,
    detect interruption, score the transcript, dial the next queued call."""
    p = await _params(request)
    interview_id = p.get("interview_id")
    if not interview_id:
        return {"status": "NOOP"}
    if not verify_interview_token(interview_id, p.get("token")):
        logger.warning("Rejected hangup webhook with invalid token (interview=%s)", interview_id)
        return {"status": "FORBIDDEN"}

    raw_duration = p.get("Duration") or p.get("CallDuration") or p.get("BillDuration")
    try:
        duration = int(float(raw_duration)) if raw_duration not in (None, "") else None
    except ValueError:
        duration = None
    logger.info("Vobiz hangup params: %s", {k: v for k, v in p.items() if k != "token"})
    interview = await handle_hangup(db, interview_id, duration, p.get("RecordUrl"))
    return {"status": interview.status if interview else "NOT_FOUND"}


@router.post("/verify-pin", response_model=InterviewResponse)
async def verify_candidate_inbound_pin(payload: InboundPinVerifyRequest, db: AsyncSession = Depends(get_db)):
    """Inbound candidate callback verification (PIN issued at dispatch)."""
    res = await db.execute(
        select(Interview)
        .where(Interview.pin_code == payload.pin_code, Interview.status.in_(ACTIVE_STATUSES))
        .options(selectinload(Interview.result), selectinload(Interview.application))
    )
    interview = res.scalars().first()
    if not interview:
        raise HTTPException(status_code=404, detail="Invalid interview PIN code")

    interview.status = "IN_PROGRESS"
    interview.started_at = interview.started_at or datetime.now(timezone.utc)
    interview.call_direction = "INBOUND"
    interview.application.status = sm.IN_PROGRESS
    await db.commit()
    await db.refresh(interview)
    return await _get_interview(db, interview.id)


@router.post("/{interview_id}/complete", response_model=InterviewResponse)
async def complete_interview(interview_id: str, db: AsyncSession = Depends(get_db)):
    """Manually conclude an interview and run the evaluator on whatever transcript was captured
    (development / recovery tool; real calls finish via the hangup webhook)."""
    interview = await _get_interview(db, interview_id)
    if not interview:
        raise HTTPException(status_code=404, detail="Interview not found")
    company_id = interview.application.job.company_id
    call_slot_limiter.release(company_id, interview.id)
    updated = await run_evaluation(db, interview_id, force_complete=True)
    await dispatch_next_queued(db, company_id)
    return updated


@router.get("", response_model=List[InterviewResponse])
async def list_interviews(application_id: Optional[str] = None, db: AsyncSession = Depends(get_db)):
    stmt = select(Interview).options(selectinload(Interview.result)).order_by(Interview.scheduled_at.desc())
    if application_id:
        stmt = stmt.where(Interview.application_id == application_id)
    return (await db.execute(stmt.limit(100))).scalars().all()


# ------------------------------------------------------------------ practice interview (no phone)
# Runs the exact same agent/turn logic as the phone webhooks, with the tester typing the candidate's answers.

@router.post("/practice", response_model=InterviewResponse, status_code=201)
async def create_practice_interview(payload: PracticeCreate, db: AsyncSession = Depends(get_db)):
    res = await db.execute(select(Application).where(Application.id == payload.application_id))
    app = res.scalars().first()
    if not app:
        raise HTTPException(status_code=404, detail="Application not found")
    if app.status != sm.SHORTLISTED:
        raise HTTPException(status_code=409, detail=f"Application is {app.status}; shortlist the candidate first")
    interview = Interview(
        application_id=app.id,
        pin_code=await interview_service.generate_unique_pin(db),
        status="QUEUED",
        call_direction="PRACTICE",
    )
    db.add(interview)
    app.status = sm.INTERVIEW_QUEUED
    await db.commit()
    return await _get_interview(db, interview.id)


async def _practice_interview(db: AsyncSession, interview_id: str) -> Interview:
    interview = await _get_interview(db, interview_id)
    if not interview:
        raise HTTPException(status_code=404, detail="Interview not found")
    if interview.call_direction != "PRACTICE":
        raise HTTPException(status_code=409, detail="Only practice interviews can be driven from the browser")
    return interview


@router.post("/{interview_id}/practice/start", response_model=PracticeTurnResponse)
async def practice_start(interview_id: str, db: AsyncSession = Depends(get_db)):
    interview = await _practice_interview(db, interview_id)
    if interview.status not in ("QUEUED", "CALL_INTERRUPTED", "IN_PROGRESS"):
        raise HTTPException(status_code=409, detail=f"Interview is {interview.status}")
    say = await begin_interview(db, interview)
    return PracticeTurnResponse(say=say, done=False, interview=await _get_interview(db, interview_id))


@router.post("/{interview_id}/practice/turn", response_model=PracticeTurnResponse)
async def practice_turn(interview_id: str, payload: PracticeTurnRequest, db: AsyncSession = Depends(get_db)):
    interview = await _practice_interview(db, interview_id)
    if interview.status != "IN_PROGRESS":
        raise HTTPException(status_code=409, detail=f"Interview is {interview.status}; start it first")
    turn = await process_turn(db, interview, payload.answer)
    if turn.done:
        await run_evaluation(db, interview_id, force_complete=True)
    return PracticeTurnResponse(say=turn.say, done=turn.done, interview=await _get_interview(db, interview_id))


@router.get("/{interview_id}", response_model=InterviewResponse)
async def get_interview(interview_id: str, db: AsyncSession = Depends(get_db)):
    itw = await _get_interview(db, interview_id)
    if not itw:
        raise HTTPException(status_code=404, detail="Interview not found")
    return itw
