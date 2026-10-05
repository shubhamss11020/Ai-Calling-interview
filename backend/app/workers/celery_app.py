from celery import Celery
import logging
import ssl
from celery.signals import task_prerun, task_failure, task_retry

from app.core.config import settings
from app.core.tracing import configure_tracing
from app.core.logging import request_id_ctx, setup_logging

setup_logging(settings.ENVIRONMENT, settings.LOG_LEVEL)
configure_tracing()
logger = logging.getLogger("app.worker")

celery_app = Celery(
    "recruitment_worker",
    broker=settings.broker_url,
    backend=settings.result_backend_url,
    include=[
        "app.workers.resume_tasks",
        "app.workers.screening_tasks",
        "app.workers.interview_tasks",
    ]
)

@task_prerun.connect
def _bind_task_id(task_id=None, task=None, **_):
    request_id_ctx.set(f"task:{task_id[:8]}")
    logger.info("Task start %s id=%s", task.name, task_id)


@task_failure.connect
def _log_task_failure(task_id=None, exception=None, sender=None, **_):
    logger.error("Task FAILED %s id=%s: %s: %s", getattr(sender, "name", "?"), task_id,
                 exception.__class__.__name__, exception)


@task_retry.connect
def _log_task_retry(request=None, reason=None, **_):
    logger.warning("Task RETRY %s id=%s reason=%s", request.task, request.id, reason)


_TLS = {"ssl_cert_reqs": ssl.CERT_REQUIRED}

celery_app.conf.update(
    broker_use_ssl=_TLS if settings.broker_url.startswith("rediss://") else None,
    redis_backend_use_ssl=_TLS if settings.result_backend_url.startswith("rediss://") else None,
    broker_connection_retry_on_startup=True,
    broker_transport_options={"visibility_timeout": 3600, "socket_keepalive": True},
    result_expires=3600,  # keep Upstash storage small
    task_ignore_result=False,
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="UTC",
    enable_utc=True,
    task_track_started=True,
    task_time_limit=300,  # 5 minutes max per task
    worker_concurrency=4,
    task_default_queue="screening",
    task_acks_late=True,
    task_routes={
        "tasks.process_resume_document": {"queue": "resume-parsing"},
        "tasks.screen_application": {"queue": "screening"},
        "tasks.evaluate_interview": {"queue": "telephony"},
        "tasks.dispatch_queued_calls": {"queue": "telephony"},
    },
)
