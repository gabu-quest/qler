# Claude Context: qler

**Version:** see `pyproject.toml`
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

Fully implemented. The version is in `pyproject.toml`; what each release added is in `CHANGELOG.md`.

| File | Purpose |
|------|---------|
| `CHANGELOG.md` | Release history |
| `NORTH_STAR.md` | Vision, philosophy, non-negotiable principles |
| `README.md` | User-facing introduction |
| `specs/performance-upgrades.md` | Spec for the performance upgrades |
| `benchmarks/` | Benchmark suites and the Celery comparison |

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

---

## Non-Negotiable Rules

### 1. Never bypass the -ler stack

- Database work goes through sqler's public calls (models, querysets, `db.transaction()`). Raw SQL text is only for the schema upgrade steps and the `qler doctor` checks, where sqler has no call, and each such place says so in a comment.
- If sqler can't express an operation, **fix sqler** — don't work around it

### 2. SQLite is the design center

- NEVER add features that "work better with Postgres"
- Use WAL mode and partial indexes — lean into SQLite strengths

### 3. Debuggability over features

- Every design decision MUST answer: "Does this make debugging easier?"
- Full attempt history preserved, structured failure kinds, correlation IDs

### 4. Explicit over magic

- No auto-discovery of tasks — explicit `@task` decorator required
- A worker takes an explicit list of queues (`--queues`, default `default`)

---

## Tech Stack

Versions are in `pyproject.toml`.

| Tool | Purpose |
|------|---------|
| Python 3.12+ | Runtime |
| asyncio | Async foundation |
| SQLite (WAL mode) | Storage, through sqler |
| uv | Package management |
| sqler | Database operations |
| click | CLI |
| croniter | Cron expression parsing |
| python-ulid | ULID generation |

---

## Checks

Run these from the repository root before you hand work back:

```
uv run ruff check .
uv run ruff format --check .
uv run mypy
uv run pytest -q
```

---

## File Structure

```
src/qler/
├── __init__.py          # Public API and __all__
├── queue.py             # Queue: enqueue, claim, complete, fail
├── task.py              # @task decorator and TaskWrapper
├── cron.py              # @cron decorator and CronWrapper
├── worker.py            # Worker: claims jobs, runs tasks, manages leases
├── rate_limit.py        # Token bucket rate limiting
├── lifecycle.py         # Structured lifecycle events for job state changes
├── metrics.py           # Prometheus metrics (Queue(metrics=True))
├── enums.py             # JobStatus, AttemptStatus, FailureKind
├── exceptions.py        # QlerError hierarchy
├── _context.py          # current_job() and the other context variables
├── _notify.py           # In-process wake-up for job.wait()
├── _schema.py           # Schema set-up and upgrades: ensure_schema(db)
├── _sqler.py            # Private helpers for what sqler's annotations do not say
├── _time.py             # Time utilities
├── py.typed             # PEP 561 marker
├── cli/
│   ├── __init__.py      # Imports the modules so their commands register
│   ├── group.py         # The qler command group
│   ├── helpers.py       # Shared helpers and constants
│   ├── database.py      # init, backup
│   ├── workers.py       # worker, health
│   ├── reports.py       # status, jobs, job, attempts
│   ├── control.py       # retry, cancel
│   ├── deadletter.py    # dlq commands
│   ├── maintenance.py   # archive, purge, doctor
│   ├── schedules.py     # cron, tasks
│   └── logs.py          # logs
└── models/
    ├── job.py           # Job: primary queue item, state machine
    ├── attempt.py       # JobAttempt: audit record per execution attempt
    ├── bucket.py        # RateLimitBucket: token bucket state
    └── dependency.py    # JobDependency: which jobs wait on this one
tests/
├── conftest.py              # Shared fixtures
├── test_scaffold.py         # Package structure and public API
├── test_models.py           # Job and JobAttempt fields, defaults, properties
├── test_queue.py            # Queue init, enqueue, claim, complete, fail
├── test_task.py             # @task and TaskWrapper
├── test_lifecycle.py        # End-to-end job flows
├── test_lifecycle_events.py # Structured lifecycle events
├── test_worker.py           # Worker init, execution, concurrency, shutdown
├── test_batch_claim.py      # Batch claiming and dispatch
├── test_lease.py            # Lease recovery and renewal
├── test_immediate.py        # Immediate mode
├── test_cancellation.py     # Cooperative cancellation
├── test_cron.py             # @cron and the scheduler loop
├── test_rate_limit.py       # Rate limiting
├── test_dependencies.py     # Job dependencies and chaining
├── test_dep_index.py        # Reverse dependency table
├── test_dlq.py              # Dead letter queue
├── test_unique.py           # Unique jobs
├── test_progress.py         # set_progress and progress fields
├── test_archival.py         # Job archival
├── test_pool_health.py      # Connection pool health
├── test_metrics.py          # Prometheus metrics
├── test_notify.py           # job.wait() wake-up registry
├── test_context.py          # current_job() context access
└── test_cli.py              # CLI commands
```
