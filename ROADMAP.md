# qler Roadmap

## Milestones

### M-2: sqler Gaps (prerequisite) ✅

Resolved all blocking sqler features. See [SQLER_GAPS.md](SQLER_GAPS.md).

**Where:** `../sqler` — branch `feat/qler-prerequisites`

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

**Where:** `../logler`

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

**Where:** `../sqler` — branch `feat/qler-prerequisites`

**Root cause:** `_build_aggregate_query()` was not calling `_rewrite_promoted_refs()`, so WHERE clauses with promoted column filters used `json_extract(data, '$.status')` instead of the real `status` column. Promoted fields are stripped from the JSON blob on save, so json_extract returns NULL → zero matches.

**Fix:** Added `_rewrite_promoted_refs()` call and promoted field detection in SELECT clause to `_build_aggregate_query()` in both `async_query.py` and `query.py`. Affects all aggregate functions: `count()`, `sum()`, `avg()`, `min()`, `max()`.

**Deliverables:**
- ✅ Fix aggregate SQL generation for promoted column filters (both sync + async)
- ✅ 10 regression tests in sqler (`TestAsyncPromotedAggregates` + `TestPromotedAggregates`)
- ✅ qler's `.count()` calls (cli.py, worker.py cron scheduler) now work correctly
- ✅ All 313 qler tests pass, all 636 sqler tests pass

### M9: Cron Catchup ✅

Recover missed cron runs when the worker restarts after downtime.

- ✅ `catchup` parameter on `@cron` decorator (`False`, `"latest"`, or `int 1-100`)
- ✅ `CronWrapper._find_last_enqueued_ts()` — query DB for most recent cron job timestamp
- ✅ `CronWrapper.missed_runs()` — walk croniter forward from anchor to now
- ✅ Scheduler loop startup catchup pass — enqueue missed runs before normal scheduling
- ✅ Idempotency keys prevent duplicate catchup jobs
- ✅ `max_running` guard applies to catchup jobs (no pile-up)
- ✅ `catchup=True` rejected (unbounded catchup is always a bug)

**Exit criteria:** Worker offline for hours → restarts → missed cron runs enqueued up to catchup limit.

### M10: Job Dependencies/Chaining ✅

Job A depends on Job B completing before it can be claimed.

- ✅ `depends_on` parameter on `enqueue()` — list of job ULIDs
- ✅ `Job.dependencies` field (JSON list of ULIDs) + `pending_dep_count` promoted column
- ✅ Claim query filters out jobs with `pending_dep_count > 0` via partial index
- ✅ `_resolve_dependencies()` decrements count on completion (idempotent)
- ✅ `_cascade_cancel_dependents()` on terminal failure (retryable failures skip)
- ✅ `job.wait_for_dependencies()` with timeout, backoff, error differentiation
- ✅ Enqueue-time validation: rejects missing/failed/cancelled deps
- ✅ CLI: `qler job <id>` shows dependency status
- ✅ 32 tests

**Also fixed:** sqler `delete()` missing `_rewrite_promoted_refs` (same class of bug as M8 `.count()` fix). 6 regression tests added in sqler.

### M11: Dead Letter Queue ✅

Configurable DLQ for permanently failed jobs.

- ✅ `Queue(db, dlq="dead_letters")` — auto-move failed jobs to named queue
- ✅ `original_queue` field on Job — tracks source queue for replay
- ✅ Terminal failure (retry exhaustion) moves job to DLQ queue with `status=FAILED`
- ✅ `Queue.replay_job()` — reset FAILED→PENDING, restore to original queue
- ✅ DLQ partial index for efficient lookups
- ✅ `qler dlq` CLI command group (JSON-first, `--human` opt-in):
  - `list` — list DLQ jobs with `--limit`, `--since`, `--task` filters
  - `count` — count DLQ jobs
  - `job <id>` — full job detail
  - `replay <id>` / `replay --all` — replay back to original queue (or `--queue` override)
  - `purge --confirm` / `purge --older-than` — permanently delete DLQ jobs
- ✅ Cascade cancel still fires when job moves to DLQ
- ✅ Immediate mode respects DLQ configuration
- ✅ 49 tests (26 core + 23 CLI)

### M12: procler Integration ✅

Lightweight health endpoint for worker observability via procler.

- ✅ Health endpoint: TCP (`--health-port`) or Unix socket (`--health-socket`), opt-in only
- ✅ JSON response: status, worker_id, uptime, active_jobs, concurrency, queues, started_at
- ✅ Status values: `"healthy"` (running) or `"draining"` (shutdown signal received)
- ✅ Server stays up during drain so procler sees draining, not connection refused
- ✅ Non-`/health` paths return 404; Unix socket file cleaned up on exit
- ✅ `qler health` CLI command: queries a running worker's health endpoint (TCP or Unix)
- ✅ Human-readable + `--json` output; exit code 0 on success, 1 on failure
- ✅ 18 tests (12 worker + 6 CLI)

### M13: `qler tasks` Command ✅

List registered tasks with their configuration.

- ✅ `qler tasks` CLI command with `--app`/`--db`+`--module`, `--queue` filter, `--json`
- ✅ Shows task path, queue, retries, rate limit, cron expression, active job count
- ✅ Human table + JSON output formats
- ✅ 9 tests

### M14: v0.3.0 Release ✅

Tag v0.3.0 with M9–M13 features.

- ✅ Version bump to 0.3.0
- ✅ CHANGELOG.md v0.3.0 entry
- ✅ ROADMAP.md updated

### M15: Per-Task Idempotency Key Generators ✅

Custom idempotency key generation per task, so callers don't have to construct keys manually.

- ✅ `@task(q, idempotency_key=lambda order_id: f"charge:{order_id}")`
- ✅ Key function receives the same args/kwargs as the task
- ✅ Explicit `_idempotency_key=` on `enqueue()` takes precedence over fn
- ✅ Validation: callable check at decoration time, string check at call time
- ✅ `qler tasks --json` shows `idempotency_key` boolean field
- ✅ 8 tests

### M16: `qler backup` Command ✅

Safe online backup of the qler SQLite database.

- ✅ `qler backup --db <source> --to <destination> [--json]`
- ✅ Uses sqler's `async_backup()` (SQLite backup API via `sqlite3.backup()`)
- ✅ Safe to run while workers are active (WAL mode)
- ✅ Refuses to overwrite existing destination (safety first)
- ✅ `--json` output with BackupResult metadata (success, size, duration, paths)
- ✅ 8 tests

### M17: Job Timeouts ✅

Per-task and per-job execution timeouts to prevent hung tasks from holding concurrency slots.

- ✅ `@task(q, timeout=30)` — per-task default timeout in seconds
- ✅ `_timeout` on `.enqueue()` / `.delay()` — per-job override
- ✅ `timeout` field on Job model (nullable int)
- ✅ Worker wraps execution with `asyncio.wait_for()` (async) / `asyncio.shield()` (sync)
- ✅ `FailureKind.TIMEOUT` — retryable failure kind
- ✅ `qler tasks --json` shows timeout config
- ✅ 19 tests (8 config, 8 worker execution, 3 immediate mode)

### M18: Batch Enqueue ✅

Single-transaction bulk job creation for performance.

- ✅ `Queue.enqueue_many(jobs)` — atomic batch insert
- ✅ `TaskWrapper.enqueue_many(arg_list)` — convenience wrapper
- ✅ All-or-nothing validation (bad payload in batch → none created)
- ✅ Intra-batch dependency support
- ✅ Idempotency key checking within batches
- ✅ 15 tests (10 queue + 5 task wrapper)

### M19: Job Progress ✅

Tasks report progress for long-running operations.

- ✅ `set_progress(percent, message="")` — async function, update from inside task
- ✅ `progress` and `progress_message` fields on Job model
- ✅ `qler job <id>` shows progress in human + JSON output
- ✅ Progress reset on auto-retry, manual retry, and DLQ replay
- ✅ Validation: integer 0–100, raises outside task context
- ✅ 12 tests

### M20: Unique Jobs ✅

Prevent duplicate pending/running jobs for the same task.

- ✅ `@task(q, unique=True)` — at most one PENDING/RUNNING job per task_path
- ✅ `@task(q, unique_key=fn)` — scoped uniqueness by key function
- ✅ `unique` and `unique_key` mutually exclusive (ConfigurationError)
- ✅ Uniqueness check runs after idempotency check; returns existing active job
- ✅ Terminal states (completed/failed/cancelled) don't block new enqueues
- ✅ `enqueue_many()` respects uniqueness per-job
- ✅ `_unique_key` per-call override on `enqueue()`
- ✅ Partial index on `unique_key` for active jobs
- ✅ `qler tasks --json` shows `unique`/`unique_key` fields
- ✅ 19 tests

### M21: `qler logs` ✅

Bridge CLI to logler for job-correlated log viewing.

- ✅ `qler logs <job_id> --db <db>` — show logs matching job ULID via logler
- ✅ Searches `db_to_jsonl()` output for ULID (matches both job and attempt records)
- ✅ `--level` filter, `--limit`, `--json` output
- ✅ Graceful fallback if logler not installed (helpful error message)
- ✅ Correlation ID tip shown in human output when present
- ✅ 5 tests

### M22: Prometheus Metrics ✅

Optional metrics export for production observability.

- ✅ Optional `prometheus_client` dependency (`qler[metrics]`)
- ✅ `Queue(metrics=True)` opt-in with private `CollectorRegistry`
- ✅ Also accepts `CollectorRegistry` instance for shared registries
- ✅ Counters: enqueued, claimed, completed, failed (by queue/task/failure_kind), retried, leases_recovered
- ✅ Gauge: queue depth (by queue/status) — async DB query refreshed on each scrape
- ✅ Histogram: job duration (by queue/task) with sub-second to 10-minute buckets
- ✅ `/metrics` endpoint on health server (Prometheus text format)
- ✅ Zero overhead when disabled (no `prometheus_client` import)
- ✅ 20 tests

### M23: v0.4.0 Release ✅

Tag v0.4.0 with M15–M22 features.

- ✅ Version bump to 0.4.0
- ✅ CHANGELOG.md v0.4.0 entry
- ✅ ROADMAP.md updated

### M24: Structured Lifecycle Events (logler Integration) ✅

Emit structured log entries at every job lifecycle transition, aligned with Prometheus metric labels, so logler can query and correlate metric events without Prometheus.

- ✅ `qler.lifecycle` logger with `emit()` helper — standard fields (event, job_id, queue, task, correlation_id) + per-event extras
- ✅ Always-on: fires via Python `logging` regardless of Prometheus config (~200ns overhead when no handler attached)
- ✅ 7 events: `job.enqueued`, `job.claimed`, `job.completed`, `job.failed`, `job.retried`, `job.lease_recovered`, `job.executed`
- ✅ Labels aligned with Prometheus counters (queue, task, failure_kind, duration)
- ✅ job_id and correlation_id on every event for logler correlation
- ✅ logler fix: CorrelationFilter preserves explicit correlation_id; JsonHandler forwards extra fields to JSON
- ✅ 15 tests

### M25: v0.5.0 Release ✅

Tag v0.5.0 with M24.

- ✅ Version bump to 0.5.0 (pyproject.toml + __init__.py)
- ✅ CHANGELOG.md v0.5.0 entry
- ✅ ROADMAP.md updated

### M26: Production Readiness (lerproof M5 items) ✅

Three production readiness features identified by lerproof M5 integration testing.

- ✅ Archival CLI flags — `--archive-interval` / `--archive-after` on `qler worker`
- ✅ Pool health check — `Queue.pool_health()` + `pool` field in Worker `/health` endpoint
- ✅ Memory watchdog — `--memory-limit-mb` triggers emergency archival when RSS exceeds limit
- ✅ Input validation — lower-bound guards on all 3 new Worker params
- ✅ macOS RSS fix — correct bytes-vs-KB normalization for `ru_maxrss`
- ✅ Pool health tests — concrete assertions, unhealthy-pool scenario coverage

---

## Separate Projects

| Project | Description |
|---------|-------------|
| **qler-web** | Web dashboard (Vue 3 + Naive UI). Separate Python package depending on qler. Follows logler-web pattern. |

---

## Status Key

- ✅ Done
- 🔄 In progress
- ⬚ Not started
