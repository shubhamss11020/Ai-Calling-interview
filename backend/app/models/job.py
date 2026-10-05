import uuid
from datetime import datetime, timezone
from sqlalchemy import Column, String, Text, Integer, Float, DateTime, ForeignKey, JSON
from sqlalchemy.orm import relationship
from app.core.database import Base


class Job(Base):
    __tablename__ = "jobs"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    company_id = Column(String(36), ForeignKey("companies.id", ondelete="CASCADE"), nullable=False, index=True)
    title = Column(String(255), nullable=False, index=True)
    description = Column(Text, nullable=False)
    required_skills = Column(JSON, default=list)  # ["Python", "FastAPI", "PostgreSQL", "Docker"]
    preferred_skills = Column(JSON, default=list)
    min_experience_years = Column(Float, default=0.0)
    max_experience_years = Column(Float, nullable=True)
    location = Column(String(255), default="Remote")
    employment_type = Column(String(50), default="Full-time")  # Full-time, Contract, Internship
    status = Column(String(50), default="ACTIVE", index=True)  # DRAFT, ACTIVE, PAUSED, CLOSED

    # Screening rules configuration
    screening_criteria = Column(JSON, default=dict)
    # Interview configuration for Phase 2 (voice prompts, questions, interview duration)
    interview_config = Column(JSON, default=dict)

    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    updated_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))

    company = relationship("Company", back_populates="jobs")
    applications = relationship("Application", back_populates="job", cascade="all, delete-orphan")
