# Changelog

All notable changes to qler will be documented in this file.

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
