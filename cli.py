#!/usr/bin/env python3
"""Typer-based CLI client for Task Breaker server."""
import sys
from typing import Optional

import httpx
import typer
import uvicorn

app = typer.Typer(
    help="Task Breaker CLI — interacts with the local Task Breaker server."
)

_DEFAULT_BASE_URL = "http://127.0.0.1:8000"


def _client(base_url: str = _DEFAULT_BASE_URL) -> httpx.Client:
    return httpx.Client(base_url=base_url, timeout=300)


def _check_server(base_url: str) -> bool:
    try:
        with _client(base_url) as client:
            client.get("/api/tasks")
        return True
    except httpx.ConnectError:
        return False


def _require_server(base_url: str) -> None:
    if not _check_server(base_url):
        typer.echo(
            f"Cannot connect to Task Breaker server at {base_url}.\n"
            "Start it with:  python cli.py serve",
            err=True,
        )
        raise typer.Exit(1)


@app.command()
def serve(
    host: str = typer.Option("127.0.0.1", help="Bind host"),
    port: int = typer.Option(8000, help="Bind port"),
    reload: bool = typer.Option(False, help="Enable auto-reload (development)"),
):
    """Start the Task Breaker server."""
    uvicorn.run("task_breaker.app:app", host=host, port=port, reload=reload)


@app.command("list")
def list_tasks(
    status: Optional[str] = typer.Option(None, help="Filter by status: open|done"),
    sort: Optional[str] = typer.Option(None, help="Sort field: id|due_date|level|status|created_at|updated_at|title"),
    order: str = typer.Option("desc", help="Sort direction: asc|desc"),
    url: str = typer.Option(_DEFAULT_BASE_URL, help="Server base URL"),
):
    """List tasks."""
    _require_server(url)
    params = {}
    if status:
        params["status"] = status
    if sort:
        params["sort"] = sort
    params["order"] = order
    with _client(url) as client:
        resp = client.get("/api/tasks", params=params)
        resp.raise_for_status()
    tasks = resp.json()
    if not tasks:
        typer.echo("No tasks.")
        return
    for t in tasks:
        status_str = "✓" if t["status"] == "done" else "○"
        atomic_str = " 🔒" if t.get("atomic") else ""
        focus_str = " ⭐" if t.get("daily_focus") else ""
        due_str = f"  due: {t['due_date']}" if t.get("due_date") else ""
        typer.echo(
            f"[{t['id']}] {status_str} {t['title']}{atomic_str}{focus_str}  (level {t['level']}){due_str}"
        )


def _print_tree_node(node: dict, prefix: str = "", is_last: bool = True) -> None:
    """Recursively print a tree node with box-drawing characters."""
    connector = "└── " if is_last else "├── "
    status_icon = "✓" if node["status"] == "done" else "○"
    atomic_str = " 🔒" if node.get("atomic") else ""
    typer.echo(
        f"{prefix}{connector}[{node['id']}] {status_icon} {node['title']}{atomic_str}"
    )
    children = node.get("children", [])
    child_prefix = prefix + ("    " if is_last else "│   ")
    for i, child in enumerate(children):
        _print_tree_node(child, child_prefix, i == len(children) - 1)


@app.command("tree")
def tree_tasks(
    task_id: Optional[int] = typer.Argument(None, help="Optional task ID to show subtree"),
    url: str = typer.Option(_DEFAULT_BASE_URL, help="Server base URL"),
):
    """Show tasks as a hierarchical tree."""
    _require_server(url)
    with _client(url) as client:
        if task_id is not None:
            resp = client.get(f"/api/tasks/{task_id}/tree")
        else:
            resp = client.get("/api/tasks/tree")
        resp.raise_for_status()
    tree = resp.json()
    if not tree:
        typer.echo("No tasks.")
        return
    typer.echo("Task Hierarchy")
    for i, root in enumerate(tree):
        _print_tree_node(root, "", i == len(tree) - 1)


@app.command("add")
def add_task(
    title: str = typer.Argument(..., help="Task title"),
    breakdown: bool = typer.Option(
        False, "--breakdown", help="Trigger AI breakdown immediately"
    ),
    due: Optional[str] = typer.Option(None, "--due", help="Due date in YYYY-MM-DD format"),
    url: str = typer.Option(_DEFAULT_BASE_URL, help="Server base URL"),
):
    """Add a new task."""
    _require_server(url)
    body: dict = {"title": title}
    if due:
        body["due_date"] = due
    with _client(url) as client:
        resp = client.post("/api/tasks", json=body)
        resp.raise_for_status()
        task = resp.json()
        task_id = task["id"]
        typer.echo(f"Created task #{task_id}: {task['title']}")
        if task.get("due_date"):
            typer.echo(f"  due: {task['due_date']}")
        if breakdown:
            typer.echo("Triggering AI breakdown (this may take a while)…")
            resp2 = client.post(f"/api/tasks/{task_id}/breakdown")
            resp2.raise_for_status()
            task = resp2.json()
            for step in task.get("breakdown", []):
                typer.echo(f"  - {step}")


@app.command("show")
def show_task(
    task_id: int = typer.Argument(..., help="Task ID"),
    url: str = typer.Option(_DEFAULT_BASE_URL, help="Server base URL"),
):
    """Show task details."""
    _require_server(url)
    with _client(url) as client:
        resp = client.get(f"/api/tasks/{task_id}")
        resp.raise_for_status()
    t = resp.json()
    typer.echo(f"[{t['id']}] {t['title']}")
    typer.echo(f"  status:  {t['status']}")
    typer.echo(f"  level:   {t['level']}")
    if t.get("daily_focus"):
        typer.echo("  daily focus: ⭐")
    if t.get("due_date"):
        typer.echo(f"  due:     {t['due_date']}")
    if t.get("atomic"):
        typer.echo("  atomic:  yes")
    if t.get("parent_id"):
        typer.echo(f"  parent:  #{t['parent_id']}")
    if t.get("children_ids"):
        ids = ", ".join(f"#{c}" for c in t["children_ids"])
        typer.echo(f"  children: {ids}")
    if t.get("breakdown"):
        typer.echo("  breakdown:")
        for step in t["breakdown"]:
            typer.echo(f"    - {step}")
    if t.get("notes"):
        typer.echo(f"  notes:   {t['notes']}")
    typer.echo(f"  updated: {t['updated_at']}")


@app.command("breakdown")
def breakdown_task(
    task_id: int = typer.Argument(..., help="Task ID"),
    url: str = typer.Option(_DEFAULT_BASE_URL, help="Server base URL"),
):
    """Trigger AI breakdown for a task."""
    _require_server(url)
    typer.echo("Running AI breakdown (this may take several minutes)…")
    with _client(url) as client:
        resp = client.post(f"/api/tasks/{task_id}/breakdown")
        resp.raise_for_status()
    task = resp.json()
    typer.echo(f"Breakdown for task #{task_id}:")
    for step in task.get("breakdown", []):
        typer.echo(f"  - {step}")


@app.command("complete")
def complete_task(
    task_id: int = typer.Argument(..., help="Task ID"),
    url: str = typer.Option(_DEFAULT_BASE_URL, help="Server base URL"),
):
    """Mark a task as done."""
    _require_server(url)
    with _client(url) as client:
        resp = client.post(f"/api/tasks/{task_id}/complete")
        resp.raise_for_status()
    typer.echo(f"Task #{task_id} marked as done.")


@app.command("note")
def add_note(
    task_id: int = typer.Argument(..., help="Task ID"),
    text: str = typer.Argument(..., help="Note text"),
    url: str = typer.Option(_DEFAULT_BASE_URL, help="Server base URL"),
):
    """Add or replace a note on a task."""
    _require_server(url)
    with _client(url) as client:
        resp = client.post(f"/api/tasks/{task_id}/note", json={"note": text})
        resp.raise_for_status()
    typer.echo(f"Note updated for task #{task_id}.")


@app.command("delete")
def delete_task(
    task_id: int = typer.Argument(..., help="Task ID"),
    url: str = typer.Option(_DEFAULT_BASE_URL, help="Server base URL"),
):
    """Delete a task."""
    _require_server(url)
    with _client(url) as client:
        resp = client.delete(f"/api/tasks/{task_id}")
        resp.raise_for_status()
    task = resp.json()
    typer.echo(f"Deleted task #{task['id']}: {task['title']}")


@app.command("due")
def set_due_date(
    task_id: int = typer.Argument(..., help="Task ID"),
    date: str = typer.Argument(..., help="Due date in YYYY-MM-DD format (empty string to clear)"),
    url: str = typer.Option(_DEFAULT_BASE_URL, help="Server base URL"),
):
    """Set or update the due date of a task."""
    _require_server(url)
    with _client(url) as client:
        resp = client.put(f"/api/tasks/{task_id}/due", json={"due_date": date or None})
        resp.raise_for_status()
    t = resp.json()
    due = t.get("due_date")
    if due:
        typer.echo(f"Task #{task_id} due date set to {due}.")
    else:
        typer.echo(f"Task #{task_id} due date cleared.")


@app.command("focus")
def toggle_focus(
    task_id: int = typer.Argument(..., help="Task ID"),
    url: str = typer.Option(_DEFAULT_BASE_URL, help="Server base URL"),
):
    """Toggle daily focus for a task."""
    _require_server(url)
    with _client(url) as client:
        resp = client.post(f"/api/tasks/{task_id}/focus")
        resp.raise_for_status()
    t = resp.json()
    state = "added to" if t.get("daily_focus") else "removed from"
    typer.echo(f"Task #{task_id} {state} daily focus.")


@app.command("focus-list")
def focus_list(
    url: str = typer.Option(_DEFAULT_BASE_URL, help="Server base URL"),
):
    """List daily focus tasks."""
    _require_server(url)
    with _client(url) as client:
        resp = client.get("/api/tasks/focus")
        resp.raise_for_status()
    tasks = resp.json()
    if not tasks:
        typer.echo("No daily focus tasks.")
        return
    typer.echo("Daily Focus Tasks:")
    for t in tasks:
        status_str = "✓" if t["status"] == "done" else "○"
        due_str = f"  due: {t['due_date']}" if t.get("due_date") else ""
        typer.echo(f"[{t['id']}] {status_str} {t['title']}  (level {t['level']}){due_str}")


if __name__ == "__main__":
    app()
