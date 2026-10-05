from app.workers.celery_app import celery_app
from app.workers.db import run_with_session
from app.services.llm_client import LLMRateLimited
from app.services.pipeline_service import screen_application


@celery_app.task(
    name="tasks.screen_application",
    bind=True,
    max_retries=3,
)
def screen_application_task(self, application_id: str):
    """Stage 1 + Stage 2 screening, persisted to the DB. 429s back off 10s / 30s / 60s."""
    try:
        app = run_with_session(lambda db: screen_application(db, application_id))
        return {"application_id": application_id, "status": app.status}
    except LLMRateLimited as exc:
        raise self.retry(exc=exc, countdown=(10, 30, 60)[min(self.request.retries, 2)])
