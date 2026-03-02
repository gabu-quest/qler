# Claude Context: qler

**Version:** 0.5.0
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

**Fully implemented.** v0.5.0 with timeouts, batch enqueue, progress, unique jobs, Prometheus metrics, logler logs CLI, structured lifecycle events. Production readiness features (pool health, archival CLI, memory watchdog) shipped in M26. 667+ passing tests.

| File | Purpose |
|------|---------|
| `ROADMAP.md` | **Milestone tracker** — M-2 through M14 complete |
| `CHANGELOG.md` | Release history (v0.1.0, v0.2.0, v0.3.0) |
| `NORTH_STAR.md` | Vision, philosophy, non-negotiable principles |
| `SPEC.md` | v0.1.0 MVP build spec (historical reference) |
| `SQLER_GAPS.md` | sqler prerequisites (historical — all resolved) |
| `BRAINSTORM.md` | Early design exploration (historical reference) |

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

## Tech Stack

| Tool | Version | Purpose |
|------|---------|---------|
| Python | 3.12+ | Runtime |
| asyncio | stdlib | Async foundation |
| SQLite | WAL mode | Storage (via sqler) |
| uv | latest | Package management |
| sqler | latest | Database operations |
| click | latest | CLI |
| croniter | latest | Cron expression parsing |
| python-ulid | latest | ULID generation |

---

## File Structure

```
src/qler/
├── __init__.py          # Public API: Queue, Worker, task, cron, current_job
├── queue.py             # Queue class — enqueue, claim, complete, fail, retry
├── task.py              # @task decorator, TaskWrapper
├── cron.py              # @cron decorator, CronSchedule, CronWrapper
├── worker.py            # Worker loop, lease renewal, recovery, cron scheduler
├── rate_limit.py        # RateSpec, token bucket rate limiting
├── cli.py               # Click CLI (init, worker, status, jobs, cron, etc.)
├── models/
│   ├── job.py           # Job model (sqler AsyncSQLerSafeModel)
│   ├── attempt.py       # JobAttempt model
│   └── bucket.py        # RateLimitBucket model
├── enums.py             # JobStatus, AttemptStatus, FailureKind
├── exceptions.py        # Error types (QlerError hierarchy)
├── _context.py          # current_job() context variable
├── _notify.py           # In-process asyncio.Event notification for instant wait() wakeup
├── _time.py             # Timestamp utilities
└── py.typed             # PEP 561 marker
tests/
├── conftest.py          # Shared fixtures
├── test_scaffold.py     # Imports, public API surface
├── test_models.py       # Job, JobAttempt, RateLimitBucket
├── test_queue.py        # Enqueue, claim, payload validation
├── test_task.py         # @task decorator, run_now()
├── test_lifecycle.py    # Complete/fail/retry/cancel/wait
├── test_worker.py       # Worker loop, concurrency, shutdown
├── test_lease.py        # Lease renewal, recovery, expiry
├── test_immediate.py    # Immediate mode
├── test_cancellation.py # Cooperative cancellation
├── test_cron.py         # @cron scheduler, dedup
├── test_rate_limit.py   # Token bucket, requeue
├── test_notify.py       # Event notification registry + integration
├── test_cli.py          # All CLI commands
└── test_context.py      # current_job context
```

---

## Active Roadmaps

- [ROADMAP.md](./ROADMAP.md) — M0–M26 complete (v0.5.0 + production readiness).
