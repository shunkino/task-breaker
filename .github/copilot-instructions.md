# Copilot Instructions

## Build, test, and lint
- Install deps: `uv venv` then activate the venv and run `uv sync`.
- Tests: no test suite is defined in this repo.
- Linting/formatting: no linting tools are defined in this repo.

## High-level architecture
- Single-file CLI (`task_breaker.py`) that manages tasks stored in a JSON file under `~/.task-breaker/tasks.json` by default.
- Tasks are modeled as a `Task` dataclass and loaded/saved as JSON via `load_tasks`/`save_tasks`.
- Task breakdown uses the GitHub Copilot SDK to open a session and optionally calls the WorkIQ MCP server (local `workiq mcp`) when `--no-workiq` is not set.
- CLI commands are built with `argparse` and map to command handlers (`cmd_add`, `cmd_list`, `cmd_show`, `cmd_complete`, `cmd_note`, `cmd_breakdown`).

## Key conventions
- Keep task storage as JSON and preserve the schema defined by the `Task` dataclass.
- Timestamps use UTC ISO-8601 via `datetime.now(timezone.utc).isoformat()`.
- Copilot model default is `gpt-4.1`, overrideable with `--model`.
- WorkIQ MCP usage is enabled by default and configured through `--workiq-command` (default: `npx`) and `--workiq-args` (default: `-y @microsoft/workiq mcp`).
