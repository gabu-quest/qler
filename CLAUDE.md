# Claude Context: qler

**Version:** 0.1.0 (pre-implementation)
**Type:** Python library — background job queue on SQLite

---

## What qler Is

qler is an **async-first background job queue** for Python, built on SQLite via sqler.

**One sentence:** "Background jobs without Redis, with first-class debugging."

- SQLite is the **only** infrastructure — no Redis, no RabbitMQ
- Async-native (`asyncio` first, sync compatibility layer second)
- Built for the **-ler ecosystem** (sqler, logler, procler)
- Debuggability over features — every failure is fully explainable

---

## Project Status

**Pre-implementation.** The spec is complete but implementation is blocked on sqler gaps.

| File | Purpose |
|------|---------|
| `SPEC.md` | Full technical specification (77KB) — **the source of truth** |
| `NORTH_STAR.md` | Vision, philosophy, non-negotiable principles |
| `SQLER_GAPS.md` | Blocking sqler features that must exist before implementation |
| `BRAINSTORM.md` | Design exploration, competitive analysis |

**No source code exists yet.** When implementation begins, the package will live in `src/qler/`.

---

## Core Architecture Decisions

| Decision | Choice | Rationale |
|----------|--------|-----------|
| Primary key | ULID (text) | Sortable, unique, no coordination needed |
| Job claiming | Lease-based | Predictable recovery without separate coordinator |
| Delivery guarantee | At-least-once | Explicit; tasks SHOULD be idempotent |
| Storage model | Promoted columns + JSON blob | Hot fields (status, queue, priority, eta) get real columns for indexes/CHECK; everything else stays in sqler's JSON document |
| Failure tracking | `FailureKind` enum | Structured reasons prevent ghost states, enable smart retry logic |
| Config | Code, not YAML | Git-friendly, code-reviewable |

---

## The -ler Stack

qler is one piece of a cohesive toolkit. All -ler libraries MUST integrate through their public APIs.

| Layer | Tool | Responsibility |
|-------|------|----------------|
| Storage | **sqler** | SQLite ORM, async, optimistic locking |
| Queues | **qler** | Background job execution |
| Logs | **logler** | Log aggregation, correlation IDs |
| Processes | **procler** | Process management, health checks |
| Future | **dagler** | DAG/pipeline orchestration (post qler v1.0) |

**Key integration:** A job's `correlation_id` connects the job record (qler) → logs (logler) → worker process (procler).

---

## Non-Negotiable Rules

### 1. Never bypass the -ler stack

- All DB operations MUST go through sqler's model API — no `db.adapter.execute()`, no raw SQL
- All logging MUST go through logler — no raw file reads
- If sqler can't express an operation, **fix sqler** — don't work around it

### 2. SQLite is the design center

- NEVER add features that "work better with Postgres"
- Use WAL mode, `UPDATE...RETURNING`, partial indexes — lean into SQLite strengths
- Single-file deployment is a feature, not a limitation

### 3. Debuggability over features

- Every design decision MUST answer: "Does this make debugging easier?"
- Full attempt history preserved, structured failure kinds, correlation IDs
- CLI outputs JSON for LLM consumption

### 4. Explicit over magic

- No auto-discovery of tasks — explicit `@task` decorator required
- Workers MUST specify which queues to process
- No UI-only settings that can't be code-reviewed

---

## Blockers (sqler Gaps)

Implementation MUST NOT begin until these sqler features exist. See `SQLER_GAPS.md` for full details.

| Gap | What | Blocks |
|-----|------|--------|
| Multi-field ordering | `order_by("-priority", "eta", "ulid")` | Deterministic claim query |
| Promoted columns | Real SQLite columns for hot fields | CHECK constraints, indexes |
| F-expressions in update | `update(attempts=F("attempts") + 1)` | Atomic counters |
| Atomic update-and-return | `update_one()` with `RETURNING` | Race-free job claiming |

---

## Tech Stack

| Tool | Version | Purpose |
|------|---------|---------|
| Python | 3.12+ | Runtime |
| asyncio | stdlib | Async foundation |
| SQLite | WAL mode | Storage (via sqler) |
| uv | latest | Package management, task running |
| sqler | latest | Database operations |

---

## When Implementation Begins

The package structure SHOULD follow:

```
src/qler/
├── __init__.py          # Public API: Queue, task, Job
├── queue.py             # Queue class
├── task.py              # @task decorator
├── models.py            # Job, JobAttempt (sqler models)
├── worker.py            # Worker loop
├── cli.py               # CLI interface
└── py.typed             # PEP 561 marker
tests/
├── conftest.py
├── test_queue.py
├── test_task.py
├── test_worker.py
└── test_models.py
```
