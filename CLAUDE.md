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

**Pre-implementation.** The spec is build-ready but blocked on sqler gaps.

| File | Purpose |
|------|---------|
| `SPEC.md` | **MVP build spec** — what to build, how it works |
| `ROADMAP.md` | **Phased milestones** — M-2 (sqler gaps) through v0.2+ |
| `NORTH_STAR.md` | Vision, philosophy, non-negotiable principles |
| `SQLER_GAPS.md` | Blocking sqler features (prerequisite) |
| `BRAINSTORM.md` | Design exploration, competitive analysis |

**No source code exists yet.** When implementation begins, the package will live in `src/qler/`.

---

## Core Architecture Decisions

| Decision | Choice | Rationale |
|----------|--------|-----------|
| Primary key | ULID (text) | Sortable, unique, no coordination needed |
| Job claiming | Lease-based | Predictable recovery without separate coordinator |
| Delivery guarantee | At-least-once | Explicit; tasks SHOULD be idempotent |
| Storage model | Promoted columns + JSON blob | Hot fields get real columns; rest stays in sqler's document store |
| Failure tracking | `FailureKind` enum | Structured reasons prevent ghost states |
| CLI output | Human-first, `--json` flag | Readable by default, machine-parseable on demand |
| Config | Code, not YAML | Git-friendly, code-reviewable |

---

## The -ler Stack

All -ler libraries MUST integrate through their public APIs.

| Layer | Tool | Responsibility |
|-------|------|----------------|
| Storage | **sqler** | SQLite ORM, async, optimistic locking |
| Queues | **qler** | Background job execution |
| Logs | **logler** | Log aggregation, correlation IDs |
| Processes | **procler** | Process management, health checks |
| Future | **dagler** | DAG/pipeline orchestration (post qler v1.0) |

---

## Non-Negotiable Rules

### 1. Never bypass the -ler stack

- All DB operations MUST go through sqler's model API — no raw SQL
- If sqler can't express an operation, **fix sqler** — don't work around it

### 2. SQLite is the design center

- NEVER add features that "work better with Postgres"
- Use WAL mode, `UPDATE...RETURNING`, partial indexes — lean into SQLite strengths

### 3. Debuggability over features

- Every design decision MUST answer: "Does this make debugging easier?"
- Full attempt history preserved, structured failure kinds, correlation IDs

### 4. Explicit over magic

- No auto-discovery of tasks — explicit `@task` decorator required
- Workers MUST specify which queues to process

---

## Blockers (sqler Gaps)

Implementation MUST NOT begin until hard blockers are resolved. See `SQLER_GAPS.md`.

| Gap | Status | Impact |
|-----|--------|--------|
| Multi-field ordering | **Hard blocker** | Deterministic claim query |
| Promoted columns | **Hard blocker** | CHECK constraints, indexes |
| F-expressions in update | **Hard blocker** | Atomic counters |
| Atomic update-and-return | **Soft blocker** | SafeModel works but thundering herds under concurrency |

---

## Tech Stack

| Tool | Version | Purpose |
|------|---------|---------|
| Python | 3.12+ | Runtime |
| asyncio | stdlib | Async foundation |
| SQLite | WAL mode | Storage (via sqler) |
| uv | latest | Package management |
| sqler | latest | Database operations |
| click | latest | CLI |

---

## File Structure (when implementation begins)

```
src/qler/
├── __init__.py          # Public API: Queue, task, current_job
├── queue.py             # Queue class
├── task.py              # @task decorator
├── models/
│   ├── job.py           # Job model
│   └── attempt.py       # JobAttempt model
├── worker.py            # Worker loop + lease renewal
├── cli.py               # Click CLI
├── exceptions.py        # Error types
└── py.typed             # PEP 561 marker
tests/
├── unit/                # Immediate mode, fast
└── integration/         # Real worker, slow
```

---

## Active Roadmaps

- [ROADMAP.md](./ROADMAP.md) — current milestone: M-2 (sqler gaps)
