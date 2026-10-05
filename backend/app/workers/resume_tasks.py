from sqlalchemy import select

from app.workers.celery_app import celery_app
from app.workers.db import run_with_session
from app.models.candidate import Candidate
from app.services.resume_service import resume_service
from app.services import rag_service


@celery_app.task(name="tasks.process_resume_document", bind=True, max_retries=3)
def process_resume_document_task(self, candidate_id: str, file_path: str):
    """Extract text/skills/experience from a stored resume and update the candidate profile."""
    parsed = {}

    async def _update(db):
        text = resume_service.extract_text(file_path)
        parsed.update(resume_service.parse_resume_text(text))
        candidate = (await db.execute(select(Candidate).where(Candidate.id == candidate_id))).scalars().first()
        if candidate is None:
            raise LookupError(f"Candidate {candidate_id} not found")
        candidate.raw_resume_text = text
        candidate.extracted_skills = parsed["extracted_skills"]
        candidate.total_experience_years = parsed["total_experience_years"]
        if not candidate.phone and parsed["phone"]:
            candidate.phone = parsed["phone"]
        await db.commit()
        await rag_service.index_candidate(db, candidate_id, text)

    try:
        run_with_session(_update)
        return {
            "status": "MANUAL_REVIEW" if parsed.get("parse_flag") else "SUCCESS",
            "candidate_id": candidate_id,
            "parse_flag": parsed.get("parse_flag"),
            "extracted_skills": parsed.get("extracted_skills"),
            "total_experience_years": parsed.get("total_experience_years"),
        }
    except LookupError:
        raise
    except Exception as exc:
        raise self.retry(exc=exc, countdown=10)
