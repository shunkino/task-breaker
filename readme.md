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
  --workiq-command CMD   Override WorkIQ command (default: npx)
  --workiq-args ...      Override WorkIQ args (default: -y @microsoft/workiq mcp)

### WorkIQ notes
- First use requires accepting WorkIQ EULA: `workiq accept-eula`
- If running via npx, ensure Node.js is available

### UV notes
- `uv sync` will create/update `uv.lock` for pinned versions.
