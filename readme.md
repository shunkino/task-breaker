## Task Breaker CLI

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
