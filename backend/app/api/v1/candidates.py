from typing import List, Optional
import os
import shutil
import uuid
from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Form, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.core.database import get_db
from app.core.config import settings
from app.models.candidate import Candidate
from app.schemas.candidate import CandidateCreate, CandidateResponse
from app.services.resume_service import resume_service
from app.services.candidate_service import find_existing_candidate
from app.services import rag_service
import logging

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/candidates", tags=["Candidates"])


@router.post("", response_model=CandidateResponse, status_code=status.HTTP_201_CREATED)
async def create_candidate(payload: CandidateCreate, db: AsyncSession = Depends(get_db)):
    candidate = Candidate(
        full_name=payload.full_name,
        email=payload.email,
        phone=resume_service.normalize_phone(payload.phone) or payload.phone,
        location=payload.location,
        extracted_skills=payload.extracted_skills,
        total_experience_years=payload.total_experience_years,
        headline=payload.headline
    )
    db.add(candidate)
    await db.commit()
    await db.refresh(candidate)
    return candidate


@router.post("/upload-resume", response_model=CandidateResponse)
async def upload_resume(
    file: UploadFile = File(...),
    full_name: Optional[str] = Form(None),
    email: Optional[str] = Form(None),
    phone: Optional[str] = Form(None),
    db: AsyncSession = Depends(get_db)
):
    """Upload resume document (PDF/DOCX/TXT), parse candidate profile, and save. `phone` overrides the parsed number."""
    file_ext = os.path.splitext(file.filename)[1].lower()
    if file_ext not in [".pdf", ".docx", ".txt"]:
        raise HTTPException(status_code=400, detail="Only PDF, DOCX, and TXT files supported")

    saved_filename = f"{uuid.uuid4()}{file_ext}"
    saved_path = os.path.join(settings.UPLOAD_DIR, saved_filename)

    with open(saved_path, "wb") as buffer:
        shutil.copyfileobj(file.file, buffer)

    extracted_text = resume_service.extract_text(saved_path)
    parsed = resume_service.parse_resume_text(extracted_text)

    cand_email = (email or parsed.get("email") or f"candidate_{uuid.uuid4().hex[:6]}@placeholder.invalid").strip().lower()
    cand_phone = resume_service.normalize_phone(phone) or parsed.get("phone")

    # Dedup: refresh the existing profile rather than creating a second Candidate
    candidate = await find_existing_candidate(db, cand_email, cand_phone)
    if candidate is None:
        cand_name = full_name or os.path.splitext(file.filename)[0].replace("_", " ").title()
        candidate = Candidate(full_name=cand_name, email=cand_email, phone=cand_phone)
        db.add(candidate)
    candidate.resume_url = f"/uploads/{saved_filename}"
    candidate.raw_resume_text = extracted_text
    candidate.extracted_skills = parsed["extracted_skills"]
    candidate.total_experience_years = parsed["total_experience_years"]
    if phone and cand_phone:
        candidate.phone = cand_phone
    elif candidate.phone is None:
        candidate.phone = cand_phone
    await db.commit()
    await db.refresh(candidate)
    try:
        await rag_service.index_candidate(db, candidate.id, extracted_text)
    except Exception as exc:  # indexing is best-effort; screening re-indexes lazily
        logger.warning("Resume indexing failed for candidate=%s: %s", candidate.id, exc)
        await db.rollback()
    return candidate


@router.get("", response_model=List[CandidateResponse])
async def list_candidates(db: AsyncSession = Depends(get_db)):
    stmt = select(Candidate).order_by(Candidate.created_at.desc())
    res = await db.execute(stmt)
    return res.scalars().all()


@router.get("/{candidate_id}", response_model=CandidateResponse)
async def get_candidate(candidate_id: str, db: AsyncSession = Depends(get_db)):
    stmt = select(Candidate).where(Candidate.id == candidate_id)
    res = await db.execute(stmt)
    cand = res.scalars().first()
    if not cand:
        raise HTTPException(status_code=404, detail="Candidate not found")
    return cand
