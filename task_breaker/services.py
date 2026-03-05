from datetime import datetime, timedelta, timezone
from typing import List, Optional

from fastapi import HTTPException
from sqlalchemy import asc, case, desc
from sqlalchemy.orm import Session

from .config import settings as app_settings
from .copilot_integration import breakdown_task as _breakdown_task
from .models import TaskORM

_SORT_FIELDS = {
    "id": TaskORM.id,
    "due_date": TaskORM.due_date,
    "level": TaskORM.level,
    "status": TaskORM.status,
    "created_at": TaskORM.created_at,
    "updated_at": TaskORM.updated_at,
    "title": TaskORM.title,
}


class TaskService:
    def __init__(self, db: Session) -> None:
        self.db = db

    def list_tasks(self, status: Optional[str] = None, sort_by: Optional[str] = None, sort_order: str = "desc") -> List[TaskORM]:
        query = self.db.query(TaskORM)
        if status:
            query = query.filter(TaskORM.status == status)
        field = _SORT_FIELDS.get(sort_by or "created_at", TaskORM.created_at)
        order_fn = asc if sort_order == "asc" else desc
        return query.order_by(order_fn(field)).all()

    def get_task_tree(self) -> List[dict]:
        """Return all tasks organized as a hierarchical tree structure."""
        tasks = self.db.query(TaskORM).order_by(TaskORM.created_at).all()
        children_map: dict[Optional[int], list] = {}
        for t in tasks:
            children_map.setdefault(t.parent_id, []).append(t)
        roots = children_map.get(None, [])
        return [self._build_tree_node(r, children_map) for r in roots]

    def get_subtree(self, task_id: int) -> list:
        """Return a single task and all its descendants as a tree."""
        task = self.get_task(task_id)
        tasks = self.db.query(TaskORM).order_by(TaskORM.created_at).all()
        children_map: dict[Optional[int], list] = {}
        for t in tasks:
            children_map.setdefault(t.parent_id, []).append(t)
        return [self._build_tree_node(task, children_map)]

    @staticmethod
    def _build_tree_node(task: TaskORM, children_map: dict) -> dict:
        """Recursively build a tree node dict for a task."""
        children = children_map.get(task.id, [])
        return {
            "id": task.id,
            "title": task.title,
            "status": task.status,
            "level": task.level,
            "atomic": task.atomic,
            "parent_id": task.parent_id,
            "children_ids": task.children_ids or [],
            "breakdown": task.breakdown or [],
            "notes": task.notes,
            "source": task.source,
            "created_at": task.created_at.isoformat() if task.created_at else None,
            "updated_at": task.updated_at.isoformat() if task.updated_at else None,
            "due_date": task.due_date.date().isoformat() if task.due_date else None,
            "daily_focus": task.daily_focus,
            "children": [
                TaskService._build_tree_node(c, children_map) for c in children
            ],
        }

    def get_task(self, task_id: int) -> TaskORM:
        task = self.db.query(TaskORM).filter(TaskORM.id == task_id).first()
        if not task:
            raise HTTPException(status_code=404, detail=f"Task {task_id} not found")
        return task

    def create_task(self, title: str, due_date: Optional[datetime] = None) -> TaskORM:
        task = TaskORM(title=title, due_date=due_date)
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

    def toggle_focus(self, task_id: int) -> TaskORM:
        task = self.get_task(task_id)
        task.daily_focus = not task.daily_focus
        task.updated_at = datetime.now(timezone.utc)
        self.db.commit()
        self.db.refresh(task)
        return task

    def list_focus_tasks(self) -> List[TaskORM]:
        return (
            self.db.query(TaskORM)
            .filter(TaskORM.daily_focus.is_(True))
            .order_by(
                TaskORM.focus_order.is_(None),  # NULLs last
                TaskORM.focus_order.asc(),
                TaskORM.updated_at.desc(),
            )
            .all()
        )

    def reorder_focus(self, ordered_ids: List[int]) -> None:
        """Persist focus_order for all supplied task IDs in a single UPDATE."""
        if not ordered_ids:
            return
        # Build a CASE expression: CASE WHEN id=1 THEN 0 WHEN id=2 THEN 1 … END
        order_case = case(
            {task_id: pos for pos, task_id in enumerate(ordered_ids)},
            value=TaskORM.id,
        )
        self.db.query(TaskORM).filter(TaskORM.id.in_(ordered_ids)).update(
            {"focus_order": order_case},
            synchronize_session=False,
        )
        self.db.commit()

    def set_due_date(self, task_id: int, due_date: Optional[datetime]) -> TaskORM:
        task = self.get_task(task_id)
        task.due_date = due_date
        task.updated_at = datetime.now(timezone.utc)
        self.db.commit()
        self.db.refresh(task)
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
