import logging
from contextlib import asynccontextmanager
from datetime import date as _date, datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import BackgroundTasks, Depends, FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from .config import settings
from .copilot_integration import (
    AI_CONTEXT_MARKER,
    accept_workiq_eula_via_mcp,
    is_workiq_eula_accepted,
    save_workiq_eula_acceptance,
    WORKIQ_EULA_URL,
)
from .database import get_db, init_db
from .scheduler import start_scheduler, stop_scheduler
from .services import BreakdownService, TaskService

_BASE_DIR = Path(__file__).parent


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    start_scheduler()
    yield
    stop_scheduler()


app = FastAPI(title="Task Breaker", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=str(_BASE_DIR / "static")), name="static")
templates = Jinja2Templates(directory=str(_BASE_DIR / "templates"))


# ---------------------------------------------------------------------------
# API Routes
# ---------------------------------------------------------------------------


@app.get("/api/tasks", response_model=List[Dict[str, Any]])
def api_list_tasks(
    status: Optional[str] = None,
    sort: Optional[str] = None,
    order: str = "desc",
    db: Session = Depends(get_db),
):
    svc = TaskService(db)
    tasks = svc.list_tasks(status=status, sort_by=sort, sort_order=order)
    return [_task_to_dict(t) for t in tasks]


@app.get("/api/tasks/tree")
def api_task_tree(db: Session = Depends(get_db)):
    """Return all tasks as a hierarchical tree structure."""
    svc = TaskService(db)
    return svc.get_task_tree()


@app.get("/api/tasks/{task_id}/tree")
def api_task_subtree(task_id: int, db: Session = Depends(get_db)):
    """Return a task and all its descendants as a hierarchical tree."""
    svc = TaskService(db)
    return svc.get_subtree(task_id)


@app.post("/api/tasks", response_model=Dict[str, Any], status_code=201)
def api_create_task(
    body: Dict[str, Any],
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
):
    title = body.get("title", "").strip()
    if not title:
        raise HTTPException(status_code=422, detail="title is required")
    due_date = None
    if body.get("due_date"):
        try:
            due_date = datetime.combine(
                _date.fromisoformat(body["due_date"]),
                datetime.min.time(),
                tzinfo=timezone.utc,
            )
        except ValueError:
            raise HTTPException(status_code=422, detail="due_date must be YYYY-MM-DD")
    svc = TaskService(db)
    task = svc.create_task(title, due_date=due_date)

    # Queue background WorkIQ context gathering for the new task
    if is_workiq_eula_accepted(settings.workiq_eula_path):
        background_tasks.add_task(_gather_task_context, task.id, title)

    return _task_to_dict(task)


@app.get("/api/tasks/focus", response_model=List[Dict[str, Any]])
def api_list_focus_tasks(db: Session = Depends(get_db)):
    svc = TaskService(db)
    return [_task_to_dict(t) for t in svc.list_focus_tasks()]


@app.post("/api/tasks/focus/reorder", status_code=204)
def api_reorder_focus(body: Dict[str, Any], db: Session = Depends(get_db)):
    """Persist a new focus order. Body: {"ordered_ids": [1, 3, 2, ...]}"""
    ordered_ids = body.get("ordered_ids", [])
    if not isinstance(ordered_ids, list):
        raise HTTPException(status_code=422, detail="ordered_ids must be a list")
    if not all(isinstance(i, int) for i in ordered_ids):
        raise HTTPException(
            status_code=422, detail="ordered_ids must contain only integers"
        )
    svc = TaskService(db)
    svc.reorder_focus(ordered_ids)


@app.get("/api/tasks/{task_id}", response_model=Dict[str, Any])
def api_get_task(task_id: int, db: Session = Depends(get_db)):
    svc = TaskService(db)
    return _task_to_dict(svc.get_task(task_id))


@app.post("/api/tasks/{task_id}/complete", response_model=Dict[str, Any])
def api_complete_task(
    task_id: int,
    body: Optional[Dict[str, Any]] = None,
    db: Session = Depends(get_db),
):
    opts = body or {}
    include_children = bool(opts.get("include_children", False))
    svc = TaskService(db)
    return _task_to_dict(svc.complete_task(task_id, include_children=include_children))


@app.post("/api/tasks/{task_id}/reopen", response_model=Dict[str, Any])
def api_reopen_task(task_id: int, db: Session = Depends(get_db)):
    svc = TaskService(db)
    return _task_to_dict(svc.reopen_task(task_id))


@app.post("/api/tasks/{task_id}/archive", response_model=Dict[str, Any])
def api_archive_task(
    task_id: int,
    body: Optional[Dict[str, Any]] = None,
    db: Session = Depends(get_db),
):
    opts = body or {}
    include_children = bool(opts.get("include_children", False))
    svc = TaskService(db)
    return _task_to_dict(svc.archive_task(task_id, include_children=include_children))


@app.post("/api/tasks/{task_id}/note", response_model=Dict[str, Any])
def api_add_note(task_id: int, body: Dict[str, Any], db: Session = Depends(get_db)):
    note = body.get("note", "")
    svc = TaskService(db)
    return _task_to_dict(svc.add_note(task_id, note))


@app.delete("/api/tasks/{task_id}", response_model=Dict[str, Any])
def api_delete_task(task_id: int, db: Session = Depends(get_db)):
    svc = TaskService(db)
    task = svc.delete_task(task_id)
    return _task_to_dict(task)


@app.post("/api/tasks/{task_id}/focus", response_model=Dict[str, Any])
def api_toggle_focus(task_id: int, db: Session = Depends(get_db)):
    svc = TaskService(db)
    return _task_to_dict(svc.toggle_focus(task_id))


@app.put("/api/tasks/{task_id}/due", response_model=Dict[str, Any])
def api_set_due_date(task_id: int, body: Dict[str, Any], db: Session = Depends(get_db)):
    due_str = body.get("due_date")
    due_date = None
    if due_str:
        try:
            due_date = datetime.combine(
                _date.fromisoformat(due_str), datetime.min.time(), tzinfo=timezone.utc
            )
        except ValueError:
            raise HTTPException(status_code=422, detail="due_date must be YYYY-MM-DD")
    svc = TaskService(db)
    return _task_to_dict(svc.set_due_date(task_id, due_date))


@app.post("/api/tasks/{task_id}/breakdown", response_model=Dict[str, Any])
async def api_breakdown_task(
    task_id: int,
    body: Optional[Dict[str, Any]] = None,
    db: Session = Depends(get_db),
):
    svc = TaskService(db)
    task = svc.get_task(task_id)
    opts = body or {}
    model = opts.get("model", settings.model)
    use_workiq = not opts.get("no_workiq", False)
    max_tasks_per_level = opts.get("max_tasks_per_level")
    if use_workiq and not is_workiq_eula_accepted(settings.workiq_eula_path):
        raise HTTPException(
            status_code=428,
            detail=(
                "WorkIQ EULA has not been accepted. "
                "Please accept the EULA in Settings before using WorkIQ features."
            ),
        )
    steps = await BreakdownService.breakdown_task(
        task,
        model=model,
        use_workiq=use_workiq,
        debug=settings.debug,
        max_tasks_per_level=max_tasks_per_level,
    )
    task = svc.update_breakdown(task_id, steps)
    # Preserve AI context from breakdown as a note (skip if one already exists)
    if context:
        try:
            # Re-read notes to get fresh state after potentially long breakdown
            db.refresh(task)
            if AI_CONTEXT_MARKER not in (task.notes or ""):
                ai_note = f"{AI_CONTEXT_MARKER}{context}"
                existing = task.notes or ""
                new_notes = f"{existing}\n\n{ai_note}" if existing else ai_note
                svc.add_note(task_id, new_notes)
        except Exception:
            _logger.debug("Failed to save context note for task %d", task_id, exc_info=True)
    svc.create_child_tasks(task, steps, settings.max_level)
    db.refresh(task)
    return _task_to_dict(task)


@app.get("/api/settings", response_model=Dict[str, Any])
def api_get_settings():
    return {
        "auto_breakdown_enabled": settings.auto_breakdown_enabled,
        "auto_breakdown_threshold_days": settings.auto_breakdown_threshold_days,
        "check_interval_hours": settings.check_interval_hours,
        "model": settings.model,
        "max_level": settings.max_level,
        "max_tasks_per_level": settings.max_tasks_per_level,
        "workiq_eula_accepted": is_workiq_eula_accepted(settings.workiq_eula_path),
        "workiq_eula_url": WORKIQ_EULA_URL,
    }


@app.put("/api/settings", response_model=Dict[str, Any])
def api_update_settings(body: Dict[str, Any]):
    # For a personal app, update in-memory settings (changes last until restart)
    for key in (
        "auto_breakdown_enabled",
        "auto_breakdown_threshold_days",
        "check_interval_hours",
    ):
        if key in body:
            object.__setattr__(settings, key, body[key])
    return api_get_settings()


@app.get("/api/workiq-eula", response_model=Dict[str, Any])
def api_get_workiq_eula():
    """Return current WorkIQ EULA acceptance status."""
    return {
        "accepted": is_workiq_eula_accepted(settings.workiq_eula_path),
        "eula_url": WORKIQ_EULA_URL,
    }


@app.post("/api/workiq-eula/accept", response_model=Dict[str, Any])
async def api_accept_workiq_eula():
    """Accept the WorkIQ EULA. Calls accept_eula on the MCP server."""
    if is_workiq_eula_accepted(settings.workiq_eula_path):
        return {"accepted": True, "message": "EULA already accepted."}
    success = await accept_workiq_eula_via_mcp(
        workiq_command=settings.workiq_command,
        workiq_args=settings.workiq_args,
        model=settings.model,
        eula_path=settings.workiq_eula_path,
    )
    if not success:
        # Best-effort: record locally even if MCP call didn't confirm
        save_workiq_eula_acceptance(settings.workiq_eula_path)
    return {"accepted": True, "message": "WorkIQ EULA accepted."}


# ---------------------------------------------------------------------------
# Web Routes
# ---------------------------------------------------------------------------


@app.get("/", response_class=HTMLResponse)
def web_index(
    request: Request,
    sort: Optional[str] = None,
    order: str = "desc",
    db: Session = Depends(get_db),
):
    svc = TaskService(db)
    tasks = svc.list_tasks(sort_by=sort, sort_order=order)

    # Group tasks into board columns
    backlog = [t for t in tasks if t.status == "open" and not t.daily_focus]
    today_tasks = [t for t in tasks if t.daily_focus and t.status in ("open", "done")]
    done = [t for t in tasks if t.status == "done" and not t.daily_focus]

    return templates.TemplateResponse(
        "index.html",
        {
            "request": request,
            "tasks": tasks,
            "backlog": backlog,
            "today_tasks": today_tasks,
            "done": done,
            "sort": sort or "created_at",
            "order": order,
            "current_date": _date.today(),
        },
    )


@app.get("/focus", response_class=HTMLResponse)
def web_focus(request: Request, db: Session = Depends(get_db)):
    svc = TaskService(db)
    tasks = svc.list_focus_tasks()
    return templates.TemplateResponse(
        "focus.html", {"request": request, "tasks": tasks}
    )


@app.post("/tasks/{task_id}/focus", response_class=HTMLResponse)
def web_toggle_focus(task_id: int, db: Session = Depends(get_db)):
    svc = TaskService(db)
    svc.toggle_focus(task_id)
    return RedirectResponse(url=f"/tasks/{task_id}", status_code=303)


@app.post("/tasks/{task_id}/due", response_class=HTMLResponse)
def web_set_due_date(
    task_id: int,
    due_date: Optional[str] = Form(None),
    db: Session = Depends(get_db),
):
    svc = TaskService(db)
    parsed_due = None
    if due_date:
        try:
            parsed_due = datetime.combine(
                _date.fromisoformat(due_date), datetime.min.time(), tzinfo=timezone.utc
            )
        except ValueError:
            # Preserve existing due date on invalid input to avoid data loss.
            return RedirectResponse(
                url=f"/tasks/{task_id}?error=invalid_due_date", status_code=303
            )
    svc.set_due_date(task_id, parsed_due)
    return RedirectResponse(url=f"/tasks/{task_id}", status_code=303)


@app.get("/tree", response_class=HTMLResponse)
def web_tree(request: Request, db: Session = Depends(get_db)):
    svc = TaskService(db)
    tree = svc.get_task_tree()
    return templates.TemplateResponse("tree.html", {"request": request, "tree": tree})


@app.post("/tasks", response_class=HTMLResponse)
def web_add_task(
    request: Request,
    background_tasks: BackgroundTasks,
    title: str = Form(...),
    due_date: Optional[str] = Form(None),
    db: Session = Depends(get_db),
):
    svc = TaskService(db)
    parsed_due = None
    if due_date:
        try:
            parsed_due = datetime.combine(
                _date.fromisoformat(due_date), datetime.min.time(), tzinfo=timezone.utc
            )
        except ValueError:
            pass
    task = svc.create_task(title.strip(), due_date=parsed_due)

    # Queue background WorkIQ context gathering for the new task
    if is_workiq_eula_accepted(settings.workiq_eula_path):
        background_tasks.add_task(_gather_task_context, task.id, title.strip())

    return RedirectResponse(url="/", status_code=303)


@app.post("/tasks/{task_id}/complete", response_class=HTMLResponse)
def web_complete_task(
    task_id: int,
    include_children: Optional[str] = Form(None),
    db: Session = Depends(get_db),
):
    svc = TaskService(db)
    svc.complete_task(task_id, include_children=include_children == "on")
    return RedirectResponse(url="/", status_code=303)


@app.post("/tasks/{task_id}/reopen", response_class=HTMLResponse)
def web_reopen_task(task_id: int, db: Session = Depends(get_db)):
    svc = TaskService(db)
    svc.reopen_task(task_id)
    return RedirectResponse(url="/", status_code=303)


@app.post("/tasks/{task_id}/archive", response_class=HTMLResponse)
def web_archive_task(
    task_id: int,
    include_children: Optional[str] = Form(None),
    db: Session = Depends(get_db),
):
    svc = TaskService(db)
    svc.archive_task(task_id, include_children=include_children == "on")
    return RedirectResponse(url="/", status_code=303)


@app.post("/tasks/{task_id}/delete", response_class=HTMLResponse)
def web_delete_task(task_id: int, db: Session = Depends(get_db)):
    svc = TaskService(db)
    svc.delete_task(task_id)
    return RedirectResponse(url="/", status_code=303)


@app.get("/tasks/{task_id}", response_class=HTMLResponse)
def web_task_detail(request: Request, task_id: int, db: Session = Depends(get_db)):
    svc = TaskService(db)
    task = svc.get_task(task_id)
    parent = None
    if task.parent_id:
        try:
            parent = svc.get_task(task.parent_id)
        except HTTPException:
            pass
    children = []
    if task.children_ids:
        for cid in task.children_ids:
            try:
                children.append(svc.get_task(cid))
            except HTTPException:
                pass
    return templates.TemplateResponse(
        "task_detail.html",
        {"request": request, "task": task, "parent": parent, "children": children},
    )


@app.post("/tasks/{task_id}/note", response_class=HTMLResponse)
def web_add_note(
    task_id: int,
    note: str = Form(...),
    db: Session = Depends(get_db),
):
    svc = TaskService(db)
    svc.add_note(task_id, note)
    return RedirectResponse(url=f"/tasks/{task_id}", status_code=303)


@app.post("/tasks/{task_id}/breakdown", response_class=HTMLResponse)
async def web_breakdown_task(
    task_id: int,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
):
    svc = TaskService(db)
    task = svc.get_task(task_id)

    async def _do_breakdown():
        _db_gen = get_db()
        _db = next(_db_gen)
        try:
            _svc = TaskService(_db)
            _task = _svc.get_task(task_id)
            use_workiq = is_workiq_eula_accepted(settings.workiq_eula_path)
            steps, context = await BreakdownService.breakdown_task(
                _task, use_workiq=use_workiq, debug=settings.debug
            )
            _task = _svc.update_breakdown(task_id, steps)
            # Preserve AI context from breakdown as a note (skip if one already exists)
            if context:
                try:
                    # Re-read notes to get fresh state after potentially long breakdown
                    _db.refresh(_task)
                    if AI_CONTEXT_MARKER not in (_task.notes or ""):
                        ai_note = f"{AI_CONTEXT_MARKER}{context}"
                        existing = _task.notes or ""
                        new_notes = f"{existing}\n\n{ai_note}" if existing else ai_note
                        _svc.add_note(task_id, new_notes)
                except Exception:
                    _logger.debug("Failed to save context note for task %d", task_id, exc_info=True)
            _svc.create_child_tasks(_task, steps, settings.max_level)
        finally:
            try:
                next(_db_gen)
            except StopIteration:
                pass

    background_tasks.add_task(_do_breakdown)
    return RedirectResponse(url=f"/tasks/{task_id}", status_code=303)


@app.get("/settings", response_class=HTMLResponse)
def web_settings(request: Request):
    return templates.TemplateResponse(
        "settings.html",
        {
            "request": request,
            "settings": settings,
            "workiq_eula_accepted": is_workiq_eula_accepted(settings.workiq_eula_path),
            "workiq_eula_url": WORKIQ_EULA_URL,
        },
    )


@app.post("/settings", response_class=HTMLResponse)
def web_update_settings(
    request: Request,
    auto_breakdown_enabled: Optional[str] = Form(None),
    auto_breakdown_threshold_days: int = Form(...),
    check_interval_hours: int = Form(...),
    max_tasks_per_level: str = Form(...),
):
    object.__setattr__(
        settings, "auto_breakdown_enabled", auto_breakdown_enabled == "on"
    )
    object.__setattr__(
        settings, "auto_breakdown_threshold_days", auto_breakdown_threshold_days
    )
    object.__setattr__(settings, "check_interval_hours", check_interval_hours)
    object.__setattr__(settings, "max_tasks_per_level", max_tasks_per_level.strip())
    return RedirectResponse(url="/settings", status_code=303)


@app.post("/settings/workiq-eula", response_class=HTMLResponse)
async def web_accept_workiq_eula(request: Request):
    """Accept the WorkIQ EULA via the web settings page."""
    if not is_workiq_eula_accepted(settings.workiq_eula_path):
        success = await accept_workiq_eula_via_mcp(
            workiq_command=settings.workiq_command,
            workiq_args=settings.workiq_args,
            model=settings.model,
            eula_path=settings.workiq_eula_path,
        )
        if not success:
            save_workiq_eula_acceptance(settings.workiq_eula_path)
    return RedirectResponse(url="/settings", status_code=303)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


_logger = logging.getLogger(__name__)


async def _gather_task_context(task_id: int, title: str) -> None:
    """Background task to gather WorkIQ context for a newly created task."""
    try:
        context = await BreakdownService.get_workiq_context(
            title=title, debug=settings.debug
        )
        if context:
            db_gen = get_db()
            db = next(db_gen)
            try:
                svc = TaskService(db)
                # Preserve any existing notes (user may have edited since creation)
                task = svc.get_task(task_id)
                existing = task.notes or ""
                ai_note = f"{AI_CONTEXT_MARKER}{context}"
                combined = f"{existing}\n\n{ai_note}" if existing else ai_note
                svc.add_note(task_id, combined)
            finally:
                try:
                    next(db_gen)
                except StopIteration:
                    pass
    except Exception:
        _logger.debug("Background context gathering failed for task %d", task_id, exc_info=True)


def _task_to_dict(task) -> Dict[str, Any]:
    return {
        "id": task.id,
        "title": task.title,
        "status": task.status,
        "breakdown": task.breakdown or [],
        "notes": task.notes,
        "source": task.source,
        "created_at": task.created_at.isoformat() if task.created_at else None,
        "updated_at": task.updated_at.isoformat() if task.updated_at else None,
        "atomic": task.atomic,
        "level": task.level,
        "parent_id": task.parent_id,
        "children_ids": task.children_ids or [],
        "auto_breakdown_enabled": task.auto_breakdown_enabled,
        "due_date": task.due_date.date().isoformat() if task.due_date else None,
        "daily_focus": task.daily_focus,
    }
