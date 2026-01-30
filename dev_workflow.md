## Development Workflow (Git Worktrees + Copilot CLI)

### Goal
- Enable parallel development with multiple coding agents by isolating work in git worktrees.

### Prereqs
- Git repository initialized or cloned.
- GitHub Copilot CLI installed and authenticated.
- uv installed for Python dependency management.

### Worktree Layout
- Keep the main worktree in the repo root.
- Create one branch + worktree per agent.

### Create Worktrees
1) From the main worktree:
```bash
git checkout -b main
git pull --ff-only  # if remote exists
```

2) Create per-agent worktrees:
```bash
git worktree add ../task-breaker-agent-a -b agent/a
git worktree add ../task-breaker-agent-b -b agent/b
```

### Agent Workflow (per worktree)
1) Enter the worktree:
```bash
cd ../task-breaker-agent-a
```

2) Sync Python deps:
```bash
uv sync
```

3) Start Copilot CLI:
```bash
copilot
```

4) Maximize agent capability inside Copilot CLI:
   - /plan <prompt> to get a structured plan before coding
   - /agent to select specialized agents if available
   - /mcp show/add to confirm MCP servers (e.g., WorkIQ)
   - /review <prompt> to run a code review before finishing
   - /session rename to label the session per agent

5) Keep changes scoped:
   - Prefer small, focused commits
   - Run relevant checks/tests

### Integration Workflow (main worktree)
1) Merge each agent branch:
```bash
git merge agent/a
git merge agent/b
```

2) Resolve conflicts and rerun checks.

3) Clean up worktrees after merge:
```bash
git worktree remove ../task-breaker-agent-a
git worktree remove ../task-breaker-agent-b
git branch -d agent/a agent/b
```

### Coordination Tips
- One agent per worktree to avoid file contention.
- Keep uv.lock changes isolated per branch; resolve lock conflicts in main.
- Use clear branch names and session names to track ownership.
- Use /share if you need to export a session summary to a file or gist.
