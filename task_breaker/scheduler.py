import logging

from apscheduler.schedulers.asyncio import AsyncIOScheduler

from .config import settings
from .copilot_integration import AI_CONTEXT_MARKER, is_workiq_eula_accepted
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
                use_workiq = is_workiq_eula_accepted(settings.workiq_eula_path)
                steps, context = await BreakdownService.breakdown_task(
                    task, use_workiq=use_workiq, debug=settings.debug
                )
                task_service.update_breakdown(task.id, steps)
                # Preserve AI context from breakdown as a note (skip if one already exists)
                if context:
                    # Re-read task to get fresh notes (breakdown can take minutes)
                    db.refresh(task)
                    if AI_CONTEXT_MARKER not in (task.notes or ""):
                        ai_note = f"{AI_CONTEXT_MARKER}{context}"
                        existing = task.notes or ""
                        new_notes = (
                            f"{existing}\n\n{ai_note}" if existing else ai_note
                        )
                        task_service.add_note(task.id, new_notes)
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
