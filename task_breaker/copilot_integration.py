"""Extract of the Copilot/WorkIQ integration from task_breaker.py.

Functions here are standalone async callables used by services.
"""
import json
import os
import re
import shutil
import sys
from typing import Any, Dict, List, Optional, Tuple

from copilot import CopilotClient
from copilot.generated.session_events import SessionEventType


def resolve_copilot_cli_path(debug: bool = False) -> Optional[str]:
    """On Windows, resolve the full path to the copilot CLI."""
    if sys.platform != "win32":
        if debug:
            print("[DEBUG] resolve_copilot_cli_path: not Windows, skipping", file=sys.stderr)
        return None
    env_path = os.environ.get("COPILOT_CLI_PATH")
    if env_path:
        if debug:
            print(f"[DEBUG] resolve_copilot_cli_path: COPILOT_CLI_PATH is set to '{env_path}'", file=sys.stderr)
        return None
    found = shutil.which("copilot")
    if debug:
        print(f"[DEBUG] resolve_copilot_cli_path: shutil.which('copilot') = '{found}'", file=sys.stderr)
    if found:
        if debug:
            print(f"[DEBUG] resolve_copilot_cli_path: using resolved path '{found}'", file=sys.stderr)
        return found
    appdata = os.environ.get("APPDATA", "")
    if debug:
        print(f"[DEBUG] resolve_copilot_cli_path: APPDATA = '{appdata}'", file=sys.stderr)
    if appdata:
        cmd_path = os.path.join(appdata, "npm", "copilot.cmd")
        exists = os.path.isfile(cmd_path)
        if debug:
            print(f"[DEBUG] resolve_copilot_cli_path: checking '{cmd_path}' exists={exists}", file=sys.stderr)
        if exists:
            return cmd_path
    if debug:
        npm_dir = os.path.join(appdata, "npm") if appdata else ""
        if npm_dir and os.path.isdir(npm_dir):
            copilot_files = [f for f in os.listdir(npm_dir) if "copilot" in f.lower()]
            print(f"[DEBUG] resolve_copilot_cli_path: copilot-related files in npm dir: {copilot_files}", file=sys.stderr)
        else:
            print(f"[DEBUG] resolve_copilot_cli_path: npm dir '{npm_dir}' not found", file=sys.stderr)
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
        usage_logger.emit("copilot", {"model": model, "source_command": source_command})
        if use_workiq:
            usage_logger.emit(
                "workiq",
                {"command": workiq_command, "args": workiq_args, "source_command": source_command},
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

    if debug:
        print(f"[DEBUG] session_config = {json.dumps(session_config, indent=2)}", file=sys.stderr)

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
        if kind in ("read", "write") and path:
            norm_path = os.path.normcase(os.path.abspath(path))
            if norm_path.startswith(norm_project):
                return {"kind": "approved"}
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
        impl_detail = f"Task: {task_title}\n\nCreate a complete project with all necessary files."

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
                for kw in ["cannot", "unable", "could not", "please clarify", "please provide"]
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
