# Changelog

All notable changes to qler will be documented in this file.

## [Unreleased]

Requires `sqler>=1.2026.11.2`.

### Upgrading from 0.5

- A 0.5 database is upgraded in place the first time this version opens it. The upgrade is one-way: a 0.5 process cannot start on the upgraded file, so upgrade every process that shares a file together.
- If the old file holds two active jobs under one `unique_key` or `idempotency_key`, the queue still starts and logs an error naming them. The database enforces that key only after the extra jobs are cancelled or removed.

### Fixed

- **A key makes one job.** Eight concurrent enqueues with one `idempotency_key` (or one `unique_key`) could create four jobs. Each key now has a UNIQUE index, and every enqueue of an active key returns the same job, across processes and inside one `enqueue_many` batch. Cron relies on this to fire a tick once across workers.
- **A claim is one write.** `claim_job` and `claim_jobs` run in one transaction: an error leaves every job pending, with no attempt row. A claimed job is leased for its own `lease_duration`.
- **Finishing a job is one write.** `complete_job`, `fail_job` and `cancel_job` commit the status, the waiting jobs' dependency counts or cancellations, the dead-letter move and the attempt's end together. Lease recovery does the same. Events, metrics and wake-ups follow the commit.
- **A running job keeps its lease**, and a worker told to stop ends in bounded time.
- **Shutdown no longer fails a long job for good.** A job still running after `shutdown_timeout` stays marked running and is run again when its lease ends. Before, it was failed whatever its `max_retries`.
- **A job a shutdown stopped is claimable at once when its task has ended.** It goes back to the queue as lease recovery would put it, without waiting out its lease. A task that ignores the stop, or a sync task still running in its thread, keeps its lease.
- **A job keeps its lease while work it started still runs.** A shutdown that stopped an async job awaiting `asyncio.to_thread(fn)` released the job while `fn` ran on, so a second worker could run it at the same time. A function the job handed to the loop's default thread pool (`asyncio.to_thread`, `loop.run_in_executor(None, ...)`), and a task the job started and did not wait for, now count as its work: the stop waits for both within its grace and releases the job only once they have ended. The stop still cancels only the job's own task. The worker makes its own pool the loop's default for this. A thread started with `threading.Thread`, a pool the job made itself, or a subprocess is still not seen.
- **Retry takes a job out of the dead-letter queue.** `Job.retry()` on a dead-lettered job left it pending inside the dead-letter queue, where no worker claims it. It now returns to the queue it came from.
- **The rate limiter decides and writes in one transaction.** Concurrent calls for one bucket raised `StaleVersionError` after the job was already marked running, and a clock that stepped back stored a negative token count.
- **A rate limit follows a changed rate.** A bucket kept the limit it was created with for as long as the database file lived.
- **A read gives its connection back.** Four tasks that had each read the queue depth left the pool empty, and the next enqueue hung. The worker's `/metrics` handler reads the depth on every scrape.
- **Two queues in one process keep their own jobs.** The worker's cancel check, cron's last-run lookup and every CLI command read the jobs of whichever queue was initialised last. So did a job's own methods (`cancel`, `request_cancel`, `retry`, `renew_lease`, `wait_for_dependencies`) and `set_progress` and `is_cancellation_requested`: they now use the database the job was read from or saved to. `init_db()` no longer binds the model classes to its database, so sqler's `set_db()` deprecation warning is gone. A query on the class itself (`Job.query()`, `Job.from_id(...)`) therefore raises sqler's `NotBoundError`: read through `queue.jobs`, `queue.attempts` and `queue.archive`.
- **A failed finish is logged.** A `complete_job` or `fail_job` that raised inside the worker was dropped silently.
- **Enqueue refuses a bad payload before it waits for the write lock.**
- `complete_job`, `fail_job`, `cancel_job` and `replay_job` called before `init_db()` raise `RuntimeError("Queue not initialized. Call init_db() first.")` where they raised an `AttributeError` or a `TypeError`.
- `qler jobs` and `qler status` print `1 job`, not `1 jobs`.

### Changed

- **Immediate mode runs only the jobs the call created.** An enqueue that returns an existing job for its key does not run it again.
- **One enqueue path and one claim path.** `enqueue` and `enqueue_many` share one implementation, as do `claim_job` and `claim_jobs`, so the single and the batch call check the same things in the same order. When one batch has a bad payload in one entry and a bad dependency in another, the payload error is the one raised.
- **The dependency edges are a sqler model** (`JobDependency`, table `qler_job_dependencies`), and the archive moves rows through sqler. The queue sets its database up through sqler's public calls.
- **A job with no database raises.** A `Job(...)` made in code and never enqueued, or a job that was pickled and loaded, raises sqler's `NotBoundError` from its methods, `set_progress` and `is_cancellation_requested`. Before, it used whichever queue was initialised last.
- **The command line is a package**, `qler.cli`, one module per group of commands. The commands and their output are unchanged.

### Added

- `UniqueKeyConflictError` is exported: `Job.retry()` and a dead-letter replay raise it when another active job holds the job's `unique_key`.
- `Queue.jobs`, `Queue.attempts` and `Queue.archive`: sqler querysets over this queue's own tables.
- Type checking: the package passes mypy (it ships `py.typed`), and lint covers ruff's `UP`, `SIM`, `ASYNC`, `RUF` and `B` rules.

### Removed

- `JobNotFoundError`, `TaskNotFoundError`, `SignatureMismatchError` and `ClaimConflictError`. Nothing ever raised them, so an `except` for one caught nothing. A missing task or a payload that does not fit the task's signature fails the job with `FailureKind.TASK_NOT_FOUND` or `FailureKind.SIGNATURE_MISMATCH`, as before.

## [0.5.1] — 2026-04-21

### Fixed

- **sqler compatibility** — removed inline `UNIQUE` from promoted-column definitions and restored uniqueness with explicit indexes so `Queue.init_db()` works with current `sqler` schema validation
- **rate-limit bucket schema** — renamed the promoted rate-limit column from reserved-word `key` to `bucket_key`, added a migration for legacy databases, and kept backward-compatible model access via `bucket.key`

## [0.5.0] — 2026-02-25

### Added

- **Structured lifecycle events** (M24) — `qler.lifecycle` logger emits structured log entries at every job state transition (enqueued, claimed, completed, failed, retried, lease_recovered, executed) with job_id, queue, task, correlation_id, and event-specific fields. Always-on via Python `logging`, works with logler's `JsonHandler` for queryable JSON output. Zero overhead when no handler attached.

### Fixed

- **logler integration** — `CorrelationFilter` now preserves explicitly-set `correlation_id`; `JsonHandler` forwards extra fields to JSON output
- **`__version__`** — was stuck at "0.3.0" after v0.4.0 release

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
