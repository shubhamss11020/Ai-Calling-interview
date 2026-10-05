from pydantic import BaseModel, EmailStr, Field
from typing import List, Optional
from datetime import datetime


class CandidateCreate(BaseModel):
    full_name: str
    email: EmailStr
    phone: Optional[str] = None
    location: Optional[str] = None
    extracted_skills: List[str] = Field(default_factory=list)
    total_experience_years: float = 0.0
    headline: Optional[str] = None


class CandidateResponse(BaseModel):
    id: str
    full_name: str
    email: EmailStr
    phone: Optional[str] = None
    location: Optional[str] = None
    resume_url: Optional[str] = None
    extracted_skills: List[str]
    total_experience_years: float
    headline: Optional[str] = None
    created_at: datetime

    class Config:
        from_attributes = True
