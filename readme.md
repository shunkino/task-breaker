## Task Breaker

AI-powered task decomposition for productivity. Automatically breaks down stale tasks into smaller, actionable steps using GitHub Copilot SDK and WorkIQ MCP.

### Quick Start

```bash
uv sync                                  # install dependencies
uv run python src/cli.py serve           # start the server
# open http://127.0.0.1:8000             # web portal
uv run python src/cli.py add "My task" --breakdown   # add + break down
```

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

### Documentation

See **[docs/README.md](docs/README.md)** for full documentation including:
- Problem → Solution narrative
- Prerequisites and setup
- Deployment guide
- Architecture diagrams
- REST API reference
- Responsible AI notes
