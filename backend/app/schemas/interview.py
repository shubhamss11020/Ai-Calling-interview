from pydantic import BaseModel, Field
from typing import Optional, Dict, Any, List
from datetime import datetime


class InterviewResultResponse(BaseModel):
    id: str
    interview_id: str
    overall_score: int
    summary: Optional[str] = None
    transcript_url: Optional[str] = None
    recording_url: Optional[str] = None
    competency_scores: Dict[str, Any]
    full_transcript: List[Dict[str, Any]]
    evaluated_at: datetime

    class Config:
        from_attributes = True


class InterviewResponse(BaseModel):
    id: str
    application_id: str
    pin_code: str
    status: str
    call_direction: str
    attempts_count: int
    scheduled_at: datetime
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    duration_seconds: Optional[int] = None
    recording_url: Optional[str] = None
    transcript: List[Dict[str, Any]] = Field(default_factory=list)
    result: Optional[InterviewResultResponse] = None

    class Config:
        from_attributes = True


class InboundPinVerifyRequest(BaseModel):
    phone_number: str
    pin_code: str


class PracticeCreate(BaseModel):
    application_id: str


class PracticeTurnRequest(BaseModel):
    answer: str = ""


class PracticeTurnResponse(BaseModel):
    say: str
    done: bool
    interview: InterviewResponse
