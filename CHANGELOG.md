# Changelog

All notable changes to qler will be documented in this file.

## [0.4.0] — 2026-02-25

### Added

- **Job timeouts** (M17) — `@task(q, timeout=30)` per-task and `_timeout` per-job execution timeouts with `FailureKind.TIMEOUT`
- **Batch enqueue** (M18) — `Queue.enqueue_many()` for atomic bulk job creation with intra-batch dependencies
- **Job progress** (M19) — `set_progress(percent, message)` from inside tasks, visible in CLI and API
- **Unique jobs** (M20) — `@task(q, unique=True)` and `unique_key=fn` prevent duplicate pending/running jobs
- **`qler logs` command** (M21) — bridge CLI to logler for job-correlated log viewing
- **Prometheus metrics** (M22) — opt-in `Queue(metrics=True)` with counters, histograms, gauges, and `/metrics` endpoint
- **Per-task idempotency key generators** (M15) — `@task(q, idempotency_key=lambda order_id: f"charge:{order_id}")`
- **`qler backup` command** (M16) — safe online backup via SQLite backup API

## [0.3.0] — 2026-02-23

### Added

- **Cron catchup** (M9) — recover missed cron runs on worker restart (`catchup="latest"` or `catchup=N`)
- **Job dependencies** (M10) — `depends_on` parameter for job chaining with cascading cancel
- **Dead letter queue** (M11) — auto-move failed jobs, replay, `qler dlq` CLI command group
- **procler integration** (M12) — health endpoint for worker observability (`--health-port`, `--health-socket`)
- **`qler tasks` command** (M13) — list registered tasks with config, rate limits, cron, active job counts

## [0.2.0] — 2026-02-18

### Added

- **`@cron` decorator** — declarative periodic tasks with cron expressions (via `croniter`)
- **`CronWrapper`** — wraps `TaskWrapper` with scheduling metadata, delegates enqueue/run/call
- **Worker cron scheduler loop** — background task enqueues cron jobs at scheduled times
- **Cron idempotency keys** — `cron:{task_path}:{timestamp}` prevents duplicate scheduling
- **`max_running` guard** — prevents cron job pile-up when previous runs are still active
- **`qler cron` CLI command** — lists registered cron tasks with schedules and active counts
- **Token bucket rate limiting** — `@task(q, rate_limit="10/m")` for per-task rate limits
- **Queue-level rate limits** — `Queue(db, rate_limits={"emails": "100/h"})`
- **`RateLimitBucket` model** — persistent token state with refill-on-access
- **Rate-limited requeue** — exceeded limits requeue jobs with delayed ETA instead of failing
- **Immediate mode bypass** — rate limits are skipped in immediate mode for testing

## [0.1.0] — 2026-02-18

Initial MVP release. Background jobs without Redis, built on SQLite via sqler.

### Added

- **Queue** class — async job queue backed by SQLite, standalone or shared DB
- **@task decorator** — register async and sync (via `to_thread`) functions
- **Job model** — ULID primary keys, promoted columns, optimistic locking
- **JobAttempt model** — full attempt history with structured failure tracking
- **Worker** — concurrent claim loop, semaphore-gated, signal-aware shutdown
- **Lease management** — automatic renewal, expiry recovery, background scanning
- **Retry system** — exponential backoff + jitter, configurable per-task
- **Immediate mode** — `Queue(immediate=True)` for testing without a worker
- **`task.run_now()`** — direct execution with payload validation
- **Idempotency keys** — deduplicate enqueue calls
- **Correlation IDs** — end-to-end tracing with logler integration
- **Payload validation** — size limits, JSON-serializability checks
- **CLI** — 10 commands with human-first output and `--json` flag:
  - `qler init` — create DB, set PRAGMAs, auto-gitignore
  - `qler worker` — start worker (`--app`, `--module`, `--queues`, `--concurrency`)
  - `qler status` — queue depths by status
  - `qler jobs` — list with filters (`--status`, `--queue`, `--task`, `--since`)
  - `qler job <id>` — detailed job view
  - `qler attempts <id>` — attempt history
  - `qler retry` — retry failed/cancelled jobs (single or `--all`)
  - `qler cancel` — cancel pending jobs (single or `--all`)
  - `qler purge` — delete terminal jobs older than threshold
  - `qler doctor` — health checks (schema, WAL, leases, stale jobs)
- **Enums** — `JobStatus`, `AttemptStatus`, `FailureKind` with structured failure kinds
- **Exception hierarchy** — typed errors for every failure mode
