from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException, status, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.core.database import get_db
from app.models.job import Job
from app.models.candidate import Candidate
from app.models.application import Application
from app.schemas.application import ApplicationCreate, ApplicationUpdateStatus, ApplicationResponse
import uuid
from app.core.config import settings
from app.services import state_machine as sm
from app.services.candidate_service import find_existing_candidate, find_existing_application
from app.services.pipeline_service import screen_application
from app.services.resume_service import resume_service

import logging

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/applications", tags=["Applications"])


@router.post("/jobs/{job_id}", response_model=ApplicationResponse, status_code=status.HTTP_201_CREATED)
async def submit_application(job_id: str, payload: ApplicationCreate, db: AsyncSession = Depends(get_db)):
    res = await db.execute(select(Job).where(Job.id == job_id))
    job = res.scalars().first()
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    if job.status != "ACTIVE":
        raise HTTPException(status_code=409, detail=f"Job is {job.status} and not accepting applications")

    if payload.candidate_id:
        cand_res = await db.execute(select(Candidate).where(Candidate.id == payload.candidate_id))
        candidate = cand_res.scalars().first()
        if not candidate:
            raise HTTPException(status_code=404, detail="Candidate not found")
    else:
        if not payload.email and not payload.phone:
            raise HTTPException(status_code=422, detail="Provide candidate_id, or an email/phone to identify the applicant")
        candidate = await find_existing_candidate(db, payload.email, payload.phone)
        if not candidate:
            candidate = Candidate(
                full_name=payload.full_name or "Anonymous Applicant",
                email=(payload.email or f"noemail_{uuid.uuid4().hex[:8]}@placeholder.invalid").strip().lower(),
                phone=resume_service.normalize_phone(payload.phone),
            )
            db.add(candidate)
            await db.flush()

    # Same candidate re-applying (e.g. LinkedIn + Naukri): return the existing application, no duplicate screening/calls
    existing = await find_existing_application(db, candidate.id, job.id)
    if existing:
        logger.info("Duplicate application: candidate=%s already applied to job=%s (application=%s)",
                    candidate.id, job.id, existing.id)
        return await _load_application(db, existing.id)

    application = Application(candidate_id=candidate.id, job_id=job.id, source=payload.source, status=sm.APPLIED)
    db.add(application)
    await db.commit()

    if settings.ASYNC_PROCESSING:
        from app.workers.screening_tasks import screen_application_task
        screen_application_task.apply_async(args=[application.id], queue="screening")
    else:
        await screen_application(db, application.id)

    return await _load_application(db, application.id)


async def _load_application(db: AsyncSession, application_id: str) -> Application:
    res = await db.execute(
        select(Application)
        .where(Application.id == application_id)
        .options(selectinload(Application.candidate), selectinload(Application.screening))
        .execution_options(populate_existing=True)
    )
    return res.scalars().first()


@router.get("", response_model=List[ApplicationResponse])
async def list_applications(
    job_id: Optional[str] = Query(None),
    status: Optional[str] = Query(None),
    source: Optional[str] = Query(None),
    db: AsyncSession = Depends(get_db)
):
    stmt = (
        select(Application)
        .options(selectinload(Application.candidate), selectinload(Application.screening))
        .order_by(Application.applied_at.desc())
    )

    if job_id:
        stmt = stmt.where(Application.job_id == job_id)
    if status:
        stmt = stmt.where(Application.status == status)
    if source:
        stmt = stmt.where(Application.source == source)

    res = await db.execute(stmt)
    return res.scalars().all()


@router.get("/{application_id}", response_model=ApplicationResponse)
async def get_application(application_id: str, db: AsyncSession = Depends(get_db)):
    stmt = (
        select(Application)
        .where(Application.id == application_id)
        .options(selectinload(Application.candidate), selectinload(Application.screening))
    )
    res = await db.execute(stmt)
    app = res.scalars().first()
    if not app:
        raise HTTPException(status_code=404, detail="Application not found")
    return app


@router.patch("/{application_id}/status", response_model=ApplicationResponse)
async def update_application_status(
    application_id: str,
    payload: ApplicationUpdateStatus,
    db: AsyncSession = Depends(get_db)
):
    stmt = (
        select(Application)
        .where(Application.id == application_id)
        .options(selectinload(Application.candidate), selectinload(Application.screening))
    )
    res = await db.execute(stmt)
    app = res.scalars().first()
    if not app:
        raise HTTPException(status_code=404, detail="Application not found")

    try:
        sm.assert_transition(app.status, payload.status)
    except sm.InvalidTransition as exc:
        logger.warning("application=%s rejected transition: %s", application_id, exc)
        raise HTTPException(status_code=409, detail=str(exc))
    logger.info("application=%s status %s -> %s (manual)", application_id, app.status, payload.status)
    app.status = payload.status
    await db.commit()
    await db.refresh(app)
    return app
