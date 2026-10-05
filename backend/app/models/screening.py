import uuid
from datetime import datetime, timezone
from sqlalchemy import Column, String, Integer, Float, Text, DateTime, ForeignKey, JSON, Boolean
from sqlalchemy.orm import relationship
from app.core.database import Base


class Screening(Base):
    __tablename__ = "screenings"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    application_id = Column(String(36), ForeignKey("applications.id", ondelete="CASCADE"), unique=True, nullable=False, index=True)
    
    # Deterministic Stage 1
    deterministic_passed = Column(Boolean, default=True)
    deterministic_notes = Column(Text, nullable=True)

    # AI Matching Stage 2
    match_score = Column(Integer, default=0, index=True)  # 0 to 100
    decision = Column(String(50), default="PENDING", index=True)  # SUITABLE, MARGINAL, NOT_SUITABLE
    matched_skills = Column(JSON, default=list)  # ["FastAPI", "PostgreSQL", "Node.js"]
    missing_skills = Column(JSON, default=list)  # ["Redis", "Kubernetes"]
    experience_matched = Column(Boolean, default=True)
    reason = Column(Text, nullable=True)  # "Meets backend requirements..."
    
    evaluated_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

    application = relationship("Application", back_populates="screening")
