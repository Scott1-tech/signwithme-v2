from celery import Celery

from app.config import settings

celery_app = Celery(
    "contract_signing",
    broker=settings.redis_url,
    backend=settings.redis_url,
    include=["app.workers.tasks"],
)

celery_app.conf.update(
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    task_track_started=True,
    task_time_limit=60 * 15,
    task_soft_time_limit=60 * 12,
    worker_max_tasks_per_child=32,
    # Inline mode: .delay() runs the task now, in this process, and a failure
    # is raised to the caller instead of being stored silently.
    task_always_eager=settings.run_tasks_inline,
    task_eager_propagates=settings.run_tasks_inline,
)
