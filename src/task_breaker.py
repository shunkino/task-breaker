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

from copilot import CopilotClient, PermissionRequestResult
from copilot.generated.session_events import SessionEventType

APP_NAME = "task-breaker"
DEFAULT_MODEL = "gpt-4.1"
DEFAULT_STORAGE = os.path.expanduser("~/.task-breaker/tasks.json")
DEFAULT_USAGE_LOG = os.path.expanduser("~/.task-breaker/usage.log")
DEFAULT_EULA_PATH = os.path.expanduser("~/.task-breaker/workiq_eula.json")
DEFAULT_MAX_LEVEL = 3
DEFAULT_MAX_TASKS_PER_LEVEL = "5-L"
WORKIQ_EULA_URL = "https://github.com/microsoft/work-iq-mcp"
AI_CONTEXT_MARKER = "[AI context] "


def evaluate_max_tasks_formula(formula: str, level: int) -> Optional[int]:
    """
    Evaluate a formula for max tasks at a given level.

    Args:
        formula: Formula string, e.g., "5-L", "10", "auto", "3*L+2"
        level: Current task level

    Returns:
        Maximum number of tasks (None for "auto" mode, meaning let LLM decide)
    """
    if not formula or not isinstance(formula, str):
        return None

    formula = formula.strip()

    # Handle "auto" mode - let LLM decide
    if formula.lower() == "auto":
        return None

    # Try to parse as a simple integer
    try:
        return max(1, int(formula))
    except ValueError:
        pass

    # Formula contains 'L' - evaluate as expression
    # Replace L with the actual level value
    # Only allow safe mathematical operations
    safe_formula = formula.replace("L", str(level))

    # Validate that only safe characters are present
    if not re.match(r"^[0-9+\-*/() ]+$", safe_formula):
        # Invalid characters in formula, return None (auto mode)
        return None

    try:
        # Evaluate the mathematical expression
        result = eval(safe_formula, {"__builtins__": {}}, {})
        # Ensure at least 1 task
        return max(1, int(result))
    except (SyntaxError, ValueError, ZeroDivisionError, NameError):
        # If evaluation fails, return None (auto mode)
        return None


def is_workiq_eula_accepted(eula_path: str = DEFAULT_EULA_PATH) -> bool:
    """Check whether the WorkIQ EULA has been accepted locally."""
    if not os.path.exists(eula_path):
        return False
    try:
        with open(eula_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return bool(data.get("accepted"))
    except (json.JSONDecodeError, OSError):
        return False


def save_workiq_eula_acceptance(eula_path: str = DEFAULT_EULA_PATH) -> None:
    """Record that the user accepted the WorkIQ EULA."""
    os.makedirs(os.path.dirname(eula_path), exist_ok=True)
    data = {
        "accepted": True,
        "accepted_at": now_iso(),
        "eula_url": WORKIQ_EULA_URL,
    }
    with open(eula_path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)


def prompt_eula_acceptance(eula_path: str = DEFAULT_EULA_PATH) -> bool:
    """Interactively ask the user to accept the WorkIQ EULA. Returns True if accepted."""
    print("\n" + "=" * 60)
    print("WorkIQ End User License Agreement (EULA)")
    print("=" * 60)
    print(f"\nBefore using WorkIQ, you must accept the EULA.")
    print(f"Please review the EULA at:\n  {WORKIQ_EULA_URL}\n")
    answer = input("Do you accept the WorkIQ EULA? [y/N]: ").strip().lower()
    if answer in ("y", "yes"):
        return True
    return False


def ensure_workiq_eula(args: argparse.Namespace) -> bool:
    """Check EULA acceptance; prompt if needed. Returns True if accepted."""
    eula_path = getattr(args, "eula_path", DEFAULT_EULA_PATH)
    if is_workiq_eula_accepted(eula_path):
        return True
    if not prompt_eula_acceptance(eula_path):
        print(
            "\nWorkIQ EULA not accepted. WorkIQ features are disabled."
            "\nYou can accept later with: task-breaker workiq-eula",
            file=sys.stderr,
        )
        return False
    # User accepted in CLI — now call accept_eula on the MCP server
    print("Registering EULA acceptance with WorkIQ...")
    success = asyncio.run(
        accept_workiq_eula_via_mcp(
            workiq_command=getattr(args, "workiq_command", "npx"),
            workiq_args=getattr(
                args, "workiq_args", ["-y", "@microsoft/workiq", "mcp"]
            ),
            model=getattr(args, "model", DEFAULT_MODEL),
            eula_path=eula_path,
            debug=getattr(args, "debug", False),
        )
    )
    if success:
        print("WorkIQ EULA accepted successfully.")
    else:
        # Still save locally — the MCP call is best-effort
        save_workiq_eula_acceptance(eula_path)
        print("EULA acceptance recorded locally.")
    return True


async def accept_workiq_eula_via_mcp(
    workiq_command: str = "npx",
    workiq_args: Optional[List[str]] = None,
    model: str = DEFAULT_MODEL,
    eula_path: str = DEFAULT_EULA_PATH,
    debug: bool = False,
) -> bool:
    """Start the WorkIQ MCP server and call the accept_eula tool."""
    client_opts: Dict[str, Any] = {}
    if debug:
        client_opts["log_level"] = "debug"
    cli_path = resolve_copilot_cli_path(debug=debug)
    if cli_path:
        client_opts["cli_path"] = cli_path

    client = CopilotClient(client_opts)
    await client.start()

    _workiq_args = workiq_args or ["-y", "@microsoft/workiq", "mcp"]
    mcp_command = workiq_command
    mcp_args = [token for arg in _workiq_args for token in arg.split()]
    if sys.platform == "win32":
        mcp_args = ["/c", mcp_command] + mcp_args
        mcp_command = "cmd"

    def _approve_eula_permission(request, context) -> PermissionRequestResult:
        """Auto-approve the accept_eula tool call (user already confirmed)."""
        return PermissionRequestResult(kind="approved")

    session_config: Dict[str, Any] = {
        "model": model,
        "on_permission_request": _approve_eula_permission,
        "system_message": {
            "content": (
                "You are a helper that accepts the WorkIQ EULA. "
                "Call the accept_eula tool immediately. Do not do anything else."
            )
        },
        "mcp_servers": {
            "workiq": {
                "type": "local",
                "command": mcp_command,
                "args": mcp_args,
                "tools": ["accept_eula"],
                "timeout": 60000,
            }
        },
    }

    session = await client.create_session(session_config)
    success = False

    def _track_tool_call(event: Any) -> None:
        nonlocal success
        data = event.data
        mcp_tool = getattr(data, "mcp_tool_name", None)
        if mcp_tool == "accept_eula":
            success = True

    session.on(_track_tool_call)

    try:
        await session.send_and_wait(
            {
                "prompt": (
                    "The user has reviewed and accepted the WorkIQ EULA at "
                    f"{WORKIQ_EULA_URL}. "
                    "Call the accept_eula tool now to record their acceptance."
                )
            },
            timeout=60000,
        )
    except Exception as exc:
        if debug:
            print(f"[DEBUG] accept_eula error: {exc}", file=sys.stderr)
    finally:
        await session.disconnect()
        await client.stop()

    if success:
        save_workiq_eula_acceptance(eula_path)

    return success


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
    due_date: Optional[str] = None
    daily_focus: bool = False


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


def create_child_tasks(
    tasks: List[Task],
    parent: Task,
    steps: List[str],
    max_level: int,
    timestamp: str,
) -> List[int]:
    """Create child Task objects from breakdown steps and return their IDs."""
    children_ids: List[int] = []
    child_level = parent.level + 1
    for step in steps:
        child_id = next_task_id(tasks)
        child = Task(
            id=child_id,
            title=step,
            status="open",
            created_at=timestamp,
            updated_at=timestamp,
            breakdown=[],
            level=child_level,
            parent_id=parent.id,
            atomic=child_level >= max_level,
        )
        tasks.append(child)
        children_ids.append(child_id)
    parent.children_ids = children_ids
    return children_ids


def render_task(task: Task) -> str:
    lines = [f"[{task.id}] {task.title}", f"  status: {task.status}"]
    if task.daily_focus:
        lines.append("  daily focus: ⭐")
    if task.atomic:
        lines.append("  atomic: yes")
    if task.level > 0:
        lines.append(f"  level: {task.level}")
    if task.due_date:
        lines.append(f"  due: {task.due_date}")
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


def _build_tree_index(tasks: List[Task]) -> Dict[Optional[int], List[Task]]:
    """Build a mapping from parent_id to list of child tasks."""
    index: Dict[Optional[int], List[Task]] = {}
    for task in tasks:
        index.setdefault(task.parent_id, []).append(task)
    return index


def _render_tree_node(
    task: Task,
    index: Dict[Optional[int], List[Task]],
    prefix: str = "",
    is_last: bool = True,
) -> List[str]:
    """Recursively render a single tree node and its children."""
    connector = "└── " if is_last else "├── "
    status_icon = "✓" if task.status == "done" else "○"
    atomic_str = " 🔒" if task.atomic else ""
    line = f"{prefix}{connector}[{task.id}] {status_icon} {task.title}{atomic_str}"
    lines = [line]

    children = index.get(task.id, [])
    child_prefix = prefix + ("    " if is_last else "│   ")
    for i, child in enumerate(children):
        lines.extend(
            _render_tree_node(child, index, child_prefix, i == len(children) - 1)
        )
    return lines


def render_tree(tasks: List[Task]) -> str:
    """Render all tasks as a hierarchical tree string."""
    if not tasks:
        return "No tasks yet."
    index = _build_tree_index(tasks)
    task_ids = {t.id for t in tasks}
    # Roots are tasks whose parent_id is None or whose parent is not in the set
    roots = [t for t in tasks if t.parent_id is None or t.parent_id not in task_ids]
    if not roots:
        return "No root tasks found."
    lines: List[str] = ["Task Hierarchy"]
    for i, root in enumerate(roots):
        lines.extend(_render_tree_node(root, index, "", i == len(roots) - 1))
    return "\n".join(lines)


def get_subtree(tasks: List[Task], task_id: int) -> List[Task]:
    """Return a task and all its descendants."""
    index = _build_tree_index(tasks)
    result: List[Task] = []

    def _collect(tid: int) -> None:
        for task in tasks:
            if task.id == tid:
                result.append(task)
                break
        for child in index.get(tid, []):
            _collect(child.id)

    _collect(task_id)
    return result


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
    max_tasks: Optional[int] = None,
) -> Tuple[List[str], Optional[str]]:
    """Break down a task into steps and optionally return AI-generated context.

    Returns:
        A tuple of (steps, context) where context is a brief summary from
        WorkIQ or None when WorkIQ is not used.
    """
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
        "on_permission_request": lambda request, context: PermissionRequestResult(kind="approved"),
        "system_message": {
            "content": (
                "You are a task manager that breaks down tasks."
                " Follow these rules:"
                " (a) If the task is vague, break it into smaller steps."
                " (b) If it is unclear how to achieve the goal, investigate context using WorkIQ."
                " (c) If the task is to implement something or a small dashboard would help,"
                " propose a small implementation project as steps."
                + (
                    f" (d) Create AT MOST {max_tasks} tasks in your breakdown."
                    f" Prioritize the most important {max_tasks} steps."
                    if max_tasks is not None and max_tasks > 0
                    else ""
                )
                + " Return the breakdown as a JSON array of short step strings."
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

    # Request a brief context summary for the task (best-effort).
    # NOTE: We intentionally gather context here rather than calling
    # get_workiq_context() because this session already holds the WorkIQ
    # conversation history — reusing it produces a more accurate summary
    # without the overhead of starting a new Copilot session.
    context = None
    if use_workiq:
        try:
            context_response = await session.send_and_wait(
                {
                    "prompt": (
                        "Now provide a brief context summary (1-4 sentences) based on what "
                        "you learned from WorkIQ about this task. This will be saved as a "
                        "reference note. Return ONLY plain text, no JSON or formatting."
                    )
                }
            )
            if context_response and context_response.data:
                ctx = context_response.data.content.strip()
                if ctx:
                    context = ctx
        except Exception:
            pass  # Context gathering is best-effort

    await session.disconnect()
    await client.stop()

    content = response.data.content if response and response.data else "[]"
    try:
        steps = json.loads(content)
    except json.JSONDecodeError:
        steps = [content.strip()]
    if not isinstance(steps, list):
        steps = [str(steps)]

    # Filter and clean steps
    steps = [str(step).strip() for step in steps if str(step).strip()]

    # Apply max_tasks limit if specified (safety truncation)
    if max_tasks is not None and max_tasks > 0 and len(steps) > max_tasks:
        steps = steps[:max_tasks]

    return steps, context


async def get_workiq_context(
    title: str,
    model: str,
    workiq_command: str,
    workiq_args: List[str],
    debug: bool = False,
) -> Optional[str]:
    """Get a brief AI-generated context summary for a task via WorkIQ.

    Returns a short (1-4 sentence) context summary or None on failure.
    """
    try:
        client_opts: Dict[str, Any] = {}
        if debug:
            client_opts["log_level"] = "debug"
        cli_path = resolve_copilot_cli_path(debug=debug)
        if cli_path:
            client_opts["cli_path"] = cli_path
        client = CopilotClient(client_opts)
        await client.start()

        mcp_command = workiq_command
        mcp_args = [token for arg in workiq_args for token in arg.split()]
        if sys.platform == "win32":
            mcp_args = ["/c", mcp_command] + mcp_args
            mcp_command = "cmd"

        session_config: Dict[str, Any] = {
            "model": model,
            "on_permission_request": lambda request, context: PermissionRequestResult(kind="approved"),
            "system_message": {
                "content": (
                    "You are a helpful assistant that provides brief context for tasks. "
                    "When asked about a task, use WorkIQ to gather relevant context and "
                    "provide a concise summary in 1-4 sentences."
                )
            },
            "mcp_servers": {
                "workiq": {
                    "type": "local",
                    "command": mcp_command,
                    "args": mcp_args,
                    "tools": ["*"],
                    "timeout": 180000,
                }
            },
        }

        session = await client.create_session(session_config)

        await session.send_and_wait(
            {
                "prompt": (
                    f"I have a new task: {title}\n\n"
                    "Use WorkIQ MCP tool (ask_work_iq) to gather any relevant context "
                    "about this task - related work items, documentation, or prior discussions."
                )
            },
            timeout=180000,
        )

        response = await session.send_and_wait(
            {
                "prompt": (
                    "Based on the context gathered, provide a brief summary of relevant "
                    "background context for this task in 1-4 sentences. "
                    "Return ONLY the plain text summary, no JSON or formatting."
                )
            }
        )

        await session.disconnect()
        await client.stop()

        if response and response.data:
            ctx = response.data.content.strip()
            if ctx:
                return ctx
        return None
    except Exception:
        return None


def _make_permission_handler(project_dir: str):
    """Create a permission handler that auto-approves read/write in the project dir."""
    norm_project = os.path.normcase(os.path.abspath(project_dir))

    def _handler(request, context) -> PermissionRequestResult:
        kind = getattr(request, "kind", "unknown")
        kind_str = kind.value if hasattr(kind, "value") else str(kind)
        path = getattr(request, "path", "") or ""

        # Auto-approve read/write operations inside the project directory
        if kind_str in ("read", "write") and path:
            norm_path = os.path.normcase(os.path.abspath(path))
            if norm_path.startswith(norm_project):
                return PermissionRequestResult(kind="approved")

        # Everything else: ask the user
        details: List[str] = []
        import dataclasses as _dc
        for f in _dc.fields(request):
            if f.name in ("kind", "tool_call_id"):
                continue
            value = getattr(request, f.name, None)
            if value is not None:
                details.append(f"  {f.name}: {value}")
        print(f"\n[Permission requested] {kind_str}")
        if details:
            print("\n".join(details))
        answer = input("Allow this action? [y/N]: ").strip().lower()
        if answer in ("y", "yes"):
            return PermissionRequestResult(kind="approved")
        return PermissionRequestResult(kind="denied-interactively-by-user")

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

    await session.disconnect()
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


def cmd_workiq_eula(args: argparse.Namespace) -> None:
    """Show EULA status or accept the WorkIQ EULA."""
    eula_path = getattr(args, "eula_path", DEFAULT_EULA_PATH)
    if is_workiq_eula_accepted(eula_path):
        print(f"WorkIQ EULA already accepted.")
        print(f"EULA: {WORKIQ_EULA_URL}")
        return
    if not prompt_eula_acceptance(eula_path):
        print("EULA not accepted.", file=sys.stderr)
        sys.exit(1)
    print("Registering EULA acceptance with WorkIQ...")
    success = asyncio.run(
        accept_workiq_eula_via_mcp(
            workiq_command=getattr(args, "workiq_command", "npx"),
            workiq_args=getattr(
                args, "workiq_args", ["-y", "@microsoft/workiq", "mcp"]
            ),
            model=getattr(args, "model", DEFAULT_MODEL),
            eula_path=eula_path,
            debug=getattr(args, "debug", False),
        )
    )
    if success:
        print("WorkIQ EULA accepted successfully.")
    else:
        save_workiq_eula_acceptance(eula_path)
        print("EULA acceptance recorded locally.")


def cmd_add(args: argparse.Namespace) -> None:
    tasks = load_tasks(args.storage)
    task_id = next_task_id(tasks)
    timestamp = now_iso()
    # Gate WorkIQ behind EULA acceptance
    use_workiq = not args.no_workiq
    if use_workiq and not ensure_workiq_eula(args):
        use_workiq = False
    args.usage_logger.emit(
        "command",
        {
            "name": "add",
            "breakdown": args.breakdown,
            "model": args.model if (args.breakdown or use_workiq) else None,
            "workiq_enabled": use_workiq,
        },
    )
    breakdown: List[str] = []
    context: Optional[str] = None
    if args.breakdown:
        # Evaluate max_tasks formula for level 0 (new root task)
        max_tasks = evaluate_max_tasks_formula(args.max_tasks_per_level, 0)
        breakdown, context = asyncio.run(
            breakdown_task(
                title=args.title,
                model=args.model,
                use_workiq=use_workiq,
                workiq_command=args.workiq_command,
                workiq_args=args.workiq_args,
                usage_logger=args.usage_logger,
                source_command="add",
                debug=args.debug,
                max_tasks=max_tasks,
            )
        )
    elif use_workiq:
        # No breakdown requested, but WorkIQ is available — gather context
        context = asyncio.run(
            get_workiq_context(
                title=args.title,
                model=args.model,
                workiq_command=args.workiq_command,
                workiq_args=args.workiq_args,
                debug=args.debug,
            )
        )
    # Build AI context note
    notes: Optional[str] = None
    if context:
        notes = f"{AI_CONTEXT_MARKER}{context}"
    task = Task(
        id=task_id,
        title=args.title,
        status="open",
        created_at=timestamp,
        updated_at=timestamp,
        breakdown=breakdown,
        due_date=args.due,
        notes=notes,
    )
    tasks.append(task)
    if breakdown:
        create_child_tasks(tasks, task, breakdown, args.max_level, timestamp)
    save_tasks(args.storage, tasks)
    print(render_task(task))
    if args.implement:
        project_dir, success = asyncio.run(
            implement_task(
                task=task,
                model=args.model,
                use_workiq=use_workiq,
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


_SORT_KEY_FNS = {
    "id": lambda t: t.id,
    "due_date": lambda t: (t.due_date or ""),
    "level": lambda t: t.level,
    "status": lambda t: t.status,
    "created_at": lambda t: t.created_at,
    "updated_at": lambda t: t.updated_at,
    "title": lambda t: t.title.lower(),
}


def cmd_list(args: argparse.Namespace) -> None:
    tasks = load_tasks(args.storage)
    if args.status:
        tasks = [task for task in tasks if task.status == args.status]
    sort_field = getattr(args, "sort", None) or "created_at"
    sort_order = getattr(args, "order", "desc")
    key_fn = _SORT_KEY_FNS.get(sort_field, lambda t: t.created_at)
    tasks = sorted(tasks, key=key_fn, reverse=(sort_order == "desc"))
    args.usage_logger.emit("command", {"name": "list", "status": args.status})
    print(render_tasks(tasks))


def cmd_tree(args: argparse.Namespace) -> None:
    tasks = load_tasks(args.storage)
    args.usage_logger.emit(
        "command", {"name": "tree", "task_id": getattr(args, "id", None)}
    )
    if hasattr(args, "id") and args.id is not None:
        subtree = get_subtree(tasks, args.id)
        print(render_tree(subtree))
    else:
        print(render_tree(tasks))


def cmd_show(args: argparse.Namespace) -> None:
    tasks = load_tasks(args.storage)
    task = find_task(tasks, args.id)
    args.usage_logger.emit("command", {"name": "show", "task_id": args.id})
    print(render_task(task))


def _set_descendants_status(tasks: List[Task], parent: Task, status: str) -> None:
    """Recursively set status on all descendants of *parent*."""
    timestamp = now_iso()
    for cid in parent.children_ids or []:
        child = next((t for t in tasks if t.id == cid), None)
        if child:
            child.status = status
            child.updated_at = timestamp
            _set_descendants_status(tasks, child, status)


def cmd_complete(args: argparse.Namespace) -> None:
    tasks = load_tasks(args.storage)
    task = find_task(tasks, args.id)
    task.status = "done"
    task.updated_at = now_iso()
    if getattr(args, "include_children", False):
        _set_descendants_status(tasks, task, "done")
    save_tasks(args.storage, tasks)
    args.usage_logger.emit("command", {"name": "complete", "task_id": args.id})
    print(render_task(task))


def cmd_archive(args: argparse.Namespace) -> None:
    tasks = load_tasks(args.storage)
    task = find_task(tasks, args.id)
    task.status = "archived"
    task.updated_at = now_iso()
    if getattr(args, "include_children", False):
        _set_descendants_status(tasks, task, "archived")
    save_tasks(args.storage, tasks)
    args.usage_logger.emit("command", {"name": "archive", "task_id": args.id})
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


def cmd_due(args: argparse.Namespace) -> None:
    tasks = load_tasks(args.storage)
    task = find_task(tasks, args.id)
    # Treat empty or whitespace-only input as "clear due date"
    if args.date is None or args.date.strip() == "":
        new_due_date = None
    else:
        new_due_date = args.date
    task.due_date = new_due_date
    task.updated_at = now_iso()
    save_tasks(args.storage, tasks)
    args.usage_logger.emit(
        "command", {"name": "due", "task_id": args.id, "date": new_due_date}
    )
    print(render_task(task))


def cmd_focus(args: argparse.Namespace) -> None:
    tasks = load_tasks(args.storage)
    task = find_task(tasks, args.id)
    task.daily_focus = not task.daily_focus
    task.updated_at = now_iso()
    save_tasks(args.storage, tasks)
    state = "added to" if task.daily_focus else "removed from"
    args.usage_logger.emit(
        "command", {"name": "focus", "task_id": args.id, "focus": task.daily_focus}
    )
    print(f"Task {task.id} {state} daily focus.")
    print(render_task(task))


def cmd_focus_list(args: argparse.Namespace) -> None:
    tasks = load_tasks(args.storage)
    focus_tasks = [t for t in tasks if t.daily_focus]
    args.usage_logger.emit("command", {"name": "focus-list"})
    if not focus_tasks:
        print("No daily focus tasks.")
        return
    print("Daily Focus Tasks:")
    print(render_tasks(focus_tasks))


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
    # Gate WorkIQ behind EULA acceptance
    use_workiq = not args.no_workiq
    if use_workiq and not ensure_workiq_eula(args):
        use_workiq = False
    args.usage_logger.emit(
        "command",
        {
            "name": "breakdown",
            "task_id": args.id,
            "model": args.model,
            "workiq_enabled": use_workiq,
        },
    )
    # Evaluate max_tasks formula for this task's level
    max_tasks = evaluate_max_tasks_formula(args.max_tasks_per_level, task.level)
    steps, context = asyncio.run(
        breakdown_task(
            title=task.title,
            model=args.model,
            use_workiq=use_workiq,
            workiq_command=args.workiq_command,
            workiq_args=args.workiq_args,
            usage_logger=args.usage_logger,
            source_command="breakdown",
            debug=args.debug,
            max_tasks=max_tasks,
        )
    )
    task.breakdown = steps
    timestamp = now_iso()
    task.updated_at = timestamp
    # Preserve AI context from breakdown as a note (skip if one already exists)
    if context and AI_CONTEXT_MARKER not in (task.notes or ""):
        ai_note = f"{AI_CONTEXT_MARKER}{context}"
        if task.notes:
            task.notes = f"{task.notes}\n\n{ai_note}"
        else:
            task.notes = ai_note
    create_child_tasks(tasks, task, steps, args.max_level, timestamp)
    save_tasks(args.storage, tasks)
    print(render_task(task))
    if args.implement:
        project_dir, success = asyncio.run(
            implement_task(
                task=task,
                model=args.model,
                use_workiq=use_workiq,
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
        "--due",
        default=None,
        metavar="YYYY-MM-DD",
        help="Due date in YYYY-MM-DD format",
    )
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
    add_parser.add_argument(
        "--max-tasks-per-level",
        type=str,
        default=DEFAULT_MAX_TASKS_PER_LEVEL,
        help=f"Max tasks per level (formula like '5-L' where L is level, or 'auto' for LLM-decided, default: {DEFAULT_MAX_TASKS_PER_LEVEL})",
    )
    add_parser.set_defaults(func=cmd_add)

    list_parser = subparsers.add_parser("list", help="List tasks")
    list_parser.add_argument(
        "--status", choices=["open", "done"], help="Filter by status"
    )
    list_parser.add_argument(
        "--sort",
        choices=[
            "id",
            "due_date",
            "level",
            "status",
            "created_at",
            "updated_at",
            "title",
        ],
        default="created_at",
        help="Sort field (default: created_at)",
    )
    list_parser.add_argument(
        "--order",
        choices=["asc", "desc"],
        default="desc",
        help="Sort direction (default: desc)",
    )
    list_parser.set_defaults(func=cmd_list)

    tree_parser = subparsers.add_parser("tree", help="Show task hierarchy as a tree")
    tree_parser.add_argument(
        "id", type=int, nargs="?", default=None, help="Optional task id to show subtree"
    )
    tree_parser.set_defaults(func=cmd_tree)

    show_parser = subparsers.add_parser("show", help="Show task detail")
    show_parser.add_argument("id", type=int, help="Task id")
    show_parser.set_defaults(func=cmd_show)

    complete_parser = subparsers.add_parser("complete", help="Mark task as done")
    complete_parser.add_argument("id", type=int, help="Task id")
    complete_parser.add_argument(
        "--include-children",
        action="store_true",
        help="Also mark all child tasks as done",
    )
    complete_parser.set_defaults(func=cmd_complete)

    archive_parser = subparsers.add_parser(
        "archive", help="Archive a task that is no longer relevant"
    )
    archive_parser.add_argument("id", type=int, help="Task id")
    archive_parser.add_argument(
        "--include-children",
        action="store_true",
        help="Also archive all child tasks",
    )
    archive_parser.set_defaults(func=cmd_archive)

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
    breakdown_parser.add_argument(
        "--max-tasks-per-level",
        type=str,
        default=DEFAULT_MAX_TASKS_PER_LEVEL,
        help=f"Max tasks per level (formula like '5-L' where L is level, or 'auto' for LLM-decided, default: {DEFAULT_MAX_TASKS_PER_LEVEL})",
    )
    breakdown_parser.set_defaults(func=cmd_breakdown)

    due_parser = subparsers.add_parser(
        "due", help="Set or update the due date of a task"
    )
    due_parser.add_argument("id", type=int, help="Task id")
    due_parser.add_argument(
        "date", metavar="YYYY-MM-DD", help="Due date (use '' to clear)"
    )
    due_parser.set_defaults(func=cmd_due)

    focus_parser = subparsers.add_parser("focus", help="Toggle daily focus for a task")
    focus_parser.add_argument("id", type=int, help="Task id")
    focus_parser.set_defaults(func=cmd_focus)

    focus_list_parser = subparsers.add_parser(
        "focus-list", help="List daily focus tasks"
    )
    focus_list_parser.set_defaults(func=cmd_focus_list)

    eula_parser = subparsers.add_parser(
        "workiq-eula", help="Accept or check WorkIQ EULA status"
    )
    eula_parser.add_argument(
        "--debug",
        action="store_true",
        help="Enable debug logging",
    )
    eula_parser.add_argument(
        "--model",
        default=DEFAULT_MODEL,
        help=f"Copilot model (default: {DEFAULT_MODEL})",
    )
    eula_parser.add_argument(
        "--workiq-command",
        default="npx",
        help="WorkIQ command (default: npx).",
    )
    eula_parser.add_argument(
        "--workiq-args",
        nargs="*",
        default=["-y", "@microsoft/workiq", "mcp"],
        help="Args for WorkIQ MCP server.",
    )
    eula_parser.set_defaults(func=cmd_workiq_eula)

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
