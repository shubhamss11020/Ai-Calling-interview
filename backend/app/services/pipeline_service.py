"""Phase 1 pipeline: Stage 1 deterministic filter -> Stage 2 AI match -> status transition.
Shared by the inline API path and the Celery screening worker."""
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.application import Application
from app.models.screening import Screening
from app.services import state_machine as sm
from app.services.screening_service import screening_service
from app.services.resume_service import SCANNED_FLAG
from app.services import rag_service
from app.models.knowledge import ResumeChunk

import logging

logger = logging.getLogger(__name__)


async def screen_application(db: AsyncSession, application_id: str) -> Application:
    """Idempotent: re-running replaces the existing screening record."""
    res = await db.execute(
        select(Application)
        .where(Application.id == application_id)
        .options(selectinload(Application.candidate), selectinload(Application.job), selectinload(Application.screening))
    )
    application = res.scalars().first()
    if not application:
        raise LookupError(f"Application {application_id} not found")
    if application.status in (sm.SHORTLISTED, sm.INTERVIEW_QUEUED, sm.IN_PROGRESS, sm.COMPLETED, sm.EVALUATED, sm.HIRED):
        return application  # already past screening

    candidate, job = application.candidate, application.job
    logger.info("Screening application=%s candidate=%s job=%s", application.id, candidate.id, job.id)
    application.status = sm.PROCESSING

    # Scanned / unreadable resume -> hand to a human instead of scoring on empty text
    if candidate.resume_url and len((candidate.raw_resume_text or "").strip()) < 50:
        application.status = sm.MANUAL_REVIEW
        logger.warning("application=%s resume unreadable (%s); routed to MANUAL_REVIEW", application.id, SCANNED_FLAG)
        await _upsert_screening(db, application, det_passed=True, det_notes=SCANNED_FLAG,
                                match_score=0, decision="PENDING", matched=[], missing=[],
                                exp_ok=True, reason="Resume text could not be extracted (scanned image). Manual review required.")
        await db.commit()
        return application

    must_have = (job.screening_criteria or {}).get("must_have_skills") or []
    det_passed, det_notes = screening_service.evaluate_deterministic_stage(
        candidate_skills=candidate.extracted_skills or [],
        candidate_exp=candidate.total_experience_years or 0.0,
        job_required_skills=job.required_skills or [],
        job_min_exp=job.min_experience_years or 0.0,
        must_have_skills=must_have,
    )

    if not det_passed:
        # Fast-fail: no LLM call, $0 cost
        await _upsert_screening(db, application, det_passed=False, det_notes=det_notes, match_score=0,
                                decision="NOT_SUITABLE", matched=[], missing=list(job.required_skills or []),
                                exp_ok=False, reason=det_notes)
        application.status = sm.REJECTED
        logger.info("application=%s REJECTED at Stage 1 (no LLM call): %s", application.id, det_notes)
        await db.commit()
        return application

    resume_context = await _resume_context(db, candidate, job)

    ai = await screening_service.evaluate_ai_stage(
        candidate_skills=candidate.extracted_skills or [],
        candidate_exp=candidate.total_experience_years or 0.0,
        raw_resume_text=candidate.raw_resume_text or "",
        resume_context=resume_context,
        job_title=job.title,
        job_description=job.description,
        job_required_skills=job.required_skills or [],
        job_min_exp=job.min_experience_years or 0.0,
    )
    await _upsert_screening(db, application, det_passed=True, det_notes=det_notes, match_score=ai.match_score,
                            decision=ai.decision, matched=ai.matched_skills, missing=ai.missing_skills,
                            exp_ok=ai.experience_matched, reason=ai.reason)
    application.status = {
        "SUITABLE": sm.SUITABLE, "MARGINAL": sm.MANUAL_REVIEW
    }.get(ai.decision, sm.REJECTED)
    logger.info("application=%s Stage 2 score=%s decision=%s -> %s",
                application.id, ai.match_score, ai.decision, application.status)
    await db.commit()
    return application


async def _upsert_screening(db, application, *, det_passed, det_notes, match_score, decision, matched, missing, exp_ok, reason):
    screening = application.screening
    if screening is None:
        screening = Screening(application_id=application.id)
        db.add(screening)
    screening.deterministic_passed = det_passed
    screening.deterministic_notes = det_notes
    screening.match_score = match_score
    screening.decision = decision
    screening.matched_skills = matched
    screening.missing_skills = missing
    screening.experience_matched = exp_ok
    screening.reason = reason


async def _resume_context(db, candidate, job) -> str:
    """RAG: the resume passages most relevant to this job (indexing on first use); plain truncation if RAG is off."""
    text = candidate.raw_resume_text or ""
    try:
        has_chunks = (await db.execute(
            select(ResumeChunk.id).where(ResumeChunk.candidate_id == candidate.id).limit(1))).first() is not None
        if not has_chunks and text:
            await rag_service.index_candidate(db, candidate.id, text)
        return await rag_service.resume_context_for_job(
            db, candidate.id, text, job.title, job.required_skills or [], job.description or "")
    except Exception as exc:  # RAG must never block screening
        logger.warning("RAG context failed for candidate=%s (%s); using truncated resume", candidate.id, exc)
        await db.rollback()
        return text[:6000]
