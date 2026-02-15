# Quick Reference: Doctrine Index

**All doctrines summarized on one page — Use this to decide what to read**

---

## Core Doctrines (Read for Every Project)

### [`agents.md`](../../agents.md) — 1,540 tokens
**Prime Directive:** Execute decisively without artificial limitations

**What it covers:**
- Instruction precedence (user → agents.md → repo conventions)
- Non-negotiable execution rules (maximal work, no time-limit talk, no stubs)
- Default workflow (Orient → Plan → Execute → Verify → Commit → Report)
- Standard artifacts (SPEC, TASKS, DESIGN, PLAN, HANDOFF, ADR)
- Git discipline (branching, commits, merges)
- Modern stack (Python 3.12+, Vue 3, uv, FastAPI, Pinia)
- 9 specialized roles (Planner, Dev, Test Engineer, etc.)

**When to read:** Every session, every project

---

### [`docs/testing.md`](../testing.md) — 1,688 tokens
**Prime Directive:** A failing test is a gift

**What it covers:**
- Test taxonomy (unit, integration, feature, E2E, stress)
- Definition of done (backend needs unit+integration+feature; frontend needs Playwright)
- Non-negotiable rules (determinism, meaningful assertions, public API testing)
- Playwright is mandatory for ALL frontend features
- Python testing (pytest, fixtures, markers)
- Frontend testing (Vitest + Playwright)
- CI strategy (fast vs full suites)

**When to read:** Every session, especially when writing tests

---

## Domain-Specific Doctrines (Load as Needed)

### [`docs/doctrine/git.md`](../doctrine/git.md) — 536 tokens
**Prime Directive:** Git history is part of the product

**What it covers:**
- Branching strategy (`feat/T1-slug`, `fix/T2-slug`)
- Commit discipline (imperative mood, specific messages, after logical units)
- Merge philosophy (preserve history, understand conflicts)
- `.gitignore` policy (non-negotiable rules)
- Safety and hygiene

**When to read:** Git operations, code reviews, merge conflicts

---

### [`docs/doctrine/ci.md`](../doctrine/ci.md) — 468 tokens
**Prime Directive:** Fast feedback, quality gates

**What it covers:**
- Fast vs full test suites (PR vs nightly)
- Caching strategies
- Artifact management
- Flake policy (fix immediately, never ignore)
- Quality gates and failure handling

**When to read:** Setting up CI/CD, debugging pipeline issues

---

### [`docs/doctrine/design.md`](../doctrine/design.md) — 596 tokens
**Prime Directive:** Change the system intentionally

**What it covers:**
- When DESIGN docs are required (new subsystems, API changes, migrations)
- When ADRs are required (constraining decisions, multiple alternatives)
- Planning phases (SPEC → TASKS → DESIGN → PLAN)
- 17-section DESIGN template
- Public interface stability rules
- Decision discipline (avoid ad-hoc changes)

**When to read:** Planning features, making architectural decisions

---

### [`docs/doctrine/security.md`](../doctrine/security.md) — 776 tokens
**Prime Directive:** Secure by default

**What it covers:**
- OWASP Top 10 alignment
- Secure defaults and safe libraries
- Input validation and sanitization
- Auth/session/token handling
- Secrets management
- Security testing readiness (ZAP/Burp)

**When to read:** Security-sensitive code (auth, sessions, file uploads, APIs)

---

### [`docs/doctrine/style.md`](../doctrine/style.md) — 436 tokens
**Prime Directive:** Clarity over cleverness

**What it covers:**
- Code layout and naming conventions
- API envelope patterns
- Error handling standards
- UI/A11Y consistency
- Language-specific conventions (Python, TypeScript, Vue)

**When to read:** Code reviews, refactoring, establishing patterns

---

### [`docs/doctrine/handoff.md`](../doctrine/handoff.md) — 548 tokens
**Prime Directive:** Leave clear state for next session

**What it covers:**
- Handoff document format (current branch, completed tasks, TODOs, risks)
- Multi-session work protocol
- Orchestrator compatibility signals
- State tracking for long-running work

**When to read:** Multi-session work, before ending long tasks

---

## Quick References (One-Page Summaries)

- **[agents-quick-ref.md](./agents-quick-ref.md)** — 668 tokens — Core principles, workflow, git, stack
- **[testing-quick-ref.md](./testing-quick-ref.md)** — 732 tokens — Rules, taxonomy, examples
- **[git-quick-ref.md](./git-quick-ref.md)** — ~450 tokens — Branch naming, commits, merge philosophy
- **[security-quick-ref.md](./security-quick-ref.md)** — ~600 tokens — OWASP checklist, validation, auth rules
- **[This file]** — ~400 tokens — All doctrines indexed

---

## Design Systems (For Vue 3 + Naive UI)

See **[The Style](https://github.com/gabu-quest/the-style)** - separate repository with:
- **Goshuin Edition** - Ceremonial & calm (crimson, gold, serif)
- **Cyberpunk Edition** - Neon & rebellious (pink, cyan, geometric)

Both include complete token systems, Naive UI integration, and documentation.

---

## Decision Matrix: What to Read When

| Task | Must Read | Optional |
|------|-----------|----------|
| **Starting any work** | agents.md, testing.md | agents-quick-ref.md |
| **Planning feature** | agents.md, design.md | DESIGN template |
| **Writing tests** | testing.md | testing-quick-ref.md |
| **Git operations** | git.md | git-quick-ref.md |
| **CI/CD setup** | ci.md | testing.md §10 |
| **Security code** | security.md | security-quick-ref.md |
| **Code review** | style.md | agents.md §8 |
| **Multi-session** | handoff.md | HANDOFF template |
| **Vue 3 UI** | [The Style](https://github.com/gabu-quest/the-style) | Design systems repo |

---

## Token Budget Summary

| Category | Tokens | When to Load |
|----------|--------|--------------|
| **Core** (agents + testing) | ~3,200 | Every session |
| **All doctrines** | ~4,700 | As needed |
| **Quick refs** | ~2,850 | Tight context |
| **Examples** | ~9,200 | Reference needed |
| **TOTAL** | ~24,000 | All at once (200k context) |

---

## Loading Strategy by Context Size

**200k context (Claude):** Load everything (~24k tokens)

**100k context (GPT-4):** Load core + relevant doctrines (~13k tokens)

**32k context (tight):** Use quick-refs only (~1.8k tokens)

---

**Full Index:** [CLAUDE.md](../../CLAUDE.md) | **Repository:** [README.md](../../README.md)
