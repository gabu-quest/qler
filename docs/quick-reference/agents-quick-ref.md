# Quick Reference: Agent Doctrine

**One-page summary of [`agents.md`](../../agents.md) — Print this for quick reference**

---

## Core Principles

1. **Execution over discussion** - Do the work, don't debate time limits
2. **Quality is non-negotiable** - Tests must be meaningful, code must be correct
3. **No placeholders** - Real implementation, not stubs
4. **Maximal useful work** - Do as much as possible each step
5. **Keep repo healthy** - Incremental, reviewable, tested commits

---

## Instruction Precedence (Highest → Lowest)

1. Current user request (what they just asked)
2. This agents.md (global + role rules)
3. Repository conventions (existing patterns)
4. Everything else (default habits, suggestions)

---

## Forbidden Behaviors

❌ **NEVER:**
- Mention time limits or "I can't because constraints"
- Narrow scope without permission
- Create stubs/placeholders/TODOs for core functionality
- Write toy tests that always pass
- Skip items in a todo list
- Artificially batch work into tiny chunks

---

## Default Workflow

```
Orient → Plan → Execute → Verify → Commit → Report
```

1. **Orient** - Identify entry points, patterns, tests
2. **Plan** - Brief for small, detailed for large (SPEC → TASKS → DESIGN → PLAN)
3. **Execute** - Implement coherent chunk end-to-end
4. **Verify** - Run/update tests, check build
5. **Commit** - After each logical unit
6. **Report** - What changed + what remains

---

## Standard Artifacts

| Artifact | Location | Purpose |
|----------|----------|---------|
| SPEC | docs/specs/ | What & why |
| TASKS | TASKS.md | IDs + acceptance criteria |
| DESIGN | docs/design/ | How (architecture, tradeoffs) |
| ADR | docs/adr/ | Significant decisions |
| PLAN | plan.md | Execution order |
| HANDOFF | handoff.md | Multi-session state |
| CHANGELOG | CHANGELOG.md | Version history |

---

## Git Doctrine (Quick)

**Branching:**
- `feat/T1-<slug>`, `fix/T2-<slug>`, `refactor/T3-<slug>`

**Commits:**
- Imperative mood: "add session endpoints" not "added" or "adds"
- Specific: "fix auth for HTTP and WS" not "fix bug"
- After each logical unit (feature/test/refactor slice)

**Never:**
- Merge to main without permission
- Force push without permission
- Commit inside `.gitignore` without permission

---

## Modern Stack Standards

### Python
- **3.12+**, **uv**, **pyproject.toml**, **Pydantic v2**, **FastAPI**, **pytest**, **httpx**

### Vue
- **Vue 3**, **Composition API**, **`<script setup>`**, **Pinia**, **Vitest**, **Playwright**

---

## Testing Rules (Quick)

**MUST:**
- Write tests alongside implementation
- Assert meaningful behavior (not trivialities)
- Make tests deterministic (no flakes)
- Cover happy path + edge cases + error paths
- Test public API, not private internals
- **Playwright for ALL frontend features**

**MUST NOT:**
- Fudge tests to make them pass
- Skip tests without permission
- Use sleep-based timing (except last resort)

**Prime Directive:** A failing test is a gift. Fix the product, not the test.

---

## Roles (Quick Summary)

| Role | Job |
|------|-----|
| **Planner** | SPEC → TASKS → DESIGN → PLAN |
| **Dev** | Implement + test + commit |
| **Test Engineer** | Expand coverage, no fake tests |
| **UX/DevX Reviewer** | Find issues → tasks |
| **Refactorer** | Improve structure, keep tests green |
| **Merger** | Resolve conflicts thoughtfully |
| **Docs** | Keep docs accurate + ADRs |
| **Security** | Audit + concrete tasks |
| **Release/Ops** | Prepare releases (don't push without permission) |

---

## Decision Making

**Prefer:**
- Stable, boring solutions
- Existing patterns
- Well-known libraries
- Simplicity

**Avoid:**
- Clever hacks
- Unmaintained dependencies
- Scope creep
- Over-engineering

---

## When in Doubt

1. Read the user's request again - what did they actually ask for?
2. Check agents.md for the rule
3. Look at existing code patterns
4. Choose the boring, simple solution
5. Do it end-to-end with tests

---

## Success Criteria

✅ **You're doing well if:**
- Work is complete, tested, and committed
- No placeholders or TODOs in critical path
- Tests are meaningful and passing
- Commits are reviewable
- User's request is fully addressed
- You didn't ask permission for things you should just do

---

**Full Docs:** [agents.md](../../agents.md) | **Testing:** [testing.md](../testing.md) | **Git:** [git.md](../doctrine/git.md)
