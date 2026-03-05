from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import BackgroundTasks, Depends, FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from .config import settings
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
def api_list_tasks(status: Optional[str] = None, db: Session = Depends(get_db)):
    svc = TaskService(db)
    tasks = svc.list_tasks(status=status)
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
def api_create_task(body: Dict[str, Any], db: Session = Depends(get_db)):
    title = body.get("title", "").strip()
    if not title:
        raise HTTPException(status_code=422, detail="title is required")
    svc = TaskService(db)
    task = svc.create_task(title)
    return _task_to_dict(task)


@app.get("/api/tasks/{task_id}", response_model=Dict[str, Any])
def api_get_task(task_id: int, db: Session = Depends(get_db)):
    svc = TaskService(db)
    return _task_to_dict(svc.get_task(task_id))


@app.post("/api/tasks/{task_id}/complete", response_model=Dict[str, Any])
def api_complete_task(task_id: int, db: Session = Depends(get_db)):
    svc = TaskService(db)
    return _task_to_dict(svc.complete_task(task_id))


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
    steps = await BreakdownService.breakdown_task(
        task, model=model, use_workiq=use_workiq
    )
    task = svc.update_breakdown(task_id, steps)
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


# ---------------------------------------------------------------------------
# Web Routes
# ---------------------------------------------------------------------------


@app.get("/", response_class=HTMLResponse)
def web_index(request: Request, db: Session = Depends(get_db)):
    svc = TaskService(db)
    tasks = svc.list_tasks()
    return templates.TemplateResponse(
        "index.html", {"request": request, "tasks": tasks}
    )


@app.get("/tree", response_class=HTMLResponse)
def web_tree(request: Request, db: Session = Depends(get_db)):
    svc = TaskService(db)
    tree = svc.get_task_tree()
    return templates.TemplateResponse(
        "tree.html", {"request": request, "tree": tree}
    )


@app.post("/tasks", response_class=HTMLResponse)
def web_add_task(
    request: Request,
    title: str = Form(...),
    db: Session = Depends(get_db),
):
    svc = TaskService(db)
    svc.create_task(title.strip())
    return RedirectResponse(url="/", status_code=303)


@app.post("/tasks/{task_id}/complete", response_class=HTMLResponse)
def web_complete_task(task_id: int, db: Session = Depends(get_db)):
    svc = TaskService(db)
    svc.complete_task(task_id)
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
            steps = await BreakdownService.breakdown_task(_task)
            _task = _svc.update_breakdown(task_id, steps)
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
        "settings.html", {"request": request, "settings": settings}
    )


@app.post("/settings", response_class=HTMLResponse)
def web_update_settings(
    request: Request,
    auto_breakdown_enabled: Optional[str] = Form(None),
    auto_breakdown_threshold_days: int = Form(...),
    check_interval_hours: int = Form(...),
):
    object.__setattr__(
        settings, "auto_breakdown_enabled", auto_breakdown_enabled == "on"
    )
    object.__setattr__(
        settings, "auto_breakdown_threshold_days", auto_breakdown_threshold_days
    )
    object.__setattr__(settings, "check_interval_hours", check_interval_hours)
    return RedirectResponse(url="/settings", status_code=303)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


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
    }
