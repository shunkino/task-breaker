# Task Breaker — Demo Deck (Placeholder)

> **TODO:** Create a PowerPoint presentation (`TaskBreaker.pptx`) or a public blog post covering the content below.

---

## Slide 1: Title

**Task Breaker**
*AI-Powered Task Decomposition for Productivity*

- Hackathon 2026
- Author: [Your Name]

---

## Slide 2: The Problem

**Script:**
"We've all been there — you write a todo list at the start of the week, and by Friday, half the items are still untouched. They pile up, and the list becomes overwhelming. The real issue isn't laziness — it's that the tasks are too big and vague to start."

**Key points:**
- Tasks pile up when they're too large to act on
- Unfinished items create a negative feedback loop
- Lack of granularity hides actual progress

---

## Slide 3: The Insight

**Script:**
"Our hypothesis is simple: if you break a task into small enough pieces, you'll actually start working on it. The problem is, breaking things down takes effort — so we let AI do it automatically."

**Key points:**
- Smaller tasks → higher completion rates
- Manual breakdown is tedious and often skipped
- AI can do this automatically with the right context

---

## Slide 4: The Solution — Task Breaker

**Script:**
"Task Breaker is a local task management app that monitors your todo list. When a task sits untouched for too long — say 3 days — it automatically breaks it down into smaller, actionable sub-tasks using AI."

**Key points:**
- Local-first app (no cloud required)
- Automatic breakdown after configurable threshold
- Web UI + CLI interface
- Hierarchical task tree (up to 3 levels deep)

---

## Slide 5: Architecture

**Script:**
"Under the hood, Task Breaker is a Python FastAPI server with a SQLite database. It uses the GitHub Copilot SDK for AI-powered breakdown, and optionally connects to WorkIQ via MCP to understand your work context — like your calendar and recent emails — so the breakdowns are actually relevant."

**Visual:** Include the Mermaid architecture diagram from `docs/design.md`

**Key points:**
- FastAPI + SQLite (local)
- GitHub Copilot SDK for AI
- WorkIQ MCP for workplace context (optional)
- APScheduler for automatic breakdown timer

---

## Slide 6: Live Demo

**Demo script:**
1. Start the server: `uv run python src/cli.py serve`
2. Open browser to `http://127.0.0.1:8000`
3. Add a vague task: "Prepare for quarterly business review"
4. Show the task in the UI — no breakdown yet
5. Click "Breakdown" — watch AI generate sub-tasks
6. Show the task tree with hierarchical sub-tasks
7. Mark a sub-task as complete — show progress visibility
8. Show CLI usage: `uv run python src/cli.py list`

**Talking points during demo:**
- "Notice how the AI broke 'Prepare for QBR' into concrete steps like 'Gather metrics from Q1 dashboard' and 'Draft executive summary'"
- "Each sub-task is small enough to start immediately"
- "The tree view shows progress at every level"

---

## Slide 7: WorkIQ Integration

**Script:**
"What makes this different from just asking ChatGPT to break down your task is the WorkIQ integration. WorkIQ connects to your actual work tools — calendar, email, project boards — so the AI understands your context. Instead of generic sub-tasks, you get steps grounded in your real work."

**Key points:**
- MCP-based integration
- Reads workplace signals (calendar, email, tasks)
- Produces contextually relevant breakdowns
- Privacy-first: all data stays local

---

## Slide 8: Technical Highlights

**Script:**
"A few things we're proud of technically..."

**Key points:**
- GitHub Copilot SDK for AI orchestration
- MCP protocol for tool integration
- Smart max-tasks formula to avoid over-decomposition
- Auto-breakdown scheduler with configurable thresholds
- Dual mode: server + standalone CLI

---

## Slide 9: Responsible AI

**Script:**
"We took RAI seriously. All data stays on your machine. AI-generated breakdowns are suggestions — you can edit, delete, or disable auto-breakdown. The atomic flag lets you tell the system 'this task is small enough, stop breaking it down.'"

**Key points:**
- All data local — no external telemetry
- Human oversight at every step
- Configurable guardrails (max_level, atomic flag)
- Transparent AI usage — breakdown source is tracked

---

## Slide 10: What's Next

**Script:**
"Looking ahead, we want to add smart scheduling — where the app not only breaks down tasks but suggests when to work on them based on your calendar. We're also exploring integrations with GitHub Issues and Azure DevOps work items."

**Key points:**
- Smart scheduling based on calendar availability
- GitHub Issues / Azure DevOps integration
- Multi-user support with shared task boards
- Mobile-friendly UI

---

## Slide 11: Q&A

**Task Breaker** — *Stop stacking tasks. Start breaking them.*

GitHub: [repo URL]
