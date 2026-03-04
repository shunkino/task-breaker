# Task Breaker — Design Document

## Overview

Task Breaker is a local task management application that uses AI (GitHub Copilot SDK + WorkIQ MCP) to automatically break down high-level tasks into smaller, actionable steps. It exposes both a web UI and a REST API, with a CLI client for terminal use.

---

## High-Level Architecture

```mermaid
graph TB
    subgraph Clients
        CLI["CLI Client<br/>(Typer + httpx)"]
        Browser["Web Browser"]
    end

    subgraph Server ["FastAPI Server"]
        API["REST API<br/>/api/*"]
        Web["Web Routes<br/>/ + /tasks/*"]
        Static["Static Files<br/>/static/*"]
        Templates["Jinja2 Templates"]
    end

    subgraph Core
        Services["Services Layer<br/>TaskService + BreakdownService"]
        Scheduler["APScheduler<br/>(background auto-breakdown)"]
    end

    subgraph AI ["AI Integration"]
        Copilot["GitHub Copilot SDK<br/>CopilotClient"]
        WorkIQ["WorkIQ MCP Server<br/>(optional)"]
    end

    subgraph Storage
        DB["SQLite Database<br/>~/.task-breaker/tasks.db"]
    end

    CLI -- HTTP --> API
    Browser -- HTTP --> Web
    Browser -- HTTP --> Static
    Web --> Templates
    API --> Services
    Web --> Services
    Scheduler --> Services
    Services --> DB
    Services --> Copilot
    Copilot --> WorkIQ
```

---

## Component Diagram

```mermaid
graph LR
    subgraph task_breaker["task_breaker/ package"]
        app["app.py<br/>FastAPI routes"]
        config["config.py<br/>Settings (Pydantic)"]
        database["database.py<br/>SQLAlchemy engine"]
        models["models.py<br/>TaskORM"]
        services["services.py<br/>TaskService<br/>BreakdownService"]
        copilot["copilot_integration.py<br/>breakdown_task()<br/>implement_task()"]
        sched["scheduler.py<br/>APScheduler jobs"]
    end

    cli["cli.py<br/>Typer CLI client"]

    cli -->|HTTP| app
    app --> services
    app --> config
    app --> database
    app --> sched
    services --> models
    services --> database
    services --> copilot
    sched --> services
    sched --> config
    database --> config
    models --> database
    copilot -->|Copilot SDK| CopilotAPI["GitHub Copilot"]
    copilot -->|MCP subprocess| WorkIQ["WorkIQ MCP"]
```

---

## Data Model

```mermaid
erDiagram
    tasks {
        int id PK "autoincrement"
        string title "required"
        string status "open | done"
        json breakdown "list of step strings"
        string notes "optional"
        string source "optional"
        datetime created_at "UTC"
        datetime updated_at "UTC, auto-updated"
        bool atomic "default false"
        int level "default 0, hierarchy depth"
        int parent_id FK "self-referencing"
        json children_ids "list of child task IDs"
        bool auto_breakdown_enabled "default true"
    }

    tasks ||--o{ tasks : "parent → children"
```

### Task Hierarchy

```mermaid
graph TD
    T1["Task #1 (level 0)"]
    T2["Task #2 (level 1)"]
    T3["Task #3 (level 1)"]
    T4["Task #4 (level 2, atomic)"]
    T5["Task #5 (level 2, atomic)"]
    T6["Task #6 (level 2, atomic)"]

    T1 --> T2
    T1 --> T3
    T2 --> T4
    T2 --> T5
    T3 --> T6
```

Tasks form a tree via `parent_id` / `children_ids`. When `level >= max_level` (default 3), child tasks are marked `atomic = true` and cannot be broken down further.

---

## Request Flow

### API Task Creation + Breakdown

```mermaid
sequenceDiagram
    actor User
    participant CLI as CLI / Browser
    participant API as FastAPI
    participant Svc as TaskService
    participant DB as SQLite
    participant BDS as BreakdownService
    participant AI as Copilot SDK
    participant MCP as WorkIQ MCP

    User->>CLI: add task "Build dashboard"
    CLI->>API: POST /api/tasks {title}
    API->>Svc: create_task(title)
    Svc->>DB: INSERT task
    DB-->>Svc: TaskORM
    Svc-->>API: task dict
    API-->>CLI: 201 Created

    User->>CLI: breakdown task #1
    CLI->>API: POST /api/tasks/1/breakdown
    API->>Svc: get_task(1)
    Svc->>DB: SELECT task
    DB-->>Svc: TaskORM
    API->>BDS: breakdown_task(task)
    BDS->>AI: create session (model, system prompt)
    AI->>MCP: ask_work_iq (gather context)
    MCP-->>AI: context results
    AI-->>BDS: JSON array of steps
    BDS-->>API: steps[]
    API->>Svc: update_breakdown(id, steps)
    Svc->>DB: UPDATE task.breakdown
    API->>Svc: create_child_tasks(parent, steps)
    Svc->>DB: INSERT child tasks
    DB-->>Svc: child IDs
    Svc-->>API: task dict with children
    API-->>CLI: 200 OK
```

### Auto-Breakdown (Scheduler)

```mermaid
sequenceDiagram
    participant Sched as APScheduler
    participant Svc as TaskService
    participant DB as SQLite
    participant BDS as BreakdownService
    participant AI as Copilot SDK

    loop Every check_interval_hours
        Sched->>Svc: find_stale_tasks(threshold_days)
        Svc->>DB: SELECT open tasks older than threshold
        DB-->>Svc: stale tasks[]
        loop For each stale task
            Sched->>BDS: breakdown_task(task)
            BDS->>AI: Copilot session
            AI-->>BDS: steps[]
            Sched->>Svc: update_breakdown(id, steps)
            Sched->>Svc: create_child_tasks(parent, steps)
            Svc->>DB: UPDATE + INSERT
        end
    end
```

---

## Configuration

```mermaid
graph LR
    subgraph Sources
        ENV["Environment Variables<br/>TASK_BREAKER_*"]
        DOTENV[".env file"]
        DEFAULTS["Defaults in code"]
    end

    subgraph Settings ["Settings (Pydantic)"]
        data_dir["data_dir<br/>~/.task-breaker"]
        db_url["db_url (derived)<br/>sqlite:///...tasks.db"]
        model["model<br/>gpt-4.1"]
        workiq["workiq_command / args<br/>npx -y @microsoft/workiq mcp"]
        auto["auto_breakdown_enabled<br/>threshold_days / interval"]
        max_level["max_level<br/>3"]
    end

    ENV --> Settings
    DOTENV --> Settings
    DEFAULTS --> Settings
```

| Setting | Default | Description |
|---------|---------|-------------|
| `data_dir` | `~/.task-breaker` | Storage directory |
| `model` | `gpt-4.1` | Copilot model |
| `max_level` | `3` | Max task hierarchy depth |
| `auto_breakdown_enabled` | `true` | Enable scheduler |
| `auto_breakdown_threshold_days` | `3` | Days before auto-breakdown |
| `check_interval_hours` | `1` | Scheduler check interval |

---

## Task Lifecycle

```mermaid
stateDiagram-v2
    [*] --> Open: create_task()
    Open --> Open: add_note() / update
    Open --> BrokenDown: breakdown_task()
    BrokenDown --> BrokenDown: add_note()
    Open --> Done: complete_task()
    BrokenDown --> Done: complete_task()
    Open --> [*]: delete_task()
    BrokenDown --> [*]: delete_task()
    Done --> [*]: delete_task()

    state Open {
        [*] --> Waiting
        Waiting --> AutoBreakdown: stale > threshold
        AutoBreakdown --> Waiting: scheduler runs
    }
```

---

## API Endpoints Summary

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/api/tasks` | List tasks (optional `?status=`) |
| `POST` | `/api/tasks` | Create task |
| `GET` | `/api/tasks/{id}` | Get task details |
| `POST` | `/api/tasks/{id}/complete` | Mark done |
| `POST` | `/api/tasks/{id}/note` | Add/update note |
| `POST` | `/api/tasks/{id}/breakdown` | Trigger AI breakdown |
| `DELETE` | `/api/tasks/{id}` | Delete task (returns deleted task JSON) |
| `GET` | `/api/settings` | Get settings |
| `PUT` | `/api/settings` | Update settings (in-memory) |
