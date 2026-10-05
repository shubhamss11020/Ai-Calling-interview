import logging
from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func

from app.core.database import get_db
from app.models.job import Job
from app.models.company import Company
from app.models.application import Application
from app.services import rag_service
from app.models.knowledge import JobKnowledge
from app.schemas.knowledge import KnowledgeCreate, KnowledgeResponse, KnowledgeSearchHit
from app.schemas.job import JobCreate, JobUpdate, JobResponse, JobStatsResponse

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/jobs", tags=["Jobs"])


async def ensure_default_company(db: AsyncSession) -> Company:
    stmt = select(Company).limit(1)
    res = await db.execute(stmt)
    comp = res.scalars().first()
    if not comp:
        comp = Company(name="Acme Corp", domain="acme.ai")
        db.add(comp)
        await db.commit()
        await db.refresh(comp)
    return comp


@router.post("", response_model=JobResponse, status_code=status.HTTP_201_CREATED)
async def create_job(payload: JobCreate, db: AsyncSession = Depends(get_db)):
    company = await ensure_default_company(db)

    job = Job(
        company_id=company.id,
        title=payload.title,
        description=payload.description,
        required_skills=payload.required_skills,
        preferred_skills=payload.preferred_skills,
        min_experience_years=payload.min_experience_years,
        max_experience_years=payload.max_experience_years,
        location=payload.location,
        employment_type=payload.employment_type,
        screening_criteria=payload.screening_criteria or {},
        interview_config=payload.interview_config or {}
    )
    db.add(job)
    await db.commit()
    await db.refresh(job)
    try:
        await rag_service.index_job_description(db, job.id, job.description)
    except Exception as exc:
        logger.warning("JD indexing failed for job=%s: %s", job.id, exc)
        await db.rollback()
    return job


@router.get("", response_model=List[JobStatsResponse])
async def list_jobs(db: AsyncSession = Depends(get_db)):
    # Retrieve all jobs
    stmt = select(Job).order_by(Job.created_at.desc())
    res = await db.execute(stmt)
    jobs = res.scalars().all()

    response_list = []
    for job in jobs:
        # Aggregate application counts by status
        app_stmt = select(Application.status, func.count(Application.id)).where(
            Application.job_id == job.id
        ).group_by(Application.status)
        app_res = await db.execute(app_stmt)
        status_map = dict(app_res.all())

        total = sum(status_map.values())
        processing = status_map.get("PROCESSING", 0) + status_map.get("APPLIED", 0)
        suitable = status_map.get("SUITABLE", 0)
        shortlisted = status_map.get("SHORTLISTED", 0)
        rejected = status_map.get("REJECTED", 0)
        interview_pending = status_map.get("SHORTLISTED", 0) + status_map.get("INTERVIEW_QUEUED", 0)
        interview_completed = status_map.get("INTERVIEW_COMPLETED", 0) + status_map.get("EVALUATED", 0)

        job_dict = JobStatsResponse(
            id=job.id,
            company_id=job.company_id,
            title=job.title,
            description=job.description,
            required_skills=job.required_skills or [],
            preferred_skills=job.preferred_skills or [],
            min_experience_years=job.min_experience_years,
            max_experience_years=job.max_experience_years,
            location=job.location,
            employment_type=job.employment_type,
            status=job.status,
            screening_criteria=job.screening_criteria or {},
            interview_config=job.interview_config or {},
            created_at=job.created_at,
            updated_at=job.updated_at,
            total_applications=total,
            processing_count=processing,
            suitable_count=suitable,
            shortlisted_count=shortlisted,
            rejected_count=rejected,
            interview_pending_count=interview_pending,
            interview_completed_count=interview_completed
        )
        response_list.append(job_dict)

    return response_list


@router.get("/{job_id}", response_model=JobStatsResponse)
async def get_job(job_id: str, db: AsyncSession = Depends(get_db)):
    stmt = select(Job).where(Job.id == job_id)
    res = await db.execute(stmt)
    job = res.scalars().first()
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    app_stmt = select(Application.status, func.count(Application.id)).where(
        Application.job_id == job.id
    ).group_by(Application.status)
    app_res = await db.execute(app_stmt)
    status_map = dict(app_res.all())

    return JobStatsResponse(
        id=job.id,
        company_id=job.company_id,
        title=job.title,
        description=job.description,
        required_skills=job.required_skills or [],
        preferred_skills=job.preferred_skills or [],
        min_experience_years=job.min_experience_years,
        max_experience_years=job.max_experience_years,
        location=job.location,
        employment_type=job.employment_type,
        status=job.status,
        screening_criteria=job.screening_criteria or {},
        interview_config=job.interview_config or {},
        created_at=job.created_at,
        updated_at=job.updated_at,
        total_applications=sum(status_map.values()),
        processing_count=status_map.get("PROCESSING", 0) + status_map.get("APPLIED", 0),
        suitable_count=status_map.get("SUITABLE", 0),
        shortlisted_count=status_map.get("SHORTLISTED", 0),
        rejected_count=status_map.get("REJECTED", 0),
        interview_pending_count=status_map.get("SHORTLISTED", 0) + status_map.get("INTERVIEW_QUEUED", 0),
        interview_completed_count=status_map.get("INTERVIEW_COMPLETED", 0)
    )


@router.patch("/{job_id}", response_model=JobResponse)
async def update_job(job_id: str, payload: JobUpdate, db: AsyncSession = Depends(get_db)):
    stmt = select(Job).where(Job.id == job_id)
    res = await db.execute(stmt)
    job = res.scalars().first()
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    for field, val in payload.model_dump(exclude_unset=True).items():
        setattr(job, field, val)

    await db.commit()
    await db.refresh(job)
    return job


# ---------------------------------------------------------------- interview knowledge (RAG)

@router.post("/{job_id}/knowledge", response_model=KnowledgeResponse, status_code=status.HTTP_201_CREATED)
async def add_job_knowledge(job_id: str, payload: KnowledgeCreate, db: AsyncSession = Depends(get_db)):
    """Add a question, scoring rubric or note the AI interviewer can retrieve while interviewing for this job."""
    job = (await db.execute(select(Job).where(Job.id == job_id))).scalars().first()
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    try:
        return await rag_service.add_job_knowledge(db, job_id, payload.kind, payload.content, payload.topic)
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc))


@router.get("/{job_id}/knowledge", response_model=List[KnowledgeResponse])
async def list_job_knowledge(job_id: str, db: AsyncSession = Depends(get_db)):
    res = await db.execute(select(JobKnowledge).where(JobKnowledge.job_id == job_id).order_by(JobKnowledge.created_at))
    return res.scalars().all()


@router.get("/{job_id}/knowledge/search", response_model=List[KnowledgeSearchHit])
async def search_job_knowledge(job_id: str, q: str, db: AsyncSession = Depends(get_db)):
    """Debug aid: see what the interviewer would retrieve for a given candidate answer."""
    return await rag_service.retrieve_job_knowledge(db, job_id, q)


@router.delete("/{job_id}/knowledge/{item_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_job_knowledge(job_id: str, item_id: str, db: AsyncSession = Depends(get_db)):
    item = (await db.execute(
        select(JobKnowledge).where(JobKnowledge.id == item_id, JobKnowledge.job_id == job_id))).scalars().first()
    if not item:
        raise HTTPException(status_code=404, detail="Knowledge item not found")
    await db.delete(item)
    await db.commit()
