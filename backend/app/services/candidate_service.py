from typing import Optional
from sqlalchemy import select, or_
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.candidate import Candidate
from app.models.application import Application
from app.services.resume_service import resume_service


async def find_existing_candidate(db: AsyncSession, email: Optional[str], phone: Optional[str]) -> Optional[Candidate]:
    """Deduplicate on email (case-insensitive) or normalized phone."""
    conditions = []
    if email:
        conditions.append(Candidate.email == email.strip().lower())
    norm_phone = resume_service.normalize_phone(phone)
    if norm_phone:
        conditions.append(Candidate.phone == norm_phone)
    if not conditions:
        return None
    res = await db.execute(select(Candidate).where(or_(*conditions)).order_by(Candidate.created_at).limit(1))
    return res.scalars().first()


async def find_existing_application(db: AsyncSession, candidate_id: str, job_id: str) -> Optional[Application]:
    res = await db.execute(
        select(Application).where(Application.candidate_id == candidate_id, Application.job_id == job_id)
    )
    return res.scalars().first()
