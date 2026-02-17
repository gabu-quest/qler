# qler Roadmap

## Milestones

### M-2: sqler Gaps (prerequisite) ✅

Resolved all blocking sqler features. See [SQLER_GAPS.md](SQLER_GAPS.md).

**Where:** `/home/gabu/projects/pypi/sqler/` — branch `feat/qler-prerequisites`

- ✅ Multi-field `order_by("-priority", "eta", "ulid")` — Django-style, `-` prefix = DESC
- ✅ Promoted columns (`__promoted__`, `__checks__`) — real SQLite columns, CHECK constraints, SQL rewriting
- ✅ F-expressions in `update(attempts=F("attempts") + 1)` — atomic increments via `SQLerUpdateExpression`
- ✅ `update_one()` with `RETURNING` — atomic update-and-return, race-free claiming

**Exit criteria met:** qler's claim query is now fully expressible:
```python
Job.query().filter(...).order_by("-priority", "eta", "ulid").update_one(
    status="running", attempts=F("attempts") + 1
)
```

### M-1: logler-sqler Bridge ✅

Give logler the ability to ingest directly from sqler SQLite databases — no manual file exports, no temp file management by the user. logler handles everything behind the scenes.

**Where:** `/home/gabu/projects/logler/`

**Database source (the main bridge):**
- `Investigator.load_from_db(db_path)` — new data source that reads sqler SQLite tables directly
- Map sqler model rows → LogEntry format (timestamps, levels, messages extracted from JSON data column)
- CLI: `logler llm search --db path/to/app.db` (or similar flag) — user just points at a DB
- If Rust/DuckDB need intermediate files internally, logler handles that transparently

**Correlation context (runtime log emission):**
- `logler.correlation_context(id)` — ContextVar-based context manager
- `logler.CorrelationFilter` — Python logging filter that injects correlation_id into log records
- `logler.JsonHandler` — logging handler that emits JSON structured logs logler can parse

**Exit criteria:**
- `logler llm search --db qler.db` → shows job attempts, failures, etc. without manual steps
- `with logler.correlation_context("job-123"): logger.info("hello")` → logs with correlation_id → searchable via logler CLI
- logler can correlate DB records + runtime logs for end-to-end job investigation

### M0: Project Scaffold ✅

Minimal package structure so `import qler` works.

- `pyproject.toml` (uv, PEP 621, entry point `qler`)
- `src/qler/__init__.py` — public API exports
- `src/qler/exceptions.py` — error types
- `tests/conftest.py` — shared fixtures
- `uv init` + `uv sync`

**Exit criteria:** `uv run python -c "import qler"` works.

### M1: Core Library ✅

Models, enqueue, claim, execute, complete/fail, retry. The inner loop.

- Enums: `JobStatus`, `AttemptStatus`, `FailureKind`
- Models: `Job`, `JobAttempt` (sqler models with promoted columns)
- `Queue` class (standalone DB or shared sqler `Database`)
- `@task` decorator (async + sync via `to_thread`)
- Enqueue with `_delay`, `_eta`, `_priority`, `_idempotency_key`
- Payload validation + size limits
- Claim via SafeModel (upgrade to `update_one` when available)
- Complete / fail / retry operations
- `job.wait()`, `job.cancel()`, `job.retry()`

**Exit criteria:** Can enqueue → claim → execute → complete/fail with attempt history.

### M2: Worker + Lease Management 🔄 ← current

The production-ready worker loop.

- Worker loop with concurrency semaphore
- Automatic lease renewal (background asyncio task)
- Manual renewal escape hatch (`current_job().renew_lease()`)
- Lease expiry recovery (periodic scan)
- Graceful shutdown (SIGTERM → drain → exit)
- logler correlation context wrapping job execution

**Exit criteria:** Worker runs reliably, recovers from crashes, handles concurrent jobs, shuts down cleanly, logs are correlated.

### M3: CLI ⬚

Human-first CLI with `--json` flag.

- `qler init` — create DB, PRAGMAs, auto-gitignore
- `qler worker` — start worker (--app/--module, --queues, --concurrency)
- `qler status` — queue depths
- `qler jobs` / `qler job <id>` / `qler attempts <id>` — list/detail
- `qler retry` / `qler cancel` / `qler purge` — bulk operations
- `qler doctor` — health checks
- Human-first output, `--json` flag on all commands

**Exit criteria:** All commands work, human output readable, --json parseable.

### M4: Testing + Polish ⬚

Test suite and pre-release polish.

- Immediate mode (`Queue(..., immediate=True)`)
- `task.run_now()` for direct execution
- Unit tests (immediate mode)
- Integration tests (real worker)
- `pyproject.toml` metadata finalized
- README with real (not aspirational) examples

**Exit criteria:** Tests pass, `uv pip install .` works, README accurate.

---

## Post-MVP (v0.2+)

These are explicitly NOT in v0.1. Ordered by likely priority.

| Feature | Notes |
|---------|-------|
| procler integration | Health endpoint, worker process definitions |
| Cooperative cancellation | `cancel_requested` flag for running jobs |
| Periodic/cron tasks | `@cron` decorator |
| Web dashboard | Vue 3 + Naive UI (read-only + operational) |
| Rate limiting | Per-queue or per-task |
| Job dependencies/chaining | Job A depends on Job B |
| Dead letter queue | Configurable DLQ for permanently failed jobs |
| Prometheus metrics | Export queue depths, throughput, failure rates |
| Payload encryption | Optional encryption for sensitive fields |
| Per-task idempotency key generators | `idempotency_key=lambda order_id: f"charge:{order_id}"` |
| `qler backup` command | Safe backup via SQLite backup API |
| `qler tasks` command | List registered tasks with config |
| logler db_source input validation | Sanitize user-supplied table names beyond SQL quoting; allowlist approach |
| logler JsonHandler robustness | Graceful degradation when stream write fails mid-entry |
| logler db_source temp file cleanup | Context manager API for automatic temp file cleanup on exception paths |
| logler correlation context OTel bridge | Optional trace_id propagation from OpenTelemetry spans |

---

## Status Key

- ✅ Done
- 🔄 In progress
- ⬚ Not started
