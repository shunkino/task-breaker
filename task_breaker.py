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
DEFAULT_USAGE_LOG = os.path.expanduser("~/.task-breaker/usage.log")


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


class UsageLogger:
    def __init__(self, destination: str, log_path: str) -> None:
        self.destination = destination
        self.log_path = log_path

    def emit(self, event: str, payload: Dict[str, Any]) -> None:
        if self.destination == "off":
            return
        record = {"timestamp": now_iso(), "event": event, **payload}
        line = json.dumps(record, ensure_ascii=True, separators=(",", ":"))
        if self.destination in ("stderr", "both"):
            print(line, file=sys.stderr)
        if self.destination in ("file", "both"):
            directory = os.path.dirname(self.log_path)
            if directory:
                os.makedirs(directory, exist_ok=True)
            with open(self.log_path, "a", encoding="utf-8") as handle:
                handle.write(f"{line}\n")


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
    usage_logger: Optional[UsageLogger] = None,
    source_command: Optional[str] = None,
    debug: bool = False,
) -> List[str]:
    client_opts: Dict[str, Any] = {}
    if debug:
        client_opts["log_level"] = "debug"
    client = CopilotClient(client_opts)
    await client.start()
    if usage_logger:
        usage_logger.emit(
            "copilot",
            {"model": model, "source_command": source_command},
        )
        if use_workiq:
            usage_logger.emit(
                "workiq",
                {
                    "command": workiq_command,
                    "args": workiq_args,
                    "source_command": source_command,
                },
            )

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

    if debug:
        def debug_handler(event: Any) -> None:
            data = event.data
            mcp_server = getattr(data, "mcp_server_name", None)
            mcp_tool = getattr(data, "mcp_tool_name", None)
            tool_name = getattr(data, "tool_name", None)
            content = getattr(data, "content", None)
            result = getattr(data, "result", None)
            parts = [f"[DEBUG] {event.type}"]
            if mcp_server:
                parts.append(f"mcp_server={mcp_server}")
            if mcp_tool:
                parts.append(f"mcp_tool={mcp_tool}")
            if tool_name:
                parts.append(f"tool={tool_name}")
            if content:
                parts.append(f"content={content[:200]}..." if len(str(content)) > 200 else f"content={content}")
            if result:
                parts.append(f"result={str(result)[:200]}..." if len(str(result)) > 200 else f"result={result}")
            print(" | ".join(parts), file=sys.stderr)
        session.on(debug_handler)

    if use_workiq:
        # First query WorkIQ to get better context about the task
        await session.send_and_wait(
            {
                "prompt": (
                    f"I need to break down this task: {title}\n\n"
                    "Before creating a breakdown, use WorkIQ MCP tool to gather relevant context. "
                    "Search for related work items, documentation, or prior discussions "
                    "that could inform how to approach this task. "
                    "Summarize what you find."
                )
            }
        )

    # Now request the breakdown with any context gathered
    response = await session.send_and_wait(
        {
            "prompt": (
                "Based on any context gathered, break down this task following the rules above. "
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
    args.usage_logger.emit(
        "command",
        {
            "name": "add",
            "breakdown": args.breakdown,
            "model": args.model if args.breakdown else None,
            "workiq_enabled": not args.no_workiq if args.breakdown else None,
        },
    )
    breakdown: List[str] = []
    if args.breakdown:
        breakdown = asyncio.run(
            breakdown_task(
                title=args.title,
                model=args.model,
                use_workiq=not args.no_workiq,
                workiq_command=args.workiq_command,
                workiq_args=args.workiq_args,
                usage_logger=args.usage_logger,
                source_command="add",
                debug=args.debug,
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
    args.usage_logger.emit("command", {"name": "list", "status": args.status})
    print(render_tasks(tasks))


def cmd_show(args: argparse.Namespace) -> None:
    tasks = load_tasks(args.storage)
    task = find_task(tasks, args.id)
    args.usage_logger.emit("command", {"name": "show", "task_id": args.id})
    print(render_task(task))


def cmd_complete(args: argparse.Namespace) -> None:
    tasks = load_tasks(args.storage)
    task = find_task(tasks, args.id)
    task.status = "done"
    task.updated_at = now_iso()
    save_tasks(args.storage, tasks)
    args.usage_logger.emit("command", {"name": "complete", "task_id": args.id})
    print(render_task(task))


def cmd_note(args: argparse.Namespace) -> None:
    tasks = load_tasks(args.storage)
    task = find_task(tasks, args.id)
    task.notes = args.note
    task.updated_at = now_iso()
    save_tasks(args.storage, tasks)
    args.usage_logger.emit(
        "command",
        {"name": "note", "task_id": args.id, "note_length": len(args.note)},
    )
    print(render_task(task))


def cmd_breakdown(args: argparse.Namespace) -> None:
    tasks = load_tasks(args.storage)
    task = find_task(tasks, args.id)
    args.usage_logger.emit(
        "command",
        {
            "name": "breakdown",
            "task_id": args.id,
            "model": args.model,
            "workiq_enabled": not args.no_workiq,
        },
    )
    steps = asyncio.run(
        breakdown_task(
            title=task.title,
            model=args.model,
            use_workiq=not args.no_workiq,
            workiq_command=args.workiq_command,
            workiq_args=args.workiq_args,
            usage_logger=args.usage_logger,
            source_command="breakdown",
            debug=args.debug,
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
    parser.add_argument(
        "--usage-log",
        nargs="?",
        const="stderr",
        default="off",
        choices=["off", "stderr", "file", "both"],
        help="Usage log output: off|stderr|file|both (default: off).",
    )
    parser.add_argument(
        "--usage-log-path",
        default=DEFAULT_USAGE_LOG,
        help=f"Usage log file path (default: {DEFAULT_USAGE_LOG})",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    add_parser = subparsers.add_parser("add", help="Add a new task")
    add_parser.add_argument("title", help="Task title")
    add_parser.add_argument(
        "--breakdown",
        action="store_true",
        help="Generate breakdown on add",
    )
    add_parser.add_argument(
        "--debug",
        action="store_true",
        help="Enable debug logging for Copilot SDK and MCP tool execution",
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
    breakdown_parser.add_argument(
        "--debug",
        action="store_true",
        help="Enable debug logging for Copilot SDK and MCP tool execution",
    )
    breakdown_parser.set_defaults(func=cmd_breakdown)

    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    args.usage_logger = UsageLogger(args.usage_log, args.usage_log_path)
    try:
        args.func(args)
    except KeyError as exc:
        print(str(exc), file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
