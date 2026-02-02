## Task Breaker CLI

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
