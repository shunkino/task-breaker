from datetime import datetime, timedelta, timezone
from typing import List, Optional

from fastapi import HTTPException
from sqlalchemy.orm import Session

from .config import settings as app_settings
from .copilot_integration import breakdown_task as _breakdown_task
from .models import TaskORM


class TaskService:
    def __init__(self, db: Session) -> None:
        self.db = db

    def list_tasks(self, status: Optional[str] = None) -> List[TaskORM]:
        query = self.db.query(TaskORM)
        if status:
            query = query.filter(TaskORM.status == status)
        return query.order_by(TaskORM.created_at.desc()).all()

    def get_task(self, task_id: int) -> TaskORM:
        task = self.db.query(TaskORM).filter(TaskORM.id == task_id).first()
        if not task:
            raise HTTPException(status_code=404, detail=f"Task {task_id} not found")
        return task

    def create_task(self, title: str) -> TaskORM:
        task = TaskORM(title=title)
        self.db.add(task)
        self.db.commit()
        self.db.refresh(task)
        return task

    def complete_task(self, task_id: int) -> TaskORM:
        task = self.get_task(task_id)
        task.status = "done"
        task.updated_at = datetime.now(timezone.utc)
        self.db.commit()
        self.db.refresh(task)
        return task

    def add_note(self, task_id: int, note: str) -> TaskORM:
        task = self.get_task(task_id)
        task.notes = note
        task.updated_at = datetime.now(timezone.utc)
        self.db.commit()
        self.db.refresh(task)
        return task

    def delete_task(self, task_id: int) -> TaskORM:
        task = self.get_task(task_id)
        self.db.delete(task)
        self.db.commit()
        return task

    def find_stale_tasks(self, older_than_days: int) -> List[TaskORM]:
        cutoff = datetime.now(timezone.utc) - timedelta(days=older_than_days)
        return (
            self.db.query(TaskORM)
            .filter(
                TaskORM.status == "open",
                TaskORM.breakdown == [],
                TaskORM.created_at < cutoff,
                TaskORM.auto_breakdown_enabled.is_(True),
                TaskORM.atomic.is_(False),
            )
            .all()
        )

    def create_child_tasks(
        self, parent: TaskORM, steps: List[str], max_level: int
    ) -> List[int]:
        """Create child TaskORM objects from breakdown steps and return their IDs."""
        child_level = (parent.level or 0) + 1
        children_ids: List[int] = []
        for step in steps:
            child = TaskORM(
                title=step,
                status="open",
                breakdown=[],
                level=child_level,
                parent_id=parent.id,
                atomic=child_level >= max_level,
            )
            self.db.add(child)
            self.db.flush()  # get the auto-generated id
            children_ids.append(child.id)
        parent.children_ids = children_ids
        parent.updated_at = datetime.now(timezone.utc)
        self.db.commit()
        return children_ids

    def update_breakdown(self, task_id: int, steps: List[str]) -> TaskORM:
        task = self.get_task(task_id)
        task.breakdown = steps
        task.updated_at = datetime.now(timezone.utc)
        self.db.commit()
        self.db.refresh(task)
        return task


class BreakdownService:
    @staticmethod
    async def breakdown_task(
        task: TaskORM,
        model: str = app_settings.model,
        use_workiq: bool = True,
        workiq_command: str = app_settings.workiq_command,
        workiq_args: Optional[List[str]] = None,
        debug: bool = False,
    ) -> List[str]:
        if task.atomic:
            raise ValueError(
                f"Task {task.id} is atomic and cannot be broken down further."
            )
        if (task.level or 0) >= app_settings.max_level:
            raise ValueError(
                f"Task {task.id} is at level {task.level} (max: {app_settings.max_level}). "
                "Cannot break down further."
            )
        _workiq_args = (
            workiq_args if workiq_args is not None else app_settings.workiq_args
        )
        return await _breakdown_task(
            title=task.title,
            model=model,
            use_workiq=use_workiq,
            workiq_command=workiq_command,
            workiq_args=_workiq_args,
            debug=debug,
        )
