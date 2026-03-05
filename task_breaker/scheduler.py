import logging

from apscheduler.schedulers.asyncio import AsyncIOScheduler

from .config import settings
from .database import SessionLocal
from .services import BreakdownService, TaskService

logger = logging.getLogger(__name__)

scheduler = AsyncIOScheduler()


async def check_stale_tasks() -> None:
    """Find stale open tasks (no breakdown, older than threshold) and auto-breakdown."""
    if not settings.auto_breakdown_enabled:
        return
    db = SessionLocal()
    try:
        task_service = TaskService(db)
        stale = task_service.find_stale_tasks(settings.auto_breakdown_threshold_days)
        for task in stale:
            logger.info("Auto-breakdown: processing task %d '%s'", task.id, task.title)
            try:
                steps = await BreakdownService.breakdown_task(
                    task, debug=settings.debug
                )
                task_service.update_breakdown(task.id, steps)
                task_service.create_child_tasks(task, steps, settings.max_level)
                logger.info(
                    "Auto-breakdown: task %d done, %d steps", task.id, len(steps)
                )
            except (ValueError, Exception) as exc:  # noqa: BLE001
                if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                    raise
                logger.error("Auto-breakdown: task %d failed: %s", task.id, exc)
    finally:
        db.close()


def start_scheduler() -> None:
    scheduler.add_job(
        check_stale_tasks,
        "interval",
        hours=settings.check_interval_hours,
        id="check_stale_tasks",
        replace_existing=True,
    )
    scheduler.start()
    logger.info(
        "Scheduler started: check_stale_tasks every %d hour(s)",
        settings.check_interval_hours,
    )


def stop_scheduler() -> None:
    if scheduler.running:
        scheduler.shutdown(wait=False)
        logger.info("Scheduler stopped.")
