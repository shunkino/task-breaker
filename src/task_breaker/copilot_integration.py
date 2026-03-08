"""Extract of the Copilot/WorkIQ integration from task_breaker.py.

Functions here are standalone async callables used by services.
"""

import json
import logging
import os
import re
import shutil
import sys
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from copilot import CopilotClient, PermissionRequestResult
from copilot.generated.session_events import SessionEventType

_logger = logging.getLogger(__name__)

WORKIQ_EULA_URL = "https://github.com/microsoft/work-iq-mcp"
AI_CONTEXT_MARKER = "[AI context] "


# ---------------------------------------------------------------------------
# Web-based WorkIQ permission approval store
# ---------------------------------------------------------------------------

_pending_permissions_lock = threading.Lock()
_pending_permissions: Dict[str, dict] = {}


def get_pending_permissions() -> List[dict]:
    """Return a snapshot of all pending permission requests for the web UI."""
    with _pending_permissions_lock:
        return [
            {
                "id": pid,
                "kind": info["kind"],
                "server": info.get("server"),
                "tool": info.get("tool"),
                "args": info.get("args"),
                "task_id": info.get("task_id"),
            }
            for pid, info in _pending_permissions.items()
        ]


def resolve_permission(permission_id: str, approved: bool) -> bool:
    """Approve or deny a pending permission request. Returns True if found."""
    with _pending_permissions_lock:
        info = _pending_permissions.get(permission_id)
        if not info:
            return False
        info["decision"] = "approved" if approved else "denied"
        info["event"].set()
        return True


def _default_eula_path() -> Path:
    return Path.home() / ".task-breaker" / "workiq_eula.json"


def is_workiq_eula_accepted(eula_path: Optional[Path] = None) -> bool:
    """Check whether the WorkIQ EULA has been accepted locally."""
    path = eula_path or _default_eula_path()
    if not path.exists():
        return False
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return bool(data.get("accepted"))
    except (json.JSONDecodeError, OSError):
        return False


def save_workiq_eula_acceptance(eula_path: Optional[Path] = None) -> None:
    """Record that the user accepted the WorkIQ EULA."""
    path = eula_path or _default_eula_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    data = {
        "accepted": True,
        "accepted_at": datetime.now(timezone.utc).isoformat(),
        "eula_url": WORKIQ_EULA_URL,
    }
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")


async def accept_workiq_eula_via_mcp(
    workiq_command: str = "npx",
    workiq_args: Optional[List[str]] = None,
    model: str = "gpt-4.1",
    eula_path: Optional[Path] = None,
    debug: bool = False,
) -> bool:
    """Start the WorkIQ MCP server and call the accept_eula tool.

    Returns True if the acceptance succeeded.
    """
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


def resolve_copilot_cli_path(debug: bool = False) -> Optional[str]:
    """On Windows, resolve the full path to the copilot CLI."""
    if sys.platform != "win32":
        if debug:
            print(
                "[DEBUG] resolve_copilot_cli_path: not Windows, skipping",
                file=sys.stderr,
            )
        return None
    env_path = os.environ.get("COPILOT_CLI_PATH")
    if env_path:
        if debug:
            print(
                f"[DEBUG] resolve_copilot_cli_path: COPILOT_CLI_PATH is set to '{env_path}'",
                file=sys.stderr,
            )
        if os.path.isfile(env_path):
            return env_path
        if debug:
            print(
                "[DEBUG] resolve_copilot_cli_path: COPILOT_CLI_PATH does not point to a file, falling back to auto-detection",
                file=sys.stderr,
            )
        return None
    found = shutil.which("copilot")
    if debug:
        print(
            f"[DEBUG] resolve_copilot_cli_path: shutil.which('copilot') = '{found}'",
            file=sys.stderr,
        )
    if found:
        if debug:
            print(
                f"[DEBUG] resolve_copilot_cli_path: using resolved path '{found}'",
                file=sys.stderr,
            )
        return found
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
    usage_logger: Any = None,
    source_command: Optional[str] = None,
    debug: bool = False,
    max_tasks: Optional[int] = None,
    auto_approve: bool = False,
    task_id: Optional[int] = None,
) -> Tuple[List[str], Optional[str], Dict[str, str]]:
    """Break down a task into steps and optionally return AI-generated context.

    Returns:
        A tuple of (steps, context, step_contexts) where context is a brief
        summary from WorkIQ (or None), and step_contexts maps each step
        title to its relevant context snippet.
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
        usage_logger.emit("copilot", {"model": model, "source_command": source_command})
        if use_workiq:
            usage_logger.emit(
                "workiq",
                {
                    "command": workiq_command,
                    "args": workiq_args,
                    "source_command": source_command,
                },
            )

    # Build system message with optional max_tasks constraint
    system_message_content = (
        "You are a task manager that breaks down tasks."
        " Follow these rules:"
        " (a) If the task is vague, break it into smaller steps."
        " (b) If it is unclear how to achieve the goal, investigate context using WorkIQ."
        " (c) If the task is to implement something or a small dashboard would help,"
        " propose a small implementation project as steps."
    )

    if max_tasks is not None and max_tasks > 0:
        system_message_content += (
            f" (d) Create AT MOST {max_tasks} tasks in your breakdown."
            f" Prioritize the most important {max_tasks} steps."
        )

    system_message_content += (
        " Return the breakdown as a JSON array of short step strings."
        " If you used WorkIQ, mention that in a final step like 'Review WorkIQ findings'."
    )

    session_config: Dict[str, Any] = {
        "model": model,
        "system_message": {"content": system_message_content},
    }

    if use_workiq:
        mcp_command = workiq_command
        mcp_args = [token for arg in workiq_args for token in arg.split()]
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

        def _workiq_permission_handler(request: dict, context: dict) -> dict:
            """
            Handle WorkIQ MCP permission requests.

            Approval modes (checked in order):
            1. auto_approve parameter (web/yolo mode)
            2. TASK_BREAKER_AUTO_APPROVE_WORKIQ=1 environment variable
            3. Interactive terminal prompt (CLI mode)
            4. Web-based approval via pending permissions store
            5. Deny by default
            """
            kind = getattr(request, "kind", "unknown")
            kind_str = kind.value if hasattr(kind, "value") else str(kind)
            if debug:
                print(
                    f"[DEBUG] permission_request: kind={kind_str!r} request={request}",
                    file=sys.stderr,
                )

            # Auto-approve via function parameter (web-triggered or yolo mode).
            if auto_approve:
                if debug:
                    print(
                        "[DEBUG] permission_request: auto-approved via auto_approve param",
                        file=sys.stderr,
                    )
                return PermissionRequestResult(kind="approved")

            # Explicit opt-in to auto-approve via environment variable.
            if os.environ.get("TASK_BREAKER_AUTO_APPROVE_WORKIQ") == "1":
                if debug:
                    print(
                        "[DEBUG] permission_request: auto-approved via "
                        "TASK_BREAKER_AUTO_APPROVE_WORKIQ",
                        file=sys.stderr,
                    )
                return PermissionRequestResult(kind="approved")

            # If interactive, prompt the user for a decision.
            if sys.stdin is not None and sys.stdin.isatty():
                tool_name = getattr(request, "tool_name", None)
                server_name = getattr(request, "server_name", None)
                summary_parts = [f"kind={kind_str!r}"]
                if server_name:
                    summary_parts.append(f"server={server_name!r}")
                if tool_name:
                    summary_parts.append(f"tool={tool_name!r}")
                summary = ", ".join(summary_parts)

                print(
                    f"[WorkIQ] Permission request: {summary}",
                    file=sys.stderr,
                )
                print(
                    "[WorkIQ] Approve this request? [y/N]: ",
                    end="",
                    file=sys.stderr,
                )
                try:
                    answer = input().strip().lower()
                except (EOFError, KeyboardInterrupt):
                    answer = ""

                if answer.startswith("y"):
                    decision = PermissionRequestResult(kind="approved")
                else:
                    decision = PermissionRequestResult(kind="denied-interactively-by-user")

                if debug:
                    print(
                        f"[DEBUG] permission_request: user_decision={decision.kind}",
                        file=sys.stderr,
                    )
                return decision

            # Non-interactive environment: use web-based approval if possible.
            tool_name = getattr(request, "tool_name", None)
            server_name = getattr(request, "server_name", None)
            perm_id = str(uuid.uuid4())
            event = threading.Event()
            perm_entry = {
                "kind": kind_str,
                "server": server_name,
                "tool": tool_name,
                "args": getattr(request, "args", None),
                "task_id": task_id,
                "event": event,
                "decision": None,
            }
            with _pending_permissions_lock:
                _pending_permissions[perm_id] = perm_entry

            _logger.info(
                "WorkIQ permission request queued for web approval: id=%s kind=%s tool=%s",
                perm_id,
                kind_str,
                tool_name,
            )
            if debug:
                print(
                    f"[DEBUG] permission_request: queued for web approval id={perm_id}",
                    file=sys.stderr,
                )

            # Wait up to 5 minutes for the web user to respond
            approved = event.wait(timeout=300)

            with _pending_permissions_lock:
                decision_str = perm_entry.get("decision", "denied")
                _pending_permissions.pop(perm_id, None)

            if not approved or decision_str != "approved":
                decision = PermissionRequestResult(kind="denied-interactively-by-user")
            else:
                decision = PermissionRequestResult(kind="approved")

            if debug:
                print(
                    f"[DEBUG] permission_request: web_decision={decision.kind}",
                    file=sys.stderr,
                )
            return decision

        session_config["on_permission_request"] = _workiq_permission_handler

    if debug:
        _loggable = {k: v for k, v in session_config.items() if not callable(v)}
        print(
            f"[DEBUG] session_config = {json.dumps(_loggable, indent=2)}",
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

    response = await session.send_and_wait(
        {
            "prompt": (
                "Based on any context gathered, break down this task following the rules above. "
                "Return ONLY a JSON array of steps. Task: "
                f"{title}"
            )
        }
    )

    # Request context summaries for the parent task and each sub-step.
    # NOTE: We intentionally gather context here rather than calling
    # get_workiq_context() because this session already holds the WorkIQ
    # conversation history — reusing it produces a more accurate summary
    # without the overhead of starting a new Copilot session.
    context = None
    step_contexts: Dict[str, str] = {}
    if use_workiq:
        try:
            context_response = await session.send_and_wait(
                {
                    "prompt": (
                        "Now provide context based on what you learned from WorkIQ. "
                        "Return a JSON object with two keys:\n"
                        '  "summary": a brief overall context summary (1-4 sentences) for the parent task,\n'
                        '  "steps": an object mapping each step title (exactly as it appears in the '
                        "breakdown array) to a 1-2 sentence context note relevant to that specific step.\n"
                        "Return ONLY the JSON object, no markdown fences or extra text."
                    )
                }
            )
            if context_response and context_response.data:
                raw = context_response.data.content.strip()
                # Try to parse as JSON with summary + per-step contexts
                try:
                    parsed = json.loads(raw)
                    if isinstance(parsed, dict):
                        summary = parsed.get("summary", "").strip()
                        if summary:
                            context = summary
                        steps_ctx = parsed.get("steps")
                        if isinstance(steps_ctx, dict):
                            step_contexts = {
                                str(k).strip(): str(v).strip()
                                for k, v in steps_ctx.items()
                                if str(v).strip()
                            }
                except (json.JSONDecodeError, TypeError, ValueError):
                    # Fall back: treat the whole response as a plain-text summary
                    if raw:
                        context = raw
        except Exception:
            _logger.debug("breakdown_task: context gathering failed", exc_info=True)

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

    _logger.debug(
        "breakdown_task: returning %d steps, context=%s, step_contexts=%d entries",
        len(steps),
        f"{len(context)} chars" if context else "None",
        len(step_contexts),
    )
    return steps, context, step_contexts


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
        _logger.debug(
            "get_workiq_context: starting for title=%r model=%s", title, model
        )

        client_opts: Dict[str, Any] = {}
        if debug:
            client_opts["log_level"] = "debug"
        cli_path = resolve_copilot_cli_path(debug=debug)
        if cli_path:
            client_opts["cli_path"] = cli_path
        _logger.debug("get_workiq_context: client_opts=%s", client_opts)

        client = CopilotClient(client_opts)
        await client.start()
        _logger.debug("get_workiq_context: CopilotClient started")

        mcp_command = workiq_command
        mcp_args = [token for arg in workiq_args for token in arg.split()]
        if sys.platform == "win32":
            mcp_args = ["/c", mcp_command] + mcp_args
            mcp_command = "cmd"

        def _auto_approve_permission(request: dict, context: dict) -> dict:
            """Auto-approve MCP tool calls in background context gathering.

            This runs from a web-server background task where the user has
            already accepted the WorkIQ EULA, so we approve automatically.
            """
            _logger.debug(
                "get_workiq_context: permission_request kind=%s request=%s",
                getattr(request, "kind", None),
                request,
            )
            return PermissionRequestResult(kind="approved")

        session_config: Dict[str, Any] = {
            "model": model,
            "on_permission_request": _auto_approve_permission,
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

        _loggable = {k: v for k, v in session_config.items() if not callable(v)}
        _logger.debug(
            "get_workiq_context: session_config=%s", json.dumps(_loggable, indent=2)
        )

        session = await client.create_session(session_config)
        _logger.debug("get_workiq_context: session created")

        if debug:

            def debug_handler(event: Any) -> None:
                data = event.data
                mcp_server = getattr(data, "mcp_server_name", None)
                mcp_tool = getattr(data, "mcp_tool_name", None)
                tool_name = getattr(data, "tool_name", None)
                content = getattr(data, "content", None)
                result = getattr(data, "result", None)
                parts = [f"[DEBUG] get_workiq_context: {event.type}"]
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
                _logger.debug(" | ".join(parts))

            session.on(debug_handler)

        _logger.debug("get_workiq_context: sending WorkIQ query for task=%r", title)
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
        _logger.debug("get_workiq_context: WorkIQ query completed, requesting summary")

        response = await session.send_and_wait(
            {
                "prompt": (
                    "Based on the context gathered, provide a brief summary of relevant "
                    "background context for this task in 1-4 sentences. "
                    "Return ONLY the plain text summary, no JSON or formatting."
                )
            }
        )
        _logger.debug("get_workiq_context: summary response received")

        await session.disconnect()
        await client.stop()
        _logger.debug("get_workiq_context: session destroyed, client stopped")

        if response and response.data:
            ctx = response.data.content.strip()
            if ctx:
                _logger.debug(
                    "get_workiq_context: returning context (%d chars)", len(ctx)
                )
                return ctx
        _logger.debug("get_workiq_context: no context in response")
        return None
    except Exception:
        _logger.error("get_workiq_context: failed for title=%r", title, exc_info=True)
        return None


async def get_copilot_context(
    title: str,
    model: str = "gpt-4.1",
    debug: bool = False,
) -> Optional[str]:
    """Get a brief AI-generated context for a task using Copilot only (no WorkIQ).

    Returns a short (1-4 sentence) description or None on failure.
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

        session_config: Dict[str, Any] = {
            "model": model,
            "system_message": {
                "content": (
                    "You are a helpful task assistant. When given a task title, "
                    "provide a brief context summary (1-4 sentences) describing "
                    "what this task likely involves, key considerations, and "
                    "suggested approach. Return ONLY plain text, no JSON or formatting."
                )
            },
        }

        session = await client.create_session(session_config)

        response = await session.send_and_wait(
            {"prompt": (f"Provide a brief context summary for this task: {title}")},
            timeout=30000,
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
        if kind_str in ("read", "write") and path:
            norm_path = os.path.normcase(os.path.abspath(path))
            if norm_path.startswith(norm_project):
                return PermissionRequestResult(kind="approved")
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
    task_id: int,
    task_title: str,
    task_breakdown: List[str],
    model: str,
    use_workiq: bool = True,
    workiq_command: str = "npx",
    workiq_args: Optional[List[str]] = None,
    debug: bool = False,
) -> Tuple[str, bool]:
    """Ask GitHub Copilot to implement a task inside a new project directory."""
    dir_name = f"{task_id}-{slugify(task_title)}"
    project_dir = os.path.abspath(dir_name)
    os.makedirs(project_dir, exist_ok=True)

    client_opts: Dict[str, Any] = {}
    if debug:
        client_opts["log_level"] = "debug"
    cli_path = resolve_copilot_cli_path(debug=debug)
    if cli_path:
        client_opts["cli_path"] = cli_path
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

    if task_breakdown:
        steps_text = "\n".join(f"  {i+1}. {s}" for i, s in enumerate(task_breakdown))
        impl_detail = (
            f"Task: {task_title}\n\n"
            f"Breakdown steps:\n{steps_text}\n\n"
            "Create a complete project with all necessary files. "
            "Follow the breakdown steps as a guide for the implementation."
        )
    else:
        impl_detail = (
            f"Task: {task_title}\n\nCreate a complete project with all necessary files."
        )

    print(f"Implementing task in: {project_dir}")
    try:
        if use_workiq:
            print("Querying WorkIQ for context...")
            await session.send_and_wait(
                {
                    "prompt": (
                        f"I need to implement this task: {task_title}\n\n"
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

        max_continuations = 5
        for i in range(max_continuations):
            created = [f for f in os.listdir(project_dir) if not f.startswith(".")]
            last_content = response.data.content if response and response.data else ""
            if created:
                break
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
