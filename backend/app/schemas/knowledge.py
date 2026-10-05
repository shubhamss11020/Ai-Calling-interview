from datetime import datetime
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field


class KnowledgeCreate(BaseModel):
    kind: Literal["question", "rubric", "jd", "note"] = "question"
    topic: Optional[str] = Field(default=None, max_length=100, description="Skill this relates to, e.g. PostgreSQL")
    content: str = Field(min_length=5, max_length=4000)


class KnowledgeResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    job_id: str
    kind: str
    topic: Optional[str] = None
    content: str
    created_at: datetime


class KnowledgeSearchHit(BaseModel):
    kind: str
    topic: Optional[str] = None
    content: str
