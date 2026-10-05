import uuid
from datetime import datetime, timezone

from pgvector.sqlalchemy import Vector
from sqlalchemy import Column, DateTime, ForeignKey, Index, Integer, String, Text

from app.core.config import settings
from app.core.database import Base


class ResumeChunk(Base):
    """One embedded slice of a candidate's resume (RAG source for screening and interview follow-ups)."""

    __tablename__ = "resume_chunks"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    candidate_id = Column(String(36), ForeignKey("candidates.id", ondelete="CASCADE"), nullable=False, index=True)
    chunk_index = Column(Integer, nullable=False)
    content = Column(Text, nullable=False)
    embedding = Column(Vector(settings.EMBEDDING_DIM), nullable=False)
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))


class JobKnowledge(Base):
    """Per-job knowledge the interviewer can retrieve: question bank, scoring rubric, JD excerpts."""

    __tablename__ = "job_knowledge"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    job_id = Column(String(36), ForeignKey("jobs.id", ondelete="CASCADE"), nullable=False, index=True)
    kind = Column(String(20), nullable=False, default="question")  # question | rubric | jd | note
    topic = Column(String(100), nullable=True)  # e.g. "PostgreSQL"; lets the agent tie a question to a skill
    content = Column(Text, nullable=False)
    embedding = Column(Vector(settings.EMBEDDING_DIM), nullable=False)
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

    __table_args__ = (Index("idx_job_knowledge_job_kind", "job_id", "kind"),)
