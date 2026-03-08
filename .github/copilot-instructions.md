# Copilot Instructions

## Build, test, and lint
- Install deps: `uv sync` (creates `.venv` automatically).
- Run server: `uv run python src/cli.py serve`
- Run standalone CLI: `uv run python src/task_breaker.py`
- Tests: no test suite is defined in this repo.
- Linting/formatting: no linting tools are defined in this repo.

## High-level architecture
- **Server mode** (`src/task_breaker/`): FastAPI web server with browser UI, REST API, Typer CLI client (`src/cli.py`), SQLite storage, APScheduler for auto-breakdown.
- **Standalone CLI mode** (`src/task_breaker.py`): Single-file CLI using argparse, JSON file storage, direct Copilot SDK integration.
- Both modes use GitHub Copilot for AI-powered task breakdown and optionally integrate with WorkIQ MCP.

## Key conventions
- Keep task storage as JSON and preserve the schema defined by the `Task` dataclass.
- Timestamps use UTC ISO-8601 via `datetime.now(timezone.utc).isoformat()`.
- Copilot model default is `gpt-4.1`, overrideable with `--model`.
- WorkIQ MCP usage is enabled by default and configured through `--workiq-command` (default: `npx`) and `--workiq-args` (default: `-y @microsoft/workiq mcp`).
