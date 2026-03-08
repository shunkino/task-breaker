# Task Breaker — 2-Minute Video Script

> **Total runtime:** ~2:00  
> **Tone:** Casual, confident, conversational — like explaining to a colleague  
> **Format:** Screen recording + voiceover

---

## [0:00–0:15] Hook — The Problem

**VISUAL:** Quick montage: a long todo list, items piling up, checkboxes untouched.

**VOICEOVER:**

> "You start the week with a clean todo list. By Wednesday, half the items are untouched. By Friday, the list is longer than when you started. Sound familiar? The problem isn't motivation — it's that your tasks are too big to start."

---

## [0:15–0:30] The Insight

**VISUAL:** A big block labeled "Prepare QBR" splits into four smaller blocks.

**VOICEOVER:**

> "Here's the thing — if you break a task into small enough pieces, you'll actually do it. But breaking things down takes effort. So we let AI do it for you — automatically."

---

## [0:30–0:55] Introducing Task Breaker

**VISUAL:** Show the web UI. Add a task: "Prepare for quarterly business review." Click Breakdown.

**VOICEOVER:**

> "This is Task Breaker. You add a task — something big and vague like 'Prepare for quarterly business review.' Hit Breakdown, and the AI splits it into concrete steps you can start right now — things like 'Gather Q1 metrics,' 'Draft executive summary,' 'Book review meeting.'
>
> Each sub-task is small enough to actually act on."

---

## [0:55–1:15] The Secret Sauce — WorkIQ Context

**VISUAL:** Diagram showing WorkIQ pulling context from calendar, emails, work items → feeding into Copilot SDK → producing tailored sub-tasks.

**VOICEOVER:**

> "What makes this different from just asking an LLM to break down your task? Context. Task Breaker connects to WorkIQ — which reads signals from your real work tools: your calendar, your emails, your project boards. So instead of generic sub-tasks, you get steps grounded in what's actually happening in your work."

---

## [1:15–1:35] Auto-Breakdown + Progress

**VISUAL:** Show the tree view with a parent task and completed children. Progress bar moving.

**VOICEOVER:**

> "And you don't even have to click Breakdown manually. If a task sits untouched for a few days, Task Breaker breaks it down automatically. You see progress at every level — check off sub-tasks, and the parent tracks your momentum. It feels like you're actually moving forward."

---

## [1:35–1:50] How It Works (Quick Tech)

**VISUAL:** Simple architecture slide: User → Task Breaker → WorkIQ MCP + GitHub Copilot SDK → Sub-tasks.

**VOICEOVER:**

> "Under the hood: it's a Python app running locally on your machine. GitHub Copilot SDK powers the AI breakdown. WorkIQ provides context via MCP. Your data never leaves your machine."

---

## [1:50–2:00] Close

**VISUAL:** Logo + tagline.

**VOICEOVER:**

> "Task Breaker. Break it down. Feel the progress."

---

## Production Notes

- **Screen recordings needed:** Web UI (add task, breakdown, tree view, complete sub-task)
- **Diagrams needed:** Task splitting animation, WorkIQ context flow, architecture overview
- **Music:** Light, upbeat background track — think productivity app trailer
- **Captions:** Recommended for accessibility
