#!/usr/bin/env python3
import argparse
import asyncio
import json
import os
import sys
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from copilot import CopilotClient

APP_NAME = "task-breaker"
DEFAULT_MODEL = "gpt-4.1"
DEFAULT_STORAGE = os.path.expanduser("~/.task-breaker/tasks.json")


@dataclass
class Task:
    id: int
    title: str
    status: str
    created_at: str
    updated_at: str
    breakdown: List[str]
    notes: Optional[str] = None
    source: Optional[str] = None


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def load_tasks(path: str) -> List[Task]:
    if not os.path.exists(path):
        return []
    with open(path, "r", encoding="utf-8") as handle:
        data = json.load(handle)
    return [Task(**item) for item in data]


def save_tasks(path: str, tasks: List[Task]) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        json.dump([asdict(task) for task in tasks], handle, indent=2)


def next_task_id(tasks: List[Task]) -> int:
    return max((task.id for task in tasks), default=0) + 1


def find_task(tasks: List[Task], task_id: int) -> Task:
    for task in tasks:
        if task.id == task_id:
            return task
    raise KeyError(f"Task {task_id} not found")


def render_task(task: Task) -> str:
    lines = [f"[{task.id}] {task.title}", f"  status: {task.status}"]
    if task.breakdown:
        lines.append("  breakdown:")
        for step in task.breakdown:
            lines.append(f"    - {step}")
    if task.notes:
        lines.append(f"  notes: {task.notes}")
    if task.source:
        lines.append(f"  source: {task.source}")
    lines.append(f"  updated: {task.updated_at}")
    return "\n".join(lines)


def render_tasks(tasks: List[Task]) -> str:
    if not tasks:
        return "No tasks yet."
    return "\n\n".join(render_task(task) for task in tasks)


async def breakdown_task(
    title: str,
    model: str,
    use_workiq: bool,
    workiq_command: str,
    workiq_args: List[str],
) -> List[str]:
    client = CopilotClient()
    await client.start()

    session_config: Dict[str, Any] = {
        "model": model,
        "system_message": {
            "content": (
                "You are a task manager that breaks down tasks."
                " Follow these rules:"
                " (a) If the task is vague, break it into smaller steps."
                " (b) If it is unclear how to achieve the goal, investigate context using WorkIQ."
                " (c) If the task is to implement something or a small dashboard would help,"
                " propose a small implementation project as steps."
                " Return the breakdown as a JSON array of short step strings."
                " If you used WorkIQ, mention that in a final step like 'Review WorkIQ findings'."
            )
        },
    }

    if use_workiq:
        session_config["mcp_servers"] = {
            "workiq": {
                "type": "local",
                "command": workiq_command,
                "args": workiq_args,
                "tools": ["*"],
                "timeout": 60000,
            }
        }

    session = await client.create_session(session_config)
    response = await session.send_and_wait(
        {
            "prompt": (
                "Break down this task following the rules above. "
                "Return ONLY a JSON array of steps. Task: "
                f"{title}"
            )
        }
    )

    await session.destroy()
    await client.stop()

    content = response.data.content if response and response.data else "[]"
    try:
        steps = json.loads(content)
    except json.JSONDecodeError:
        steps = [content.strip()]
    if not isinstance(steps, list):
        steps = [str(steps)]
    return [str(step).strip() for step in steps if str(step).strip()]


def cmd_add(args: argparse.Namespace) -> None:
    tasks = load_tasks(args.storage)
    task_id = next_task_id(tasks)
    timestamp = now_iso()
    breakdown: List[str] = []
    if args.breakdown:
        breakdown = asyncio.run(
            breakdown_task(
                title=args.title,
                model=args.model,
                use_workiq=not args.no_workiq,
                workiq_command=args.workiq_command,
                workiq_args=args.workiq_args,
            )
        )
    task = Task(
        id=task_id,
        title=args.title,
        status="open",
        created_at=timestamp,
        updated_at=timestamp,
        breakdown=breakdown,
    )
    tasks.append(task)
    save_tasks(args.storage, tasks)
    print(render_task(task))


def cmd_list(args: argparse.Namespace) -> None:
    tasks = load_tasks(args.storage)
    if args.status:
        tasks = [task for task in tasks if task.status == args.status]
    print(render_tasks(tasks))


def cmd_show(args: argparse.Namespace) -> None:
    tasks = load_tasks(args.storage)
    task = find_task(tasks, args.id)
    print(render_task(task))


def cmd_complete(args: argparse.Namespace) -> None:
    tasks = load_tasks(args.storage)
    task = find_task(tasks, args.id)
    task.status = "done"
    task.updated_at = now_iso()
    save_tasks(args.storage, tasks)
    print(render_task(task))


def cmd_note(args: argparse.Namespace) -> None:
    tasks = load_tasks(args.storage)
    task = find_task(tasks, args.id)
    task.notes = args.note
    task.updated_at = now_iso()
    save_tasks(args.storage, tasks)
    print(render_task(task))


def cmd_breakdown(args: argparse.Namespace) -> None:
    tasks = load_tasks(args.storage)
    task = find_task(tasks, args.id)
    steps = asyncio.run(
        breakdown_task(
            title=task.title,
            model=args.model,
            use_workiq=not args.no_workiq,
            workiq_command=args.workiq_command,
            workiq_args=args.workiq_args,
        )
    )
    task.breakdown = steps
    task.updated_at = now_iso()
    save_tasks(args.storage, tasks)
    print(render_task(task))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog=APP_NAME)
    parser.add_argument(
        "--storage",
        default=DEFAULT_STORAGE,
        help=f"Path to tasks file (default: {DEFAULT_STORAGE})",
    )
    parser.add_argument(
        "--model",
        default=DEFAULT_MODEL,
        help=f"Copilot model (default: {DEFAULT_MODEL})",
    )
    parser.add_argument(
        "--no-workiq",
        action="store_true",
        help="Disable WorkIQ MCP server usage",
    )
    parser.add_argument(
        "--workiq-command",
        default="workiq",
        help="WorkIQ command (default: workiq). Use 'npx' with --workiq-args for npx-based invocation.",
    )
    parser.add_argument(
        "--workiq-args",
        nargs="*",
        default=["mcp"],
        help="Args for WorkIQ MCP server (default: mcp). For npx: -y @microsoft/workiq mcp",
    )

    subparsers = parser.add_subparsers(dest="command", required=True)

    add_parser = subparsers.add_parser("add", help="Add a new task")
    add_parser.add_argument("title", help="Task title")
    add_parser.add_argument(
        "--breakdown",
        action="store_true",
        help="Generate breakdown on add",
    )
    add_parser.set_defaults(func=cmd_add)

    list_parser = subparsers.add_parser("list", help="List tasks")
    list_parser.add_argument(
        "--status", choices=["open", "done"], help="Filter by status"
    )
    list_parser.set_defaults(func=cmd_list)

    show_parser = subparsers.add_parser("show", help="Show task detail")
    show_parser.add_argument("id", type=int, help="Task id")
    show_parser.set_defaults(func=cmd_show)

    complete_parser = subparsers.add_parser("complete", help="Mark task as done")
    complete_parser.add_argument("id", type=int, help="Task id")
    complete_parser.set_defaults(func=cmd_complete)

    note_parser = subparsers.add_parser("note", help="Add/replace task note")
    note_parser.add_argument("id", type=int, help="Task id")
    note_parser.add_argument("note", help="Note text")
    note_parser.set_defaults(func=cmd_note)

    breakdown_parser = subparsers.add_parser("breakdown", help="Break down a task")
    breakdown_parser.add_argument("id", type=int, help="Task id")
    breakdown_parser.set_defaults(func=cmd_breakdown)

    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    try:
        args.func(args)
    except KeyError as exc:
        print(str(exc), file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
