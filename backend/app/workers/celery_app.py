import logging
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

from celery import Celery, Task

from app.config import settings

logger = logging.getLogger(__name__)

# Inline mode (RUN_TASKS_INLINE=true, e.g. a single free Render instance with
# no Redis and no worker): `.delay()` hands the task to ONE background thread
# in this process and returns at once. The request answers immediately (no
# proxy timeout on a long template analysis), and only one task runs at a time,
# which keeps both memory and database connections low. The client already
# polls the record's status, so nothing waits on the task's result.
_inline_executor = (
    ThreadPoolExecutor(max_workers=1, thread_name_prefix="inline-task")
    if settings.run_tasks_inline
    else None
)


class _QueuedResult:
    """What `.delay()` returns in inline mode: only `.id` is ever read."""

    def __init__(self, task_id: str) -> None:
        self.id = task_id


class InlineAwareTask(Task):
    def apply_async(self, args=None, kwargs=None, task_id=None, **options):
        if _inline_executor is None:
            return super().apply_async(args, kwargs, task_id=task_id, **options)
        task_id = task_id or str(uuid4())

        def run() -> None:
            try:
                # `apply` runs the task here; a failure is caught and logged,
                # and the task itself already records it on its row.
                result = self.apply(args=args, kwargs=kwargs, task_id=task_id)
                if result.failed():
                    logger.error("Inline task %s (%s) failed: %s", self.name, task_id, result.result)
            except Exception:  # noqa: BLE001 - never kill the worker thread
                logger.exception("Inline task %s (%s) crashed", self.name, task_id)

        _inline_executor.submit(run)
        return _QueuedResult(task_id)


celery_app = Celery(
    "contract_signing",
    broker=settings.redis_url,
    backend=settings.redis_url,
    include=["app.workers.tasks"],
    task_cls=InlineAwareTask,
)

celery_app.conf.update(
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    task_track_started=True,
    task_time_limit=60 * 15,
    task_soft_time_limit=60 * 12,
    worker_max_tasks_per_child=32,
)
