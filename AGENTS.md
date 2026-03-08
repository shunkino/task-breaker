# AGENTS.md — Custom Instructions for AI Agents

## Project Overview

Task Breaker is a local task management app that uses AI (GitHub Copilot SDK + WorkIQ MCP) to automatically break down high-level tasks into smaller, actionable steps.

## Build, Test, and Lint

- Install deps: `uv sync` (creates `.venv` automatically)
- Run server: `uv run python src/cli.py serve`
- Run standalone CLI: `uv run python src/task_breaker.py`
- Tests: no test suite is defined in this repo
- Linting/formatting: no linting tools are defined in this repo

## High-Level Architecture

- **Server mode** (`src/task_breaker/`): FastAPI web server with browser UI, REST API, Typer CLI client (`src/cli.py`), SQLite storage, and APScheduler for auto-breakdown.
- **Standalone CLI mode** (`src/task_breaker.py`): Single-file CLI using argparse, JSON file storage, direct Copilot SDK integration.
- Both modes use GitHub Copilot for AI-powered task breakdown and optionally integrate with WorkIQ MCP for contextual grounding.

## Source Layout

```
src/
  cli.py                     # Typer CLI client (talks to server via HTTP)
  task_breaker.py            # Standalone CLI (direct Copilot SDK, no server)
  task_breaker/              # FastAPI server package
    app.py                   #   FastAPI routes (API + web)
    config.py                #   Pydantic settings
    copilot_integration.py   #   Copilot SDK + WorkIQ integration
    database.py              #   SQLAlchemy engine
    models.py                #   TaskORM model
    scheduler.py             #   APScheduler background jobs
    services.py              #   TaskService + BreakdownService
    static/                  #   CSS
    templates/               #   Jinja2 HTML templates
```

## Key Conventions

- Task storage: SQLite (server mode) or JSON (standalone mode)
- Timestamps: UTC ISO-8601 via `datetime.now(timezone.utc).isoformat()`
- Copilot model default: `gpt-4.1`, overrideable with `--model` or `TASK_BREAKER_MODEL`
- WorkIQ MCP: enabled by default, configured via `--workiq-command` / `--workiq-args`
- Config env var prefix: `TASK_BREAKER_`

## MCP Servers

This project uses the WorkIQ MCP server for workplace context. See `mcp.json` for configuration.
