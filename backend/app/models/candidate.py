import uuid
from datetime import datetime, timezone
from sqlalchemy import Column, String, Float, DateTime, JSON, Text
from sqlalchemy.orm import relationship
from app.core.database import Base


class Candidate(Base):
    __tablename__ = "candidates"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    full_name = Column(String(255), nullable=False, index=True)
    email = Column(String(255), index=True, nullable=False)
    phone = Column(String(50), index=True, nullable=True)
    location = Column(String(255), nullable=True)
    resume_url = Column(String(500), nullable=True)
    raw_resume_text = Column(Text, nullable=True)
    extracted_skills = Column(JSON, default=list)
    total_experience_years = Column(Float, default=0.0)
    headline = Column(String(255), nullable=True)
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

    applications = relationship("Application", back_populates="candidate", cascade="all, delete-orphan")
