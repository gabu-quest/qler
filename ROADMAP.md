# qler Roadmap

## Milestones

### M0: sqler Gaps (prerequisite) ⬚

Resolve blocking features in sqler before qler implementation begins. See [SQLER_GAPS.md](SQLER_GAPS.md).

- Multi-field `order_by()` — deterministic claim query
- Promoted columns — real SQLite columns for hot fields
- F-expressions in `update()` — atomic counter increments
- `update_one()` returning model — race-free claiming (soft blocker, SafeModel works as fallback)

### M1: Core Library ⬚

Models, enqueue, claim, execute, complete/fail, retry. The inner loop.

- Job + JobAttempt sqler models with promoted columns
- `@task` decorator (async + sync via `to_thread`)
- `Queue` class (standalone DB or shared sqler `Database`)
- Enqueue with `_delay`, `_eta`, `_priority`, `_idempotency_key`
- Payload validation + size limits at enqueue time
- Claim via SafeModel optimistic locking
- Success completion + failure recording
- Retry with exponential backoff + jitter
- `job.wait()`, `job.cancel()`, `job.retry()`
- Exceptions: `JobFailedError`, `JobCancelledError`, `PayloadTooLargeError`, etc.
- Task identity constraint (reject nested functions, lambdas)
- Task resolution (`--module` / `--app`)
- Signature mismatch → permanent failure

**Exit criteria:** Can enqueue a job, have a worker claim and execute it, see it complete or fail with attempt history.

### M2: Worker + Lease Management ⬚

The production-ready worker loop.

- Worker loop with concurrency semaphore
- Automatic lease renewal (background asyncio task)
- Manual lease renewal escape hatch (`current_job().renew_lease()`)
- Lease expiry recovery (periodic scan)
- Graceful shutdown (SIGTERM → drain → exit)
- Worker ID format: `{hostname}:{pid}:{ulid}`

**Exit criteria:** Worker runs reliably, recovers from crashes, handles concurrent jobs, shuts down cleanly.

### M3: CLI ⬚

Human-first CLI with `--json` flag.

- `qler init` — create DB, set PRAGMAs, auto-add to .gitignore
- `qler worker` — start worker process
- `qler status` — queue depths and totals
- `qler jobs` — list/filter (--status, --since, --limit, --task)
- `qler job <id>` — detail with last attempt
- `qler attempts <id>` — full attempt history
- `qler retry` — re-enqueue by ID or filter
- `qler cancel` — cancel pending by ID or filter
- `qler purge` — delete terminal jobs older than threshold
- `qler doctor` — health checks (schema, WAL, orphaned tasks, stale jobs)
- `--json` flag on all commands
- JSON robustness (never crash on corrupt data)

**Exit criteria:** All commands work, human output is readable, `--json` output is parseable.

### M4: Testing + Polish ⬚

Test suite and pre-release polish.

- Immediate mode (`Queue(..., immediate=True)`)
- `task.run_now()` for direct execution
- Unit test suite (immediate mode)
- Integration test suite (real worker)
- Tests for: completion, retry, lease expiry, attempt history, idempotency, cancel, signature mismatch
- `pyproject.toml` with proper metadata
- `py.typed` marker (PEP 561)
- README with real examples (not aspirational)

**Exit criteria:** Test suite passes, package installable via `uv pip install`, README accurate.

---

## Post-MVP (v0.2+)

These are explicitly NOT in v0.1. Ordered by likely priority.

| Feature | Notes |
|---------|-------|
| logler integration | Correlation context in worker, `qler logs` command |
| procler integration | Health endpoint, worker process definitions |
| `update_one()` claim | Atomic claim when sqler ships the feature |
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

---

## Status Key

- ✅ Done
- 🔄 In progress
- ⬚ Not started
