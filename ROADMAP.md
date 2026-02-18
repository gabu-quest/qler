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

### M2: Worker + Lease Management ✅

The production-ready worker loop.

- Worker loop with concurrency semaphore
- Automatic lease renewal (background asyncio task)
- Manual renewal escape hatch (`current_job().renew_lease()`)
- Lease expiry recovery (periodic scan)
- Graceful shutdown (SIGTERM → drain → exit)
- logler correlation context wrapping job execution

**Exit criteria:** Worker runs reliably, recovers from crashes, handles concurrent jobs, shuts down cleanly, logs are correlated.

### M3: CLI ✅

Human-first CLI with `--json` flag.

- `qler init` — create DB, PRAGMAs, auto-gitignore
- `qler worker` — start worker (--app/--module, --queues, --concurrency)
- `qler status` — queue depths
- `qler jobs` / `qler job <id>` / `qler attempts <id>` — list/detail
- `qler retry` / `qler cancel` / `qler purge` — bulk operations
- `qler doctor` — health checks
- Human-first output, `--json` flag on all commands

**Exit criteria:** All commands work, human output readable, --json parseable.

### M4: Testing + Polish ✅

Test suite and pre-release polish.

- Immediate mode (`Queue(..., immediate=True)`)
- `task.run_now()` for direct execution
- Unit tests (immediate mode)
- Integration tests (real worker)
- `pyproject.toml` metadata finalized
- README with real (not aspirational) examples

**Exit criteria:** Tests pass, `uv pip install .` works, README accurate.

### M5: v0.1.0 Tag, Hardening, Cooperative Cancellation ✅

Tag MVP, harden edge cases, implement first post-MVP feature.

- v0.1.0 tag on `feat/m4-polish`
- `CHANGELOG.md` with v0.1.0 entry
- Hardened `_execute_immediate` — try/finally attempt finalization
- Hardening tests: retry exhaustion, idempotency after cancellation
- Cooperative cancellation: `cancel_requested` field, `request_cancel()`, `is_cancellation_requested()`
- CLI `--running` flag on `qler cancel` command
- 10+ cancellation tests

**Exit criteria:** All tests pass, cooperative cancellation works end-to-end.

### M6: Periodic/Cron Tasks ✅

Declarative cron scheduling without external schedulers.

- `@cron` decorator wraps `@task` with cron scheduling metadata
- `CronSchedule` dataclass (expression, max_running, timezone)
- `CronWrapper` delegates to inner `TaskWrapper` (enqueue, run_now, __call__)
- Worker `_cron_scheduler_loop()` background task with idempotency keys
- `max_running` guard prevents job pile-up
- `qler cron` CLI command lists registered cron tasks
- `croniter` dependency for cron expression parsing
- 16 tests

**Exit criteria:** `@cron(q, "*/5 * * * *")` enqueues jobs on schedule, dedup prevents duplicates.

### M7: Rate Limiting ✅

Token bucket rate limiting for tasks and queues.

- `RateSpec` dataclass with `parse_rate()` ("10/m", "100/h", "5/s", "1000/d")
- `RateLimitBucket` sqler model for token state persistence
- `try_acquire()` token bucket with refill-on-access
- Task-level: `@task(q, rate_limit="10/m")`
- Queue-level: `Queue(db, rate_limits={"emails": "100/h"})`
- Rate-limited jobs requeued with delayed ETA (not failed)
- Immediate mode bypasses rate limiting
- 17 tests

**Exit criteria:** Rate-limited claims requeue jobs; tokens refill over time.

---

## Next Up (v0.3)

### M8: Fix sqler `.count()` bug ✅

**Where:** `/home/gabu/projects/pypi/sqler/` — branch `fix/aggregate-promoted-rewrite`

**Root cause:** `_build_aggregate_query()` was not calling `_rewrite_promoted_refs()`, so WHERE clauses with promoted column filters used `json_extract(data, '$.status')` instead of the real `status` column. Promoted fields are stripped from the JSON blob on save, so json_extract returns NULL → zero matches.

**Fix:** Added `_rewrite_promoted_refs()` call to `_build_aggregate_query()` in both `async_query.py` and `query.py`. Affects all aggregate functions: `count()`, `sum()`, `avg()`, `min()`, `max()`.

**Deliverables:**
- ✅ Fix `count()` SQL generation for mixed promoted + JSON field filters
- ✅ 4 regression tests in sqler (`test_async_promoted.py::TestAsyncPromotedAggregates`)
- ✅ Removed `len(await ...all())` workarounds in qler (3 places)
- ✅ Fix `delete()` SQL generation (same missing `_rewrite_promoted_refs()` bug)
- ✅ 2 regression tests in sqler (`test_async_promoted.py::TestAsyncPromotedDelete`)
- ✅ Removed raw SQL `DELETE` workaround in qler purge command
- ✅ Replaced raw SQL `SELECT COUNT(*)` in doctor command with ORM `.count()`

### M9: Job Dependencies/Chaining ✅

Job A depends on Job B completing before it can be claimed.

- ✅ `depends_on` parameter on `enqueue()` — list of job ULIDs
- ✅ `Job.dependencies` field (JSON list of ULIDs)
- ✅ `pending_dep_count` promoted column — claim query filters `pending_dep_count = 0`
- ✅ `_resolve_dependencies()` — atomic decrement on completion
- ✅ `_cascade_cancel_dependents()` — recursive cancellation on terminal failure
- ✅ `Queue.cancel_job()` — cancel with cascade
- ✅ `DependencyError` exception for invalid deps (missing, failed, cancelled)
- ✅ `job.wait_for_dependencies()` polling helper
- ✅ `TaskWrapper.enqueue(_depends_on=...)` forwarding
- ✅ CLI: `qler job <id>` shows dependency status, `qler cancel` uses cascade
- ✅ Schema migration for existing databases (idempotent ALTER TABLE)
- ✅ 31 tests

### M10: Dead Letter Queue ⬚

Configurable DLQ for permanently failed jobs.

- `Queue(db, dlq="dead_letters")` — auto-move failed jobs to named queue
- `max_retries` exhaustion triggers DLQ move instead of terminal FAILED
- `qler dlq` CLI command — list, inspect, replay from DLQ
- DLQ jobs preserve full attempt history

### M11: procler Integration ⬚

Health endpoint, worker process definitions.

- procler process definition for `qler worker`
- Health check endpoint (HTTP or Unix socket)
- Worker heartbeat reporting
- `procler` manages worker lifecycle (start, stop, restart)

---

## Future (v0.4+)

| Feature | Notes |
|---------|-------|
| Web dashboard | Vue 3 + Naive UI (read-only + operational) |
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
