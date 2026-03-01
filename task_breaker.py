#!/usr/bin/env python3
import argparse
import asyncio
import json
import os
import re
import shutil
import sys
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from copilot import CopilotClient
from copilot.generated.session_events import SessionEventType

APP_NAME = "task-breaker"
DEFAULT_MODEL = "gpt-4.1"
DEFAULT_STORAGE = os.path.expanduser("~/.task-breaker/tasks.json")
DEFAULT_USAGE_LOG = os.path.expanduser("~/.task-breaker/usage.log")
DEFAULT_MAX_LEVEL = 3


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
    atomic: bool = False
    level: int = 0
    parent_id: Optional[int] = None
    children_ids: Optional[List[int]] = None


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
    if task.atomic:
        lines.append("  atomic: yes")
    if task.level > 0:
        lines.append(f"  level: {task.level}")
    if task.parent_id is not None:
        lines.append(f"  parent: #{task.parent_id}")
    if task.children_ids:
        ids_str = ", ".join(f"#{cid}" for cid in task.children_ids)
        lines.append(f"  children: {ids_str}")
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


def resolve_copilot_cli_path(debug: bool = False) -> Optional[str]:
    """On Windows, resolve the full path to the copilot CLI.

    subprocess.Popen uses CreateProcess which cannot find .bat/.cmd/.ps1
    files by bare name — it only finds .exe. We use shutil.which (which
    respects PATHEXT) to resolve the full path and pass it explicitly.
    """
    if sys.platform != "win32":
        if debug:
            print(
                "[DEBUG] resolve_copilot_cli_path: not Windows, skipping",
                file=sys.stderr,
            )
        return None
    # Honour explicit override
    env_path = os.environ.get("COPILOT_CLI_PATH")
    if env_path:
        if debug:
            print(
                f"[DEBUG] resolve_copilot_cli_path: COPILOT_CLI_PATH is set to '{env_path}'",
                file=sys.stderr,
            )
        return None
    # Resolve via shutil.which (respects PATHEXT: .cmd, .bat, .exe, etc.)
    found = shutil.which("copilot")
    if debug:
        print(
            f"[DEBUG] resolve_copilot_cli_path: shutil.which('copilot') = '{found}'",
            file=sys.stderr,
        )
    if found:
        # Always return the full path so subprocess.Popen/CreateProcess can
        # launch .bat/.cmd files (it can when given an absolute path).
        if debug:
            print(
                f"[DEBUG] resolve_copilot_cli_path: using resolved path '{found}'",
                file=sys.stderr,
            )
        return found
    # Fallback: try the npm global .cmd wrapper directly
    appdata = os.environ.get("APPDATA", "")
    if debug:
        print(
            f"[DEBUG] resolve_copilot_cli_path: APPDATA = '{appdata}'", file=sys.stderr
        )
    if appdata:
        cmd_path = os.path.join(appdata, "npm", "copilot.cmd")
        exists = os.path.isfile(cmd_path)
        if debug:
            print(
                f"[DEBUG] resolve_copilot_cli_path: checking '{cmd_path}' exists={exists}",
                file=sys.stderr,
            )
        if exists:
            return cmd_path
    # List what's actually in the npm directory for diagnostics
    if debug:
        npm_dir = os.path.join(appdata, "npm") if appdata else ""
        if npm_dir and os.path.isdir(npm_dir):
            copilot_files = [f for f in os.listdir(npm_dir) if "copilot" in f.lower()]
            print(
                f"[DEBUG] resolve_copilot_cli_path: copilot-related files in npm dir: {copilot_files}",
                file=sys.stderr,
            )
        else:
            print(
                f"[DEBUG] resolve_copilot_cli_path: npm dir '{npm_dir}' not found",
                file=sys.stderr,
            )
    return None


def slugify(text: str, max_words: int = 5) -> str:
    """Turn text into a short lowercase slug suitable for directory names."""
    text = re.sub(r"[^\w\s-]", "", text.lower())
    words = text.split()[:max_words]
    return "-".join(words) or "task"


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
    cli_path = resolve_copilot_cli_path(debug=debug)
    if cli_path:
        client_opts["cli_path"] = cli_path
    if debug:
        print(f"[DEBUG] breakdown_task: client_opts = {client_opts}", file=sys.stderr)
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
        mcp_command = workiq_command
        # Flatten args: split any multi-word args so each token is separate
        mcp_args = [token for arg in workiq_args for token in arg.split()]
        # Windows needs cmd /c wrapping for npx (and similar) commands
        # See: https://github.com/github/copilot-sdk/blob/main/docs/mcp/debugging.md#npx-commands
        if sys.platform == "win32":
            mcp_args = ["/c", mcp_command] + mcp_args
            mcp_command = "cmd"
        session_config["mcp_servers"] = {
            "workiq": {
                "type": "local",
                "command": mcp_command,
                "args": mcp_args,
                "tools": ["*"],
                "timeout": 180000,
            }
        }

    if debug:
        print(
            f"[DEBUG] session_config = {json.dumps(session_config, indent=2)}",
            file=sys.stderr,
        )

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
                parts.append(
                    f"content={content[:200]}..."
                    if len(str(content)) > 200
                    else f"content={content}"
                )
            if result:
                parts.append(
                    f"result={str(result)[:200]}..."
                    if len(str(result)) > 200
                    else f"result={result}"
                )
            print(" | ".join(parts), file=sys.stderr)

        session.on(debug_handler)

    if use_workiq:
        # First query WorkIQ to get better context about the task
        # WorkIQ tool calls can take 2+ minutes; use a longer timeout than the default
        await session.send_and_wait(
            {
                "prompt": (
                    f"I need to break down this task: {title}\n\n"
                    "Before creating a breakdown, use WorkIQ MCP tool to gather relevant context. "
                    "The tool name of WorkIQ MCP tool is exposed as 'ask_work_iq'. Be sure to use this."
                    "Search for related work items, documentation, or prior discussions "
                    "that could inform how to approach this task. "
                    "Summarize what you find."
                )
            },
            timeout=180000,
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


def _make_permission_handler(project_dir: str):
    """Create a permission handler that auto-approves read/write in the project dir."""
    norm_project = os.path.normcase(os.path.abspath(project_dir))

    def _handler(request: dict, context: dict) -> dict:
        kind = request.get("kind", "unknown")
        path = request.get("path", "")

        # Auto-approve read/write operations inside the project directory
        if kind in ("read", "write") and path:
            norm_path = os.path.normcase(os.path.abspath(path))
            if norm_path.startswith(norm_project):
                return {"kind": "approved"}

        # Everything else: ask the user
        details: List[str] = []
        for key, value in request.items():
            if key in ("kind", "toolCallId"):
                continue
            details.append(f"  {key}: {value}")
        print(f"\n[Permission requested] {kind}")
        if details:
            print("\n".join(details))
        answer = input("Allow this action? [y/N]: ").strip().lower()
        if answer in ("y", "yes"):
            return {"kind": "approved"}
        return {"kind": "denied-interactively-by-user"}

    return _handler


async def implement_task(
    task: Task,
    model: str,
    use_workiq: bool = True,
    workiq_command: str = "npx",
    workiq_args: Optional[List[str]] = None,
    debug: bool = False,
) -> Tuple[str, bool]:
    """Ask GitHub Copilot to implement a task inside a new project directory.

    Returns (project_dir, success) where success is False when the agent
    could not fully implement the task.
    """
    dir_name = f"{task.id}-{slugify(task.title)}"
    project_dir = os.path.abspath(dir_name)
    os.makedirs(project_dir, exist_ok=True)

    client_opts: Dict[str, Any] = {}
    if debug:
        client_opts["log_level"] = "debug"
    cli_path = resolve_copilot_cli_path(debug=debug)
    if cli_path:
        client_opts["cli_path"] = cli_path
    if debug:
        print(f"[DEBUG] implement_task: client_opts = {client_opts}", file=sys.stderr)
    client = CopilotClient(client_opts)
    await client.start()

    workiq_instruction = ""
    if use_workiq:
        workiq_instruction = (
            " When you need information about internal tools, SDKs, APIs, "
            "or packages, use WorkIQ (ask_work_iq) to look up documentation "
            "and context BEFORE attempting to install or fetch external resources."
        )

    session_config: Dict[str, Any] = {
        "model": model,
        "working_directory": project_dir,
        "on_permission_request": _make_permission_handler(project_dir),
        "system_message": {
            "content": (
                "You are a software engineer that implements projects. "
                "You MUST create all files inside the current working directory. "
                "Create a complete, runnable project with appropriate structure, "
                "including a README.md explaining how to build and run it."
                + workiq_instruction
            )
        },
    }

    if use_workiq:
        _workiq_args = workiq_args or ["-y", "@microsoft/workiq", "mcp"]
        mcp_command = workiq_command
        mcp_args = [token for arg in _workiq_args for token in arg.split()]
        if sys.platform == "win32":
            mcp_args = ["/c", mcp_command] + mcp_args
            mcp_command = "cmd"
        session_config["mcp_servers"] = {
            "workiq": {
                "type": "local",
                "command": mcp_command,
                "args": mcp_args,
                "tools": ["*"],
                "timeout": 180000,
            }
        }

    if debug:
        print(
            f"[DEBUG] implement session_config = {json.dumps({k: v for k, v in session_config.items() if k != 'on_permission_request'}, indent=2)}",
            file=sys.stderr,
        )

    session = await client.create_session(session_config)

    errors: List[str] = []

    def _track_errors(event: Any) -> None:
        data = event.data
        if event.type == SessionEventType.SESSION_ERROR:
            error_type = getattr(data, "error_type", "")
            message = getattr(data, "message", "")
            error = getattr(data, "error", "")
            parts = [p for p in [error_type, message, str(error)] if p]
            msg = " | ".join(parts) or "Unknown session error"
            errors.append(msg)
            print(f"\n[Session Error] {msg}", file=sys.stderr)

    session.on(_track_errors)

    if debug:

        def debug_handler(event: Any) -> None:
            data = event.data
            parts = [f"[DEBUG-IMPL] {event.type}"]
            # Error details
            error_type = getattr(data, "error_type", None)
            message = getattr(data, "message", None)
            error = getattr(data, "error", None)
            if error_type:
                parts.append(f"error_type={error_type}")
            if message:
                parts.append(f"message={message}")
            if error:
                parts.append(f"error={str(error)[:300]}")
            # Tool details
            mcp_server = getattr(data, "mcp_server_name", None)
            mcp_tool = getattr(data, "mcp_tool_name", None)
            tool_name = getattr(data, "tool_name", None)
            if mcp_server:
                parts.append(f"mcp_server={mcp_server}")
            if mcp_tool:
                parts.append(f"mcp_tool={mcp_tool}")
            if tool_name:
                parts.append(f"tool={tool_name}")
            # Content / result
            content = getattr(data, "content", None)
            result = getattr(data, "result", None)
            if content:
                parts.append(
                    f"content={content[:200]}..."
                    if len(str(content)) > 200
                    else f"content={content}"
                )
            if result:
                parts.append(
                    f"result={str(result)[:200]}..."
                    if len(str(result)) > 200
                    else f"result={result}"
                )
            print(" | ".join(parts), file=sys.stderr)

        session.on(debug_handler)

    # Build the implementation prompt
    if task.breakdown:
        steps_text = "\n".join(f"  {i+1}. {s}" for i, s in enumerate(task.breakdown))
        impl_detail = (
            f"Task: {task.title}\n\n"
            f"Breakdown steps:\n{steps_text}\n\n"
            "Create a complete project with all necessary files. "
            "Follow the breakdown steps as a guide for the implementation."
        )
    else:
        impl_detail = (
            f"Task: {task.title}\n\n"
            "Create a complete project with all necessary files."
        )

    print(f"Implementing task in: {project_dir}")
    try:
        # Step 1: If WorkIQ is available, query it first for grounding
        if use_workiq:
            print("Querying WorkIQ for context...")
            await session.send_and_wait(
                {
                    "prompt": (
                        f"I need to implement this task: {task.title}\n\n"
                        "Before writing any code, use the WorkIQ MCP tool (ask_work_iq) "
                        "to research the relevant SDKs, APIs, packages, project structure, "
                        "and any documentation or samples that exist for this topic. "
                        "The tool name is exposed as 'ask_work_iq'. You MUST call it now. "
                        "Do NOT use glob, grep, or other file search tools for this research step. "
                        "Summarize what you find."
                    )
                },
                timeout=180000,
            )

        # Step 2: Now implement based on context gathered
        response = await session.send_and_wait(
            {
                "prompt": (
                    "Based on the context gathered above, implement the following. "
                    "Use the information from WorkIQ to ensure you use the correct "
                    "package names, APIs, and project structure. "
                    "Do NOT fabricate URLs or package names — only use what was found "
                    "in the research step. "
                    "You must create ALL files in a single turn — do NOT stop after "
                    "planning or creating just one file. Keep going until every file "
                    "for the project is written.\n\n" + impl_detail
                )
            },
            timeout=180000,
        )

        # Step 3: Continue prompting if the agent stopped before creating files
        max_continuations = 5
        for i in range(max_continuations):
            created = [f for f in os.listdir(project_dir) if not f.startswith(".")]
            last_content = response.data.content if response and response.data else ""
            # Stop if files were created and content doesn't say "next I will"
            if created:
                break
            # Stop if the agent explicitly says it can't proceed
            if any(
                kw in last_content.lower()
                for kw in [
                    "cannot",
                    "unable",
                    "could not",
                    "please clarify",
                    "please provide",
                ]
            ):
                break
            print(f"Continuing implementation (step {i + 2})...")
            response = await session.send_and_wait(
                {
                    "prompt": (
                        "You have not created any project files yet. "
                        "Continue implementing — create all the project files now. "
                        "Do NOT just plan or describe what you will do. "
                        "Actually write and create the files."
                    )
                },
                timeout=180000,
            )
    except Exception as exc:
        errors.append(str(exc))
        response = None

    await session.destroy()
    await client.stop()

    content = response.data.content if response and response.data else ""

    # Heuristic: check whether any real file was created in the project dir
    created_files = [f for f in os.listdir(project_dir) if not f.startswith(".")]
    failure_keywords = ["fail", "unable", "could not", "cannot", "unavailable", "error"]
    content_signals_failure = any(kw in content.lower() for kw in failure_keywords)

    success = bool(created_files) and not errors
    if not created_files or (content_signals_failure and not created_files):
        success = False

    if not success and content:
        print(f"\nAgent response:\n{content}")
    if not success and errors:
        print("\nErrors encountered:", file=sys.stderr)
        for err in errors:
            print(f"  - {err}", file=sys.stderr)

    return project_dir, success


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
    if breakdown:
        max_level = getattr(args, "max_level", DEFAULT_MAX_LEVEL)
        children_ids: List[int] = []
        for step in breakdown:
            child_id = next_task_id(tasks)
            child = Task(
                id=child_id,
                title=step,
                status="open",
                created_at=timestamp,
                updated_at=timestamp,
                breakdown=[],
                level=task.level + 1,
                parent_id=task.id,
                atomic=task.level + 1 >= max_level,
            )
            tasks.append(child)
            children_ids.append(child_id)
        task.children_ids = children_ids
    save_tasks(args.storage, tasks)
    print(render_task(task))
    if args.implement:
        project_dir, success = asyncio.run(
            implement_task(
                task=task,
                model=args.model,
                use_workiq=not args.no_workiq,
                workiq_command=args.workiq_command,
                workiq_args=args.workiq_args,
                debug=args.debug,
            )
        )
        if success:
            print(f"Project created at: {project_dir}")
        else:
            print(
                "\nError: Implementation did not fully complete.",
                file=sys.stderr,
            )
            answer = input("Keep the task anyway? [y/N]: ").strip().lower()
            if answer not in ("y", "yes"):
                tasks.remove(task)
                save_tasks(args.storage, tasks)
                # Clean up the (possibly empty) project directory
                if os.path.isdir(project_dir):
                    shutil.rmtree(project_dir)
                print("Task removed.")
            else:
                print(f"Task kept. Partial output at: {project_dir}")


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
    max_level = getattr(args, "max_level", DEFAULT_MAX_LEVEL)
    if task.atomic:
        print(
            f"Task {task.id} is marked as atomic and cannot be broken down further.",
            file=sys.stderr,
        )
        sys.exit(1)
    if task.level >= max_level:
        print(
            f"Task {task.id} is at level {task.level} (max: {max_level}). "
            "Cannot break down further.",
            file=sys.stderr,
        )
        sys.exit(1)
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
    timestamp = now_iso()
    children_ids: List[int] = []
    for step in steps:
        child_id = next_task_id(tasks)
        child = Task(
            id=child_id,
            title=step,
            status="open",
            created_at=timestamp,
            updated_at=timestamp,
            breakdown=[],
            level=task.level + 1,
            parent_id=task.id,
            atomic=task.level + 1 >= max_level,
        )
        tasks.append(child)
        children_ids.append(child_id)
    task.children_ids = children_ids
    save_tasks(args.storage, tasks)
    print(render_task(task))
    if args.implement:
        project_dir, success = asyncio.run(
            implement_task(
                task=task,
                model=args.model,
                use_workiq=not args.no_workiq,
                workiq_command=args.workiq_command,
                workiq_args=args.workiq_args,
                debug=args.debug,
            )
        )
        if success:
            print(f"Project created at: {project_dir}")
        else:
            print(
                "\nError: Implementation did not fully complete.",
                file=sys.stderr,
            )
            answer = input("Keep the task anyway? [y/N]: ").strip().lower()
            if answer not in ("y", "yes"):
                # Revert the breakdown update
                task.breakdown = []
                task.updated_at = now_iso()
                save_tasks(args.storage, tasks)
                if os.path.isdir(project_dir):
                    shutil.rmtree(project_dir)
                print("Implementation output removed. Task breakdown cleared.")
            else:
                print(f"Task kept. Partial output at: {project_dir}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog=APP_NAME)
    parser.add_argument(
        "--storage",
        default=DEFAULT_STORAGE,
        help=f"Path to tasks file (default: {DEFAULT_STORAGE})",
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
    add_parser.add_argument(
        "--model",
        default=DEFAULT_MODEL,
        help=f"Copilot model (default: {DEFAULT_MODEL})",
    )
    add_parser.add_argument(
        "--implement",
        action="store_true",
        help="Ask Copilot to implement the task in a new project directory",
    )
    add_parser.add_argument(
        "--no-workiq",
        action="store_true",
        help="Disable WorkIQ MCP server usage",
    )
    add_parser.add_argument(
        "--workiq-command",
        default="npx",
        help="WorkIQ command (default: npx).",
    )
    add_parser.add_argument(
        "--workiq-args",
        nargs="*",
        default=["-y", "@microsoft/workiq", "mcp"],
        help="Args for WorkIQ MCP server (default: -y @microsoft/workiq mcp).",
    )
    add_parser.add_argument(
        "--max-level",
        type=int,
        default=DEFAULT_MAX_LEVEL,
        help=f"Maximum breakdown depth (default: {DEFAULT_MAX_LEVEL})",
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
    breakdown_parser.add_argument(
        "--model",
        default=DEFAULT_MODEL,
        help=f"Copilot model (default: {DEFAULT_MODEL})",
    )
    breakdown_parser.add_argument(
        "--implement",
        action="store_true",
        help="Ask Copilot to implement the task in a new project directory",
    )
    breakdown_parser.add_argument(
        "--no-workiq",
        action="store_true",
        help="Disable WorkIQ MCP server usage",
    )
    breakdown_parser.add_argument(
        "--workiq-command",
        default="npx",
        help="WorkIQ command (default: npx).",
    )
    breakdown_parser.add_argument(
        "--workiq-args",
        nargs="*",
        default=["-y", "@microsoft/workiq", "mcp"],
        help="Args for WorkIQ MCP server (default: -y @microsoft/workiq mcp).",
    )
    breakdown_parser.add_argument(
        "--max-level",
        type=int,
        default=DEFAULT_MAX_LEVEL,
        help=f"Maximum breakdown depth (default: {DEFAULT_MAX_LEVEL})",
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
