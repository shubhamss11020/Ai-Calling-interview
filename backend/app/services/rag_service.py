"""Retrieval-Augmented Generation over Neon pgvector.

Two corpora:
  * ResumeChunk   - the candidate's resume, so screening sees the *relevant* parts instead of the first N chars,
                    and the interviewer can probe what the candidate actually claims.
  * JobKnowledge  - the job's question bank / rubric / JD excerpts the interviewer should draw from.

Every function degrades to a no-embedding fallback (returns [] / truncated text) so the pipeline never blocks on RAG.
"""
import logging
import re
from typing import List, Optional

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.tracing import trace
from app.models.knowledge import JobKnowledge, ResumeChunk
from app.services.embedding_service import embed_query, embed_texts, embeddings_available

logger = logging.getLogger(__name__)


def chunk_text(text: str, size: Optional[int] = None, overlap: Optional[int] = None) -> List[str]:
    """Paragraph-aware sliding window. Keeps chunks <= size chars with `overlap` chars of shared context."""
    size = size or settings.RAG_CHUNK_CHARS
    overlap = overlap if overlap is not None else settings.RAG_CHUNK_OVERLAP
    text = re.sub(r"[ \t]+", " ", text or "").strip()
    if not text:
        return []
    if len(text) <= size:
        return [text]

    paragraphs = [p.strip() for p in re.split(r"\n\s*\n|\n(?=[A-Z][A-Za-z ]{2,30}\n)", text) if p.strip()]
    chunks: List[str] = []
    current = ""
    for para in paragraphs:
        # a single oversized paragraph is hard-split on the window
        while len(para) > size:
            if current:
                chunks.append(current)
                current = ""
            chunks.append(para[:size])
            para = para[size - overlap :]
        if len(current) + len(para) + 1 <= size:
            current = f"{current}\n{para}".strip()
        else:
            chunks.append(current)
            tail = current[-overlap:] if overlap else ""
            current = f"{tail}\n{para}".strip() if len(tail) + len(para) + 1 <= size else para
    if current:
        chunks.append(current)
    return [c for c in chunks if c.strip()]


# ---------------------------------------------------------------- resume corpus

@trace("rag.index_candidate")
async def index_candidate(db: AsyncSession, candidate_id: str, resume_text: str) -> int:
    """(Re)index a candidate's resume. Idempotent. Returns the number of chunks stored (0 = RAG unavailable)."""
    if not embeddings_available():
        return 0
    chunks = chunk_text(resume_text)
    vectors = await embed_texts(chunks) if chunks else None
    if not vectors:
        return 0
    await db.execute(delete(ResumeChunk).where(ResumeChunk.candidate_id == candidate_id))
    for i, (content, vec) in enumerate(zip(chunks, vectors)):
        db.add(ResumeChunk(candidate_id=candidate_id, chunk_index=i, content=content, embedding=vec))
    await db.commit()
    logger.info("Indexed candidate=%s into %s resume chunks", candidate_id, len(chunks))
    return len(chunks)


@trace("rag.retrieve_resume")
async def retrieve_resume_chunks(db: AsyncSession, candidate_id: str, query: str, k: Optional[int] = None) -> List[str]:
    vec = await embed_query(query)
    if vec is None:
        return []
    stmt = (
        select(ResumeChunk.content)
        .where(ResumeChunk.candidate_id == candidate_id)
        .order_by(ResumeChunk.embedding.cosine_distance(vec))
        .limit(k or settings.RAG_TOP_K)
    )
    return [row[0] for row in (await db.execute(stmt)).all()]


async def resume_context_for_job(
    db: AsyncSession, candidate_id: str, raw_text: str, job_title: str, required_skills: List[str], job_description: str
) -> str:
    """Resume text to show the screening LLM: retrieved relevant chunks, else a plain truncation."""
    query = f"{job_title}. Skills: {', '.join(required_skills)}. {job_description[:400]}"
    chunks = await retrieve_resume_chunks(db, candidate_id, query)
    if chunks:
        return "\n---\n".join(chunks)
    logger.info("No resume chunks for candidate=%s; falling back to truncated text", candidate_id)
    return (raw_text or "")[:6000]


# ---------------------------------------------------------------- job knowledge

@trace("rag.add_job_knowledge")
async def add_job_knowledge(
    db: AsyncSession, job_id: str, kind: str, content: str, topic: Optional[str] = None
) -> JobKnowledge:
    vecs = await embed_texts([content])
    if not vecs:
        raise RuntimeError("Embeddings unavailable: set GEMINI_API_KEY (and RAG_ENABLED=true) to add job knowledge")
    item = JobKnowledge(job_id=job_id, kind=kind, topic=topic, content=content, embedding=vecs[0])
    db.add(item)
    await db.commit()
    await db.refresh(item)
    return item


@trace("rag.index_job_description")
async def index_job_description(db: AsyncSession, job_id: str, description: str) -> int:
    """Store the JD as retrievable chunks (replacing earlier JD chunks)."""
    chunks = chunk_text(description)
    vectors = await embed_texts(chunks) if chunks else None
    if not vectors:
        return 0
    await db.execute(delete(JobKnowledge).where(JobKnowledge.job_id == job_id, JobKnowledge.kind == "jd"))
    for content, vec in zip(chunks, vectors):
        db.add(JobKnowledge(job_id=job_id, kind="jd", content=content, embedding=vec))
    await db.commit()
    return len(chunks)


@trace("rag.retrieve_job_knowledge")
async def retrieve_job_knowledge(
    db: AsyncSession, job_id: str, query: str, kinds: Optional[List[str]] = None, k: Optional[int] = None
) -> List[dict]:
    vec = await embed_query(query)
    if vec is None:
        return []
    stmt = select(JobKnowledge.kind, JobKnowledge.topic, JobKnowledge.content).where(JobKnowledge.job_id == job_id)
    if kinds:
        stmt = stmt.where(JobKnowledge.kind.in_(kinds))
    stmt = stmt.order_by(JobKnowledge.embedding.cosine_distance(vec)).limit(k or settings.RAG_TOP_K)
    return [{"kind": r[0], "topic": r[1], "content": r[2]} for r in (await db.execute(stmt)).all()]
