from pydantic import BaseModel, Field
from typing import Optional, List
from datetime import datetime
from app.schemas.candidate import CandidateResponse
from app.schemas.screening import ScreeningResponse


class ApplicationCreate(BaseModel):
    candidate_id: Optional[str] = None
    # If candidate doesn't exist yet, can supply inline
    full_name: Optional[str] = None
    email: Optional[str] = None
    phone: Optional[str] = None
    source: str = Field(default="Direct", example="LinkedIn")


class ApplicationUpdateStatus(BaseModel):
    status: str = Field(..., example="SHORTLISTED")  # SHORTLISTED, REJECTED, INTERVIEW_QUEUED, etc.


class ApplicationResponse(BaseModel):
    id: str
    job_id: str
    candidate_id: str
    source: str
    status: str
    applied_at: datetime
    updated_at: datetime
    candidate: CandidateResponse
    screening: Optional[ScreeningResponse] = None

    class Config:
        from_attributes = True


class BatchInterviewTrigger(BaseModel):
    application_ids: List[str]
    notes: Optional[str] = None
