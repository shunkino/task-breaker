## Task Breaker

### Server mode (new)

Task Breaker can now run as a local server with a web portal and REST API.

#### Start the server
```bash
python cli.py serve
# or with custom host/port
python cli.py serve --host 127.0.0.1 --port 8000
```

#### Web portal
Open <http://127.0.0.1:8000> in your browser to manage tasks visually.

#### New CLI client (`cli.py`)
```bash
python cli.py add "Plan Q2 roadmap"
python cli.py add "Build login page" --breakdown   # triggers AI breakdown immediately
python cli.py list
python cli.py list --status open
python cli.py show 1
python cli.py breakdown 1
python cli.py complete 1
python cli.py note 1 "Follow up with design"
python cli.py delete 1
```

> The server must be running before using `cli.py` commands (except `serve`).

#### Auto-breakdown scheduler
The server includes a background scheduler that automatically breaks down stale tasks.
A task is considered stale when it is:
- `open` with no breakdown
- older than the configured threshold (default: 3 days)
- not marked as atomic
- has `auto_breakdown_enabled = true`

Configure the scheduler via the **Settings** page at <http://127.0.0.1:8000/settings>
or with environment variables (prefix `TASK_BREAKER_`).

#### REST API
| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/tasks` | List tasks (`?status=open\|done`) |
| POST | `/api/tasks` | Create task `{"title": "..."}` |
| GET | `/api/tasks/{id}` | Get task |
| POST | `/api/tasks/{id}/complete` | Mark done |
| POST | `/api/tasks/{id}/note` | Add note `{"note": "..."}` |
| DELETE | `/api/tasks/{id}` | Delete task |
| POST | `/api/tasks/{id}/breakdown` | Trigger AI breakdown |
| GET | `/api/settings` | Get settings |
| PUT | `/api/settings` | Update settings |

#### Configuration
Set environment variables (prefix `TASK_BREAKER_`) or create a `.env` file:
```
TASK_BREAKER_MODEL=gpt-4.1
TASK_BREAKER_AUTO_BREAKDOWN_ENABLED=true
TASK_BREAKER_AUTO_BREAKDOWN_THRESHOLD_DAYS=3
TASK_BREAKER_CHECK_INTERVAL_HOURS=1
TASK_BREAKER_MAX_LEVEL=3
```

Data is stored as SQLite at `~/.task-breaker/tasks.db`.

---

## Task Breaker CLI (original standalone)

### Background

This project was born out of my personal experience with daily todo lists. I often couldn't finish all my tasks in a given day, and they would quickly stack up — eventually making it impossible to keep up with the growing number of todos.

My assumption is that the reason tasks pile up is that they aren't broken down into small enough chunks. To solve this, I came up with the idea of automatic task breakdown using AI tools. I utilized [WorkIQ](https://github.com/microsoft/workiq) to let the agent understand my situation correctly, and GitHub Copilot to allow it to even create simple apps or fix issues in code automatically.

**User scenario:** When a user has a todo task that hasn't been finished for *x* days, the Task Breaker app will ask the user if they want to break down that task into smaller pieces.

**Goal:** Automatically break down tasks into smaller ones if they remain unresolved for a predefined number of days. For example, if the user sets the timer to 3 days and a registered task is not finished within that period, this tool will automatically kick in and split the task into smaller, more actionable chunks. This way, the user can more easily start working on the task, increasing the probability that it moves forward. The task status also becomes more granular, giving better visibility into progress.

> **Note:** This project is still in a very experimental stage — the automatic breakdown feature is not yet implemented.

### Prereqs
- Python 3.10+
- uv (Python package manager)
- GitHub Copilot CLI installed and authenticated
- Node.js (for WorkIQ MCP via npx)

### Install
```bash
uv venv
. .venv/bin/activate
uv sync
```

### Usage
```bash
./task_breaker.py add "Plan Q2 roadmap" --breakdown
./task_breaker.py list
./task_breaker.py breakdown 1
./task_breaker.py complete 1
./task_breaker.py note 1 "Follow up with design"
```

### Options
  --storage PATH         Override storage path
  --model MODEL          Copilot model (default: gpt-4.1)
  --no-workiq            Disable WorkIQ MCP server
  --workiq-command CMD   Override WorkIQ command (default: workiq)
  --workiq-args ...      Override WorkIQ args (default: mcp)
  --usage-log [MODE]     Usage logging: off|stderr|file|both (default: off)
  --usage-log-path PATH  Usage log file path (default: ~/.task-breaker/usage.log)

### WorkIQ notes
- Install WorkIQ CLI: `npm install -g @microsoft/workiq`
- First use requires accepting WorkIQ EULA: `workiq accept-eula`
- To use via npx instead: `--workiq-command npx --workiq-args -y @microsoft/workiq mcp`

### GitHub Copilot CLI notes
- Install: `npm install -g @github/copilot`
- The SDK looks for `copilot` in PATH or uses `COPILOT_CLI_PATH` environment variable

#### Windows platform
On Windows, VS Code installs a `copilot.ps1` bootstrapper that shadows the npm-installed CLI. Python's `subprocess` cannot execute `.ps1` files directly.

**Fix:** Set `COPILOT_CLI_PATH` to point to the npm-installed `.cmd` wrapper:

```powershell
$env:COPILOT_CLI_PATH = "$env:APPDATA\npm\copilot.cmd"
```

Or set it permanently:
```powershell
[Environment]::SetEnvironmentVariable("COPILOT_CLI_PATH", "$env:APPDATA\npm\copilot.cmd", "User")
```

### UV notes
- `uv sync` will create/update `uv.lock` for pinned versions.
