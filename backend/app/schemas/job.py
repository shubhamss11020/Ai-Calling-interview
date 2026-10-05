from pydantic import BaseModel, Field
from typing import List, Optional, Dict, Any
from datetime import datetime


class JobCreate(BaseModel):
    title: str = Field(..., example="Senior Backend Engineer (Python/FastAPI)")
    description: str = Field(..., example="We are looking for a backend engineer experienced in FastAPI, PostgreSQL, and distributed queues.")
    required_skills: List[str] = Field(default_factory=list, example=["Python", "FastAPI", "PostgreSQL"])
    preferred_skills: List[str] = Field(default_factory=list, example=["Redis", "Docker", "AWS"])
    min_experience_years: float = Field(default=0.0, example=2.0)
    max_experience_years: Optional[float] = Field(default=None, example=5.0)
    location: str = Field(default="Remote")
    employment_type: str = Field(default="Full-time")
    screening_criteria: Optional[Dict[str, Any]] = Field(default_factory=dict)
    interview_config: Optional[Dict[str, Any]] = Field(default_factory=dict)


class JobUpdate(BaseModel):
    title: Optional[str] = None
    description: Optional[str] = None
    required_skills: Optional[List[str]] = None
    preferred_skills: Optional[List[str]] = None
    min_experience_years: Optional[float] = None
    max_experience_years: Optional[float] = None
    location: Optional[str] = None
    employment_type: Optional[str] = None
    status: Optional[str] = None
    screening_criteria: Optional[Dict[str, Any]] = None
    interview_config: Optional[Dict[str, Any]] = None


class JobResponse(BaseModel):
    id: str
    company_id: str
    title: str
    description: str
    required_skills: List[str]
    preferred_skills: List[str]
    min_experience_years: float
    max_experience_years: Optional[float]
    location: str
    employment_type: str
    status: str
    screening_criteria: Dict[str, Any]
    interview_config: Dict[str, Any]
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


class JobStatsResponse(JobResponse):
    total_applications: int = 0
    processing_count: int = 0
    suitable_count: int = 0
    shortlisted_count: int = 0
    rejected_count: int = 0
    interview_pending_count: int = 0
    interview_completed_count: int = 0
