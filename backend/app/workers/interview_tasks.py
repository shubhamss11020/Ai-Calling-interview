from app.workers.celery_app import celery_app
from app.workers.db import run_with_session
from app.services.interview_orchestrator import run_evaluation, dispatch_next_queued


@celery_app.task(name="tasks.evaluate_interview", bind=True, max_retries=3)
def evaluate_interview_task(self, interview_id: str):
    try:
        itw = run_with_session(lambda db: run_evaluation(db, interview_id))
        return {"interview_id": interview_id, "status": itw.status if itw else "NOT_FOUND"}
    except Exception as exc:
        raise self.retry(exc=exc, countdown=15)


@celery_app.task(name="tasks.dispatch_queued_calls")
def dispatch_queued_calls_task(company_id: str):
    return run_with_session(lambda db: dispatch_next_queued(db, company_id))
