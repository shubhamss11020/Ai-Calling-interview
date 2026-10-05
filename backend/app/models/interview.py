import uuid
from datetime import datetime, timezone
from sqlalchemy import Column, String, Integer, DateTime, ForeignKey, Text, JSON
from sqlalchemy.orm import relationship
from app.core.database import Base


class Interview(Base):
    __tablename__ = "interviews"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    application_id = Column(String(36), ForeignKey("applications.id", ondelete="CASCADE"), nullable=False, index=True)
    
    # Inbound verification PIN (e.g. 6-digit candidate passcode)
    pin_code = Column(String(10), unique=True, index=True, nullable=False)
    
    # State: QUEUED, CALLING, IN_PROGRESS, CALL_INTERRUPTED, COMPLETED, MISSED, FAILED
    status = Column(String(50), default="QUEUED", index=True)
    call_direction = Column(String(20), default="OUTBOUND")  # OUTBOUND, INBOUND
    attempts_count = Column(Integer, default=0)  # outbound dial attempts
    pin_attempts = Column(Integer, default=0)  # wrong keypad PIN entries on the current call
    duration_seconds = Column(Integer, nullable=True)
    recording_url = Column(String(500), nullable=True)
    agent_state = Column(JSON, default=dict)  # LangGraph interviewer memory: covered_skills, followups_used
    transcript = Column(JSON, default=list)  # live turns, [{"speaker","text","timestamp"}]
    
    telephony_call_id = Column(String(100), nullable=True)  # Twilio Call SID / Exotel Call ID
    scheduled_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    started_at = Column(DateTime(timezone=True), nullable=True)
    completed_at = Column(DateTime(timezone=True), nullable=True)

    application = relationship("Application", back_populates="interviews")
    result = relationship("InterviewResult", uselist=False, back_populates="interview", cascade="all, delete-orphan")


class InterviewResult(Base):
    __tablename__ = "interview_results"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    interview_id = Column(String(36), ForeignKey("interviews.id", ondelete="CASCADE"), unique=True, nullable=False, index=True)

    overall_score = Column(Integer, default=0)  # 0 to 100
    summary = Column(Text, nullable=True)
    transcript_url = Column(String(500), nullable=True)
    recording_url = Column(String(500), nullable=True)
    competency_scores = Column(JSON, default=dict)  # {"technical_depth": 85, "communication": 90}
    full_transcript = Column(JSON, default=list)  # [{"speaker": "AI", "text": "...", "timestamp": 12}]
    
    evaluated_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

    interview = relationship("Interview", back_populates="result")
