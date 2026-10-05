import uuid
from datetime import datetime, timezone
from sqlalchemy import Column, String, DateTime, ForeignKey, Index
from sqlalchemy.orm import relationship
from app.core.database import Base


class Application(Base):
    __tablename__ = "applications"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    candidate_id = Column(String(36), ForeignKey("candidates.id", ondelete="CASCADE"), nullable=False, index=True)
    job_id = Column(String(36), ForeignKey("jobs.id", ondelete="CASCADE"), nullable=False, index=True)
    source = Column(String(50), default="Direct", index=True)  # LinkedIn, Naukri, Direct, Referral
    
    # Status Pipeline:
    # APPLIED -> PROCESSING -> SUITABLE / REJECTED -> SHORTLISTED -> INTERVIEW_QUEUED -> INTERVIEW_COMPLETED -> EVALUATED -> HIRED
    status = Column(String(50), default="APPLIED", index=True)
    applied_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), index=True)
    updated_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))

    candidate = relationship("Candidate", back_populates="applications")
    job = relationship("Job", back_populates="applications")
    screening = relationship("Screening", uselist=False, back_populates="application", cascade="all, delete-orphan")
    interviews = relationship("Interview", back_populates="application", cascade="all, delete-orphan")

    __table_args__ = (
        Index("idx_app_job_status", "job_id", "status"),
    )
