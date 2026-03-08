## Task Breaker

AI-powered task decomposition for productivity. Automatically breaks down stale tasks into smaller, actionable steps using GitHub Copilot SDK and WorkIQ MCP.

### Quick Start

```bash
uv sync                                  # install dependencies
uv run python src/cli.py serve           # start the server
# open http://127.0.0.1:8000             # web portal
uv run python src/cli.py add "My task" --breakdown   # add + break down
```

> **Note:** You must accept the [WorkIQ MCP EULA](https://github.com/microsoft/work-iq-mcp) before using context-gathering features. The server will prompt you on first use, or you can accept it via the settings page. This feature might not work in some scenario. In that case, use @modelcontextprotocol/inspector or other tools to accept it.  
`npx @modelcontextprotocol/inspector npx -y @microsoft/workiq mcp`

### Project Structure

```
src/                    # Working source code
  cli.py                #   Typer CLI client (server mode)
  task_breaker.py       #   Standalone CLI (no server required)
  task_breaker/         #   FastAPI server package
docs/                   # Full documentation
  README.md             #   Problem, setup, deployment, architecture, RAI notes
  design.md             #   Architecture diagrams (Mermaid)
presentations/          # Demo deck
  TaskBreaker.md        #   Draft script (placeholder for .pptx)
AGENTS.md               # Custom instructions for AI agents
mcp.json                # MCP server configuration (WorkIQ)
```

### Architecture Overview

```mermaid
flowchart LR
    User["👤 User creates a task"] --> TB["Task Breaker"]
    TB -- "gather work context" --> WorkIQ["WorkIQ MCP (work items, docs, discussions)"]
    WorkIQ -- "relevant context" --> GitHubCopilot["GitHub Copilot SDK (AI breakdown)"]
    TB -- "break down task request" --> GitHubCopilot
    GitHubCopilot -- "actionable sub-tasks + context notes" --> TB
```

1. User adds a high-level task.
2. Task Breaker queries **WorkIQ** for related work items, docs, and discussions.
3. That context feeds into the **GitHub Copilot SDK**, which generates an actionable breakdown.
4. Sub-tasks (with AI context) are saved back and ready to work on.

### Task Breakdown Flow

```mermaid
flowchart TD
    subgraph User
        U["👤 User"]
    end

    U -- "creates" --> T0["📋 Task 'Plan Q2 roadmap'"]

    subgraph Context ["Context Gathering (WorkIQ MCP)"]
        W1["📎 Related work items"]
        W2["📄 Documents & wikis"]
        W3["💬 Discussions & threads"]
    end

    subgraph AI ["AI Breakdown (GitHub Copilot SDK)"]
        COP["🤖 Copilot + WorkIQ context"]
    end

    T0 -- "stale or user-triggered" --> COP
    W1 & W2 & W3 -- "grounding context" --> COP

    COP -- "generates" --> T1["✅ Sub-task 1 'Review last quarter OKRs'"]
    COP -- "generates" --> T2["✅ Sub-task 2 'Collect team input'"]
    COP -- "generates" --> T3["✅ Sub-task 3 'Draft roadmap document'"]
    COP -- "generates" --> T4["✅ Sub-task 4 'Schedule review meeting'"]

    T1 & T2 & T3 & T4 -- "child of" --> T0

    style T0 fill:#4a6fa5,color:#fff
    style T1 fill:#6b9f6b,color:#fff
    style T2 fill:#6b9f6b,color:#fff
    style T3 fill:#6b9f6b,color:#fff
    style T4 fill:#6b9f6b,color:#fff
    style COP fill:#f0a030,color:#fff
```

**Key:** A single user task becomes multiple actionable sub-tasks, each informed by real workplace context — so breakdowns are relevant, not generic.



### Feature Status

**Implemented:**
- Task breakdown — AI-powered decomposition of high-level tasks into actionable sub-steps
- Context gathering — WorkIQ MCP integration to fetch related work items, docs, and discussions and attach them to tasks
- Naive Task implementation — using GitHub Copilot SDK to try executing a task directly

**Not yet implemented:**
- Automatic task execution — end-to-end autonomous execution of tasks
- Task type detection — classifying tasks by action type (writing a PoC, drafting an email, etc.) to route them appropriately
- SKILL.md-based multi-task execution — leveraging skill definitions to orchestrate execution across multiple tasks

### Documentation

See **[docs/README.md](docs/README.md)** for full documentation including:
- Problem → Solution narrative
- Prerequisites and setup
- Deployment guide
- Architecture diagrams
- REST API reference
- Responsible AI notes
