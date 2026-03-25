# Project Context

- **Owner:** shkinosh
- **Project:** task-breaker — a local task management app using AI (GitHub Copilot SDK + WorkIQ MCP) to automatically break down high-level tasks into smaller, actionable steps
- **Stack:** Python, FastAPI, Jinja2 templates, SQLite, SQLAlchemy, APScheduler, Typer CLI, CSS
- **Created:** 2026-03-25

## Learnings

<!-- Append new learnings below. Each entry is something lasting about the project. -->

- **2026-03-25 — Full UI Redesign**: Replaced PicoCSS with a custom CSS design system. Dark-mode-first with `prefers-color-scheme: light` fallback. Uses CSS custom properties for full theming. No external fonts — system font stack only.
- **Design System**: All design tokens in `:root` custom properties (colors, spacing, radii, shadows, transitions). Accent color is indigo `#6366f1`. Uses glassmorphism (`backdrop-filter: blur`) on nav bar and board columns.
- **Navigation**: Sticky top nav with blur effect. Active page detected via JS matching `window.location.pathname`. On mobile (≤768px), nav links become a fixed bottom tab bar.
- **Kanban Board**: CSS Grid (`repeat(3, 1fr)`) instead of flexbox. Cards show action buttons on hover (opacity transition). Stagger fade-in animation on cards via `animation-delay`.
- **Focus Page**: Converted from `<table>` to card-based list (`.focus-card` divs). Drag-and-drop reorder uses same API endpoints. Unstar has a slide-out animation.
- **Settings Page**: Toggle switches use pure CSS (`.toggle` + `.toggle-track` pattern with `::after` pseudo-element). Config table uses `.config-table` class.
- **Tree View**: Custom expand/collapse triangle via `summary::before` with rotation transform. Connecting lines via `border-left` on nested `<ul>` and `::before` horizontal lines on `<li>`.
- **Key file paths**: `src/task_breaker/static/style.css` (design system), `src/task_breaker/templates/` (all 6 templates).
- **Constraint**: htmx CDN import kept in base.html. All Jinja2 variables, form actions, API endpoints, and JavaScript behavior preserved exactly.
