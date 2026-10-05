from pydantic import BaseModel
from typing import List, Optional
from datetime import datetime


class ScreeningResponse(BaseModel):
    id: str
    application_id: str
    deterministic_passed: bool
    deterministic_notes: Optional[str] = None
    match_score: int
    decision: str  # SUITABLE, MARGINAL, NOT_SUITABLE
    matched_skills: List[str]
    missing_skills: List[str]
    experience_matched: bool
    reason: Optional[str] = None
    evaluated_at: datetime

    class Config:
        from_attributes = True


class AIAnalysisResult(BaseModel):
    match_score: int
    decision: str
    matched_skills: List[str]
    missing_skills: List[str]
    experience_matched: bool
    reason: str
