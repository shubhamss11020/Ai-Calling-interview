"""Phase 2 call lifecycle: throttled dispatch, hangup handling, interruption recovery, evaluation."""
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.config import settings
from app.models.application import Application
from app.models.interview import Interview, InterviewResult
from app.models.job import Job
from app.services import state_machine as sm
from app.services.evaluation_service import evaluate_transcript
from app.services.interview_service import call_slot_limiter
from app.services.notification_service import send_resume_sms
from app.services.telephony_service import vobiz_service

import logging

logger = logging.getLogger(__name__)


async def _load(db: AsyncSession, interview_id: str) -> Optional[Interview]:
    res = await db.execute(
        select(Interview)
        .where(Interview.id == interview_id)
        .options(
            selectinload(Interview.result),
            selectinload(Interview.application).selectinload(Application.candidate),
            selectinload(Interview.application).selectinload(Application.job),
        )
    )
    return res.scalars().first()


async def dispatch_interview(db: AsyncSession, interview: Interview) -> bool:
    """Place the outbound call if the company has a free slot. Returns True when a call was placed;
    False leaves the interview QUEUED to be picked up by dispatch_next_queued."""
    application = interview.application
    candidate = application.candidate
    company_id = application.job.company_id

    if not candidate or not candidate.phone:
        logger.error("interview=%s FAILED: candidate has no phone number", interview.id)
        interview.status = "FAILED"
        return False
    if interview.attempts_count >= settings.MAX_CALL_ATTEMPTS:
        logger.error("interview=%s FAILED: %s dial attempts exhausted", interview.id, interview.attempts_count)
        interview.status = "FAILED"
        return False
    if not call_slot_limiter.acquire(company_id, interview.id):
        logger.info("interview=%s stays QUEUED: company=%s at call cap (%s)", interview.id, company_id,
                    settings.MAX_CONCURRENT_CALLS_PER_COMPANY)
        return False

    result = await vobiz_service.initiate_outbound_call(
        candidate_phone=candidate.phone,
        interview_id=interview.id,
        pin_code=interview.pin_code,
        callback_url=settings.PUBLIC_BASE_URL,
    )
    interview.attempts_count = (interview.attempts_count or 0) + 1
    if result.get("status") == "ERROR" or not result.get("call_id", result.get("request_uuid")):
        logger.error("interview=%s dial attempt %s failed: %s", interview.id, interview.attempts_count, result.get("error"))
        call_slot_limiter.release(company_id, interview.id)
        interview.status = "QUEUED" if interview.attempts_count < settings.MAX_CALL_ATTEMPTS else "FAILED"
        return False

    interview.telephony_call_id = result.get("call_id") or result.get("request_uuid")
    interview.status = "CALLING"
    logger.info("interview=%s CALLING call_id=%s attempt=%s", interview.id, interview.telephony_call_id, interview.attempts_count)
    return True


async def dispatch_next_queued(db: AsyncSession, company_id: str) -> int:
    """Fill free call slots from the company's QUEUED interviews (oldest first)."""
    res = await db.execute(
        select(Interview.id)
        .join(Application, Interview.application_id == Application.id)
        .join(Job, Application.job_id == Job.id)
        .where(Job.company_id == company_id, Interview.status == "QUEUED", Interview.call_direction == "OUTBOUND")
        .order_by(Interview.scheduled_at)
    )
    placed = 0
    for (interview_id,) in res.all():
        interview = await _load(db, interview_id)
        if await dispatch_interview(db, interview):
            placed += 1
        elif interview.status == "QUEUED":
            break  # no slot left (or transient failure): stop and wait for the next hangup
    await db.commit()
    return placed


async def handle_hangup(db: AsyncSession, interview_id: str, duration: Optional[int], recording_url: Optional[str]) -> Optional[Interview]:
    interview = await _load(db, interview_id)
    if not interview:
        return None
    application = interview.application
    company_id = application.job.company_id
    call_slot_limiter.release(company_id, interview.id)

    if duration is not None:
        interview.duration_seconds = duration
    if recording_url:
        interview.recording_url = recording_url

    if interview.status == "CALLING":
        # Rang but nobody ever reached IN_PROGRESS (no answer / PIN never entered)
        interview.status = "MISSED"
        if (interview.attempts_count or 0) < settings.MAX_CALL_ATTEMPTS:
            interview.status = "QUEUED"
    elif interview.status == "IN_PROGRESS":
        interrupted = duration is not None and duration < settings.MIN_CALL_DURATION_SECONDS
        if interrupted:
            interview.status = "CALL_INTERRUPTED"
            application.status = sm.CALL_INTERRUPTED
            if application.candidate and application.candidate.phone:
                await send_resume_sms(application.candidate.phone, interview.pin_code)
        else:
            interview.status = "COMPLETED"
            interview.completed_at = datetime.now(timezone.utc)
            application.status = sm.COMPLETED

    logger.info("interview=%s hangup duration=%s -> %s", interview.id, duration, interview.status)
    await db.commit()

    if interview.status == "COMPLETED":
        if settings.ASYNC_PROCESSING:
            from app.workers.interview_tasks import evaluate_interview_task
            evaluate_interview_task.apply_async(args=[interview.id], queue="telephony")
        else:
            await run_evaluation(db, interview.id)

    await dispatch_next_queued(db, company_id)
    return interview


async def run_evaluation(db: AsyncSession, interview_id: str, force_complete: bool = False) -> Optional[Interview]:
    """Score the stored transcript and move the application COMPLETED -> EVALUATED."""
    interview = await _load(db, interview_id)
    if not interview:
        return None
    if force_complete and interview.status != "COMPLETED":
        interview.status = "COMPLETED"
        interview.completed_at = datetime.now(timezone.utc)
        interview.application.status = sm.COMPLETED

    job = interview.application.job
    logger.info("interview=%s evaluating %s transcript turns", interview.id, len(interview.transcript or []))
    scorecard = await evaluate_transcript(interview.transcript or [], job.title, job.required_skills or [])

    result = interview.result or InterviewResult(interview_id=interview.id)
    result.overall_score = scorecard["overall_score"]
    result.summary = scorecard["summary"]
    result.competency_scores = scorecard["competency_scores"]
    result.full_transcript = interview.transcript or []
    result.recording_url = interview.recording_url
    result.evaluated_at = datetime.now(timezone.utc)
    if interview.result is None:
        db.add(result)

    interview.application.status = sm.EVALUATED
    await db.commit()
    logger.info("interview=%s EVALUATED score=%s", interview.id, result.overall_score)
    return await _load(db, interview_id)


# ----------------------------------------------------------------------------- live interview turns
# Shared by the Vobiz phone webhooks and the browser "practice interview", so both exercise identical logic.

MAX_SILENT_TURNS = 2


@dataclass
class Turn:
    say: str
    done: bool = False


def _last_interviewer_line(transcript: List[Dict[str, Any]], default: str = "Please go ahead.") -> str:
    return next((t["text"] for t in reversed(transcript) if t.get("speaker") == "AI Interviewer"), default)


async def begin_interview(db: AsyncSession, interview: Interview, call_id: Optional[str] = None) -> str:
    """PIN accepted (or practice session started): mark live and return what the interviewer says first."""
    resuming = interview.status == "CALL_INTERRUPTED"
    interview.status = "IN_PROGRESS"
    interview.pin_attempts = 0
    interview.started_at = interview.started_at or datetime.now(timezone.utc)
    if call_id:
        interview.telephony_call_id = call_id
    interview.application.status = sm.IN_PROGRESS

    job = interview.application.job
    transcript = list(interview.transcript or [])
    if resuming and transcript:
        opening = "Welcome back. Let's pick up where we left off. " + _last_interviewer_line(transcript, "Please continue.")
    else:
        opening = (
            f"Thank you. Your interview for the {job.title} role is now starting. "
            "Please tell me about your background and recent projects."
        )
        transcript.append({"speaker": "AI Interviewer", "text": opening, "timestamp": 0})
    interview.transcript = transcript
    await db.commit()
    logger.info("interview=%s began (resuming=%s)", interview.id, resuming)
    return opening


async def process_turn(db: AsyncSession, interview: Interview, speech: str) -> Turn:
    """One candidate utterance in, one interviewer reply out. Handles silence, runs the LangGraph agent,
    persists transcript + agent memory."""
    from app.agents.interview_agent import run_turn  # local import: agent pulls in langgraph

    job = interview.application.job
    started = interview.started_at
    if started is not None and started.tzinfo is None:
        started = started.replace(tzinfo=timezone.utc)
    elapsed = int((datetime.now(timezone.utc) - started).total_seconds()) if started else 0
    transcript = list(interview.transcript or [])
    memory = dict(interview.agent_state or {})
    speech = (speech or "").strip()

    if not speech:
        memory["silences"] = memory.get("silences", 0) + 1
        interview.agent_state = memory
        if memory["silences"] >= MAX_SILENT_TURNS:
            closing = "I am not able to hear you, so I will end the call here. Please call back to try again. Goodbye."
            transcript.append({"speaker": "AI Interviewer", "text": closing, "timestamp": elapsed})
            interview.transcript = transcript
            await db.commit()
            return Turn(closing, done=True)
        await db.commit()
        return Turn("Sorry, I did not catch that. " + _last_interviewer_line(transcript))

    memory["silences"] = 0
    transcript.append({"speaker": "Candidate", "text": speech, "timestamp": elapsed})
    history = [
        {"role": "assistant" if t["speaker"] == "AI Interviewer" else "user", "content": t["text"]}
        for t in transcript
    ]
    state = await run_turn(
        {
            "job_id": job.id,
            "job_title": job.title,
            "candidate_id": interview.application.candidate_id,
            "required_skills": job.required_skills or [],
            "history": history,
            "max_turns": int((job.interview_config or {}).get("max_questions", 6)),
            "ai_turns": sum(1 for t in transcript if t.get("speaker") == "AI Interviewer"),
            "covered_skills": memory.get("covered_skills", []),
            "followups_used": memory.get("followups_used", 0),
            "valid_answers": memory.get("valid_answers", 0),
        },
        db=db,
    )
    reply = state["reply"]
    transcript.append({"speaker": "AI Interviewer", "text": reply, "timestamp": elapsed})
    interview.transcript = transcript
    interview.agent_state = {
        **memory,
        "covered_skills": state.get("covered_skills", []),
        "followups_used": state.get("followups_used", 0),
        "valid_answers": state.get("valid_answers", 0),
    }
    await db.commit()
    return Turn(reply, done=bool(state.get("done")))
