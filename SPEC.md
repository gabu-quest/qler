# qler — MVP Build Spec

**Version:** 0.1.0
**Status:** Build-ready (sqler gaps resolved)
**Date:** 2026-02-16

---

## Overview

qler is an async-first background job queue for Python, built on SQLite via sqler.

**One sentence:** "Background jobs without Redis, with first-class debugging."

For vision, philosophy, and positioning see [NORTH_STAR.md](NORTH_STAR.md).
For sqler prerequisites see [SQLER_GAPS.md](SQLER_GAPS.md).

---

## Design Principles

1. **Async-native** — asyncio first, sync compatibility layer second
2. **SQLite-native** — Not "SQLite as fallback", SQLite as the design center
3. **sqler-native** — All DB operations through sqler's model API. No raw SQL. If sqler can't do it, fix sqler.
4. **Debuggability > Features** — "Why did this fail?" should be trivial to answer
5. **At-least-once delivery** — Be explicit; encourage idempotent tasks
6. **Lease-based claiming** — Predictable failure recovery, no separate coordinator
7. **Human-first CLI** — Readable text output by default, `--json` flag for machine consumption
8. **Git-friendly** — All config as code, no UI-only settings, auto-gitignore DB files

---

## sqler Dependencies

qler uses sqler's native API for ALL database operations. See [SQLER_GAPS.md](SQLER_GAPS.md) for details.

| Feature | Status | Used For |
|---------|--------|----------|
| `AsyncSQLerSafeModel` | **Exists** | Job/Attempt models with optimistic locking |
| `F()` expressions in filter | **Exists** | All queries |
| `db.transaction()` | **Exists** | Atomic multi-model operations |
| Multi-field `order_by()` | **Exists** | Claim query ordering |
| Promoted columns | **Exists** | Real columns for hot fields |
| `F()` in `update()` | **Exists** | Atomic counter increments |
| `update_one()` returning model | **Exists** | Race-free claiming without thundering herd |

**Note:** API signatures for gap features are aspirational. Semantics are stable; method names may evolve.

---

## Delivery Semantics

**qler provides at-least-once delivery.**

- A job will be executed *at least* once
- A job *may* be executed more than once (worker crash, lease expiry)
- Tasks SHOULD be idempotent or handle duplicates gracefully

### Idempotency Keys (MVP)

Basic dedup guard at enqueue time. Prevents the most common at-least-once pain (double-charges, duplicate emails).

```python
@task(queue)
async def charge_payment(order_id: int):
    ...

# With idempotency key — rejects if key exists within dedupe window
job = await charge_payment.enqueue(
    order_id=123,
    _idempotency_key=f"charge:{123}",
)
# Returns existing Job if key already exists (no duplicate enqueue)
```

**Implementation:** Before inserting, query `Job.query().filter(F("idempotency_key") == key & F("status") != "cancelled")`. If found, return existing job. Key is stored as a JSON field (not promoted — dedup queries are infrequent relative to claim queries).

**Scope for MVP:**
- Key uniqueness enforced at enqueue time only
- No automatic TTL/expiry on keys (purge handles cleanup)
- No per-task key generators (pass `_idempotency_key` explicitly)

---

## Data Model

### Enums

```python
from enum import Enum

class JobStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"

class AttemptStatus(str, Enum):
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    LEASE_EXPIRED = "lease_expired"

class FailureKind(str, Enum):
    EXCEPTION = "exception"              # Task raised (retryable)
    LEASE_EXPIRED = "lease_expired"      # Worker died or too slow
    TASK_NOT_FOUND = "task_not_found"    # Module/function missing (permanent)
    SIGNATURE_MISMATCH = "signature_mismatch"  # Args don't match (permanent)
    PAYLOAD_INVALID = "payload_invalid"  # Corrupt/malformed payload (permanent)
    CANCELLED = "cancelled"              # Explicitly cancelled (permanent)

RETRYABLE_FAILURES = {FailureKind.EXCEPTION}
```

### Job Model

```python
class Job(AsyncSQLerSafeModel):
    """Background job record.

    Only query-hot fields are promoted to real SQLite columns.
    Everything else stays in the JSON blob.
    Promoted fields are column-only — excluded from JSON, same as _id/_version.
    """

    __promoted__ = {
        "ulid": "TEXT UNIQUE NOT NULL",
        "status": "TEXT NOT NULL DEFAULT 'pending'",
        "queue_name": "TEXT NOT NULL DEFAULT 'default'",
        "priority": "INTEGER NOT NULL DEFAULT 0",
        "eta": "INTEGER NOT NULL",
        "lease_expires_at": "INTEGER",
    }

    __checks__ = {
        "status": "status IN ('pending','running','completed','failed','cancelled')",
    }

    # --- Promoted (column only, not in JSON) ---
    ulid: str                                # ULID primary identifier
    status: str = "pending"
    queue_name: str = "default"
    priority: int = 0                        # Higher = claimed first
    eta: int = 0                             # Unix epoch seconds (UTC), never NULL
    lease_expires_at: int | None = None

    # --- JSON blob ---
    task: str = ""                           # e.g. "myapp.tasks.send_email"
    worker_id: str | None = None
    lease_duration: int = 300                # seconds

    payload_json: str = "{}"                 # {"args": [...], "kwargs": {...}}
    result_json: str | None = None
    last_error: str | None = None
    last_failure_kind: str | None = None

    # Counters (see "Attempts vs Retries" below)
    attempts: int = 0                        # Total claims (including lease recovery)
    retry_count: int = 0                     # Only task failures (retry budget)
    max_retries: int = 0
    retry_delay: int = 60                    # Seconds, must be >= 1

    last_attempt_id: str | None = None       # ULID of latest attempt
    correlation_id: str = ""                 # For logler
    idempotency_key: str | None = None       # Dedup guard

    # Timestamps (Unix epoch seconds, UTC)
    created_at: int = 0
    updated_at: int = 0
    finished_at: int | None = None
```

**ID choice:** ULIDs over UUIDs — time-sorted, so `qler jobs` output is naturally chronological.

**ETA never NULL:** Defaults to `now()` at enqueue. Claim query is simple `eta <= now()` with no OR.

**Attempts vs Retries — two separate counters:**

| Counter | Incremented by | Used for |
|---------|----------------|----------|
| `attempts` | Every claim (including lease recovery) | Debugging, audit trail |
| `retry_count` | Only task failures that schedule retries | Retry budget, backoff exponent |

Lease expiry increments `attempts` but NOT `retry_count`. Without this separation, a job with `max_retries=0` would fail permanently after one innocent lease expiry (GC pause, laptop sleep).

### JobAttempt Model

```python
class JobAttempt(AsyncSQLerSafeModel):
    """Record of a single job execution attempt."""

    __promoted__ = {
        "ulid": "TEXT UNIQUE NOT NULL",
        "job_ulid": "TEXT NOT NULL",
        "status": "TEXT NOT NULL DEFAULT 'running'",
    }

    __checks__ = {
        "status": "status IN ('running','completed','failed','lease_expired')",
    }

    # --- Promoted ---
    ulid: str
    job_ulid: str                            # References Job.ulid
    status: str = "running"

    # --- JSON blob ---
    attempt_number: int = 0
    worker_id: str = ""
    started_at: int = 0
    finished_at: int | None = None

    failure_kind: str | None = None
    error: str | None = None
    traceback: str | None = None
    lease_expires_at: int | None = None
```

### Indexes

```python
# 1. Claim query: (queue_name, priority DESC, eta, ulid) WHERE status = 'pending'
# 2. Lease expiry: (lease_expires_at) WHERE status = 'running'
# 3. Correlation: json_extract(data, '$.correlation_id')
# 4. Attempt lookup: (job_ulid) on JobAttempt
# 5. Idempotency: json_extract(data, '$.idempotency_key') WHERE idempotency_key IS NOT NULL
```

### Time Model

All timestamps are **Unix epoch seconds (INTEGER), stored in UTC.**

- Faster comparisons than string parsing
- No timezone ambiguity
- CLI displays ISO 8601 UTC with `Z` suffix
- All timing values (`retry_delay`, `lease_duration`, `eta`) are integer seconds. Sub-second not supported.

```python
import time
def now_epoch() -> int:
    return int(time.time())
```

---

## State Invariants

These MUST hold at all times. Enforced by qler logic, validated by `qler doctor`.

| Status | `worker_id` | `lease_expires_at` | `finished_at` |
|--------|-------------|--------------------|--------------------|
| `pending` | NULL | NULL | NULL |
| `running` | NOT NULL | NOT NULL | NULL |
| `completed` | NULL | NULL | NOT NULL |
| `failed` | NULL | NULL | NOT NULL |
| `cancelled` | NULL | NULL | NOT NULL |

**Attempt invariants:**
- Each attempt transitions exactly once from `running` to a terminal status
- Updates MUST include `WHERE status='running'` guards

**Transactional consistency:**
- Job update + attempt terminalization MUST occur in the same transaction

**Terminal immutability:**
- Once terminal, `payload_json`, `task`, `created_at`, and all attempt rows are immutable
- Only `job.retry()` can reset a failed job to `pending` (new lifecycle)

---

## Job Lifecycle

```
                    ┌──────────────────────────┐
                    │         PENDING           │
                    └────────────┬──────────────┘
                                 │ worker claims
                                 ▼
                    ┌──────────────────────────┐
              ┌─────│         RUNNING           │─────┐
              │     └────────────┬──────────────┘     │
              │                  │                     │
              │ lease expires    │ task completes      │ task raises
              ▼                  ▼                     ▼
    ┌─────────────────┐ ┌─────────────────┐ ┌─────────────────┐
    │     PENDING     │ │    COMPLETED     │ │  retries left?  │
    │  (re-enqueued)  │ │                  │ ├────────┬────────┤
    └─────────────────┘ └─────────────────┘ │ yes    │ no     │
                                             ▼        ▼
                                    PENDING     FAILED
                                   (+ backoff)
```

---

## Core Operations

### Enqueue

```python
job = await send_email.enqueue(to="user@example.com", subject="Hi", body="Hello")

# With delay
job = await send_email.enqueue(..., _delay=300)

# With specific ETA (datetime → epoch internally)
job = await send_email.enqueue(..., _eta=datetime.now() + timedelta(hours=1))

# With priority override
job = await send_email.enqueue(..., _priority=100)

# With idempotency key
job = await charge_payment.enqueue(order_id=123, _idempotency_key=f"charge:123")

# Transactional enqueue (same DB as app)
async with db.transaction():
    order = Order(user_id=123, total=99.99)
    await order.save()
    await charge_payment.enqueue(order_id=order.id)
```

**Validation at enqueue time:**
1. Payload must be JSON-serializable (raises `PayloadNotSerializableError`)
2. Payload must not exceed `max_payload_size` (raises `PayloadTooLargeError`)
3. `retry_delay` must be >= 1

### Claim (SafeModel — Primary)

Uses sqler's optimistic locking within a `BEGIN IMMEDIATE` transaction. Two workers selecting the same job race on the UPDATE — only one's version check passes.

```python
async def claim_job(
    worker_id: str,
    queues: list[str],
    max_claim_attempts: int = 3,
) -> Job | None:
    for claim_attempt in range(max_claim_attempts):
        now_ts = now_epoch()
        attempt_id = generate_ulid()

        async with db.transaction():
            job = await Job.query().filter(
                F("status") == JobStatus.PENDING
                & F("queue_name").in_list(queues)
                & F("eta") <= now_ts
            ).order_by("-priority", "eta", "ulid").first()

            if not job:
                return None

            # Poison pill check — quarantine corrupt payloads
            try:
                payload = json.loads(job.payload_json)
                if not isinstance(payload, dict) or "args" not in payload or "kwargs" not in payload:
                    raise ValueError("Missing 'args' or 'kwargs'")
            except (json.JSONDecodeError, TypeError, ValueError) as e:
                job.status = JobStatus.FAILED
                job.finished_at = now_ts
                job.last_error = f"Payload parse failed: {e}"
                job.last_failure_kind = FailureKind.PAYLOAD_INVALID
                job.worker_id = None
                job.lease_expires_at = None
                job.updated_at = now_ts
                await job.save()
                # Create failed attempt for audit trail
                await JobAttempt(
                    ulid=attempt_id, job_ulid=job.ulid,
                    attempt_number=job.attempts + 1, worker_id=worker_id,
                    started_at=now_ts, finished_at=now_ts,
                    status=AttemptStatus.FAILED,
                    failure_kind=FailureKind.PAYLOAD_INVALID,
                    error=f"Payload parse failed: {e}",
                ).save()
                return None

            # Claim
            job.status = JobStatus.RUNNING
            job.worker_id = worker_id
            job.attempts += 1
            job.lease_expires_at = now_ts + job.lease_duration
            job.last_attempt_id = attempt_id
            job.updated_at = now_ts

            try:
                await job.save()  # CAS via _version
            except StaleVersionError:
                await asyncio.sleep(random.uniform(0.01, 0.1 * (claim_attempt + 1)))
                continue

            await JobAttempt(
                ulid=attempt_id, job_ulid=job.ulid,
                attempt_number=job.attempts, worker_id=worker_id,
                started_at=now_ts, status=AttemptStatus.RUNNING,
                lease_expires_at=job.lease_expires_at,
            ).save()

            return job

    return None
```

**Thundering herd concern:** With `BEGIN IMMEDIATE`, writers serialize — the second worker blocks until the first commits, then sees the updated job and skips it. This works under moderate concurrency. For higher throughput, `update_one()` (when sqler ships it) reduces to a single-roundtrip atomic operation with zero contention.

### Claim (update_one — Future Optimization)

When sqler ships `update_one()` + F-expressions, the claim becomes atomic:

```python
job = await Job.query().filter(
    F("status") == JobStatus.PENDING
    & F("queue_name").in_list(queues)
    & F("eta") <= now_ts
).order_by("-priority", "eta", "ulid").update_one(
    status=JobStatus.RUNNING,
    worker_id=worker_id,
    attempts=F("attempts") + 1,
    lease_expires_at=now_ts + F("lease_duration"),
    last_attempt_id=attempt_id,
    updated_at=now_ts,
)
```

This eliminates the retry loop entirely. Prioritize shipping `update_one()` in sqler.

### Success Completion

```python
async def complete_job(job: Job, worker_id: str, result: Any):
    now_ts = now_epoch()

    # Validate result is JSON-serializable
    try:
        result_json = json.dumps(result)
    except (TypeError, ValueError) as e:
        await handle_task_failure(job, worker_id, ValueError(f"Result not JSON-serializable: {e}"))
        return

    async with db.transaction():
        await job.refresh()
        if job.status != JobStatus.RUNNING or job.worker_id != worker_id:
            return  # Lost ownership

        job.status = JobStatus.COMPLETED
        job.result_json = result_json
        job.finished_at = now_ts
        job.updated_at = now_ts
        job.last_error = None
        job.last_failure_kind = None
        job.worker_id = None
        job.lease_expires_at = None

        try:
            await job.save()
        except StaleVersionError:
            return

        # Terminalize attempt
        if job.last_attempt_id:
            attempt = await JobAttempt.query().filter(
                F("ulid") == job.last_attempt_id & F("status") == AttemptStatus.RUNNING
            ).first()
            if attempt:
                attempt.status = AttemptStatus.COMPLETED
                attempt.finished_at = now_ts
                await attempt.save()
```

### Failure Recording

```python
async def handle_task_failure(job: Job, worker_id: str, exc: Exception,
                              failure_kind: FailureKind = FailureKind.EXCEPTION):
    now_ts = now_epoch()
    error_msg = str(exc)
    tb = traceback.format_exc()

    can_retry = failure_kind in RETRYABLE_FAILURES and job.retry_count < job.max_retries

    async with db.transaction():
        await job.refresh()
        if job.status != JobStatus.RUNNING or job.worker_id != worker_id:
            return

        if can_retry:
            job.status = JobStatus.PENDING
            job.eta = calculate_retry_eta(job)
            job.retry_count += 1
        else:
            job.status = JobStatus.FAILED
            job.finished_at = now_ts

        job.last_error = error_msg
        job.last_failure_kind = failure_kind
        job.worker_id = None
        job.lease_expires_at = None
        job.updated_at = now_ts

        try:
            await job.save()
        except StaleVersionError:
            return

        # Terminalize attempt
        if job.last_attempt_id:
            attempt = await JobAttempt.query().filter(
                F("ulid") == job.last_attempt_id & F("status") == AttemptStatus.RUNNING
            ).first()
            if attempt:
                attempt.status = AttemptStatus.FAILED
                attempt.finished_at = now_ts
                attempt.error = error_msg
                attempt.traceback = tb
                attempt.failure_kind = failure_kind
                await attempt.save()


def calculate_retry_eta(job: Job) -> int:
    base_delay = job.retry_delay * (2 ** job.retry_count)
    jitter = random.uniform(0, base_delay * 0.1)
    return now_epoch() + int(base_delay + jitter)
```

### Lease Renewal

Worker auto-renews leases every `lease_duration / 3` seconds. Part of the worker process, not a separate daemon.

```python
async def _lease_renewal_loop(self):
    while self.running:
        if not self.active_jobs:
            await asyncio.sleep(self.default_lease_duration / 3)
            continue

        now_ts = now_epoch()
        for job in list(self.active_jobs.values()):
            if job.lease_expires_at is None:
                continue
            remaining = job.lease_expires_at - now_ts
            if remaining < job.lease_duration / 3:
                await job.refresh()
                if job.status != JobStatus.RUNNING or job.worker_id != self.worker_id:
                    self.active_jobs.pop(job.ulid, None)
                    continue
                job.lease_expires_at = now_ts + job.lease_duration
                job.updated_at = now_ts
                try:
                    await job.save()
                except StaleVersionError:
                    self.active_jobs.pop(job.ulid, None)

        min_lease = min((j.lease_duration for j in self.active_jobs.values()),
                        default=self.default_lease_duration)
        await asyncio.sleep(max(0.1, min_lease / 3))
```

**Manual renewal escape hatch:**

```python
from qler import current_job

@task(queue, lease_duration=3600, auto_renew=False)
async def long_job(file_id: int):
    for chunk in chunks:
        await process_chunk(chunk)
        await current_job().renew_lease()
```

### Lease Expiry Recovery

Workers periodically scan for expired leases and re-enqueue.

```python
async def recover_expired_leases(self, lease_expiry_counts_as_retry: bool = False,
                                  max_per_tick: int = 100):
    now_ts = now_epoch()
    recovered = 0

    while recovered < max_per_tick:
        async with db.transaction():
            job = await Job.query().filter(
                F("status") == JobStatus.RUNNING & F("lease_expires_at") < now_ts
            ).first()

            if not job:
                break

            if lease_expiry_counts_as_retry:
                job.retry_count += 1
                if job.retry_count >= job.max_retries:
                    job.status = JobStatus.FAILED
                    job.finished_at = now_ts
                    job.last_error = "Max retries exceeded (last: lease expired)"
                    job.last_failure_kind = FailureKind.LEASE_EXPIRED
                else:
                    job.status = JobStatus.PENDING
                    job.eta = now_ts + random.randint(0, 3)
            else:
                job.status = JobStatus.PENDING
                job.eta = now_ts + random.randint(0, 3)

            job.worker_id = None
            job.lease_expires_at = None
            job.updated_at = now_ts

            try:
                await job.save()
            except StaleVersionError:
                continue

            recovered += 1

            # Terminalize attempt
            if job.last_attempt_id:
                attempt = await JobAttempt.query().filter(
                    F("ulid") == job.last_attempt_id & F("status") == AttemptStatus.RUNNING
                ).first()
                if attempt:
                    attempt.status = AttemptStatus.LEASE_EXPIRED
                    attempt.finished_at = attempt.lease_expires_at or now_ts
                    attempt.error = "Lease expired"
                    attempt.failure_kind = FailureKind.LEASE_EXPIRED
                    await attempt.save()
```

### Cancel

Pending jobs only. Running jobs cannot be cancelled in MVP.

```python
async def cancel(self) -> bool:
    await self.refresh()
    if self.status != JobStatus.PENDING:
        return False

    now_ts = now_epoch()
    self.status = JobStatus.CANCELLED
    self.finished_at = now_ts
    self.updated_at = now_ts
    self.last_failure_kind = FailureKind.CANCELLED
    self.worker_id = None
    self.lease_expires_at = None

    try:
        await self.save()
    except StaleVersionError:
        return False
    return True
```

### Wait

```python
async def wait(self, timeout: float | None = None, poll_interval: float = 0.5,
               max_interval: float = 5.0, backoff: float = 1.5) -> Any:
    """Wait for terminal state. Returns result or raises."""
    start = time.monotonic()
    interval = poll_interval

    while True:
        await self.refresh()

        if self.status == JobStatus.COMPLETED:
            return self.result
        if self.status == JobStatus.FAILED:
            raise JobFailedError(self)
        if self.status == JobStatus.CANCELLED:
            raise JobCancelledError(self)

        if timeout is not None and time.monotonic() - start >= timeout:
            raise TimeoutError(f"Job {self.ulid} didn't complete within {timeout}s")

        await asyncio.sleep(interval)
        interval = min(interval * backoff, max_interval)
```

### Retry (Re-enqueue Failed Job)

```python
async def retry(self) -> bool:
    """Re-enqueue a failed job. Returns True if re-enqueued."""
    await self.refresh()
    if self.status != JobStatus.FAILED:
        return False

    now_ts = now_epoch()
    self.status = JobStatus.PENDING
    self.eta = now_ts
    self.retry_count = 0
    self.finished_at = None
    self.worker_id = None
    self.lease_expires_at = None
    self.updated_at = now_ts

    try:
        await self.save()
    except StaleVersionError:
        return False
    return True
```

---

## Task Definition

```python
from qler import Queue, task

queue = Queue("qler.db")

@task(queue)
async def simple_task(x: int, y: int) -> int:
    return x + y

@task(queue, queue_name="emails", max_retries=3, retry_delay=60,
      priority=10, lease_duration=600)
async def send_email(to: str, subject: str, body: str):
    await smtp.send(to, subject, body)

# Sync tasks (run in thread pool via asyncio.to_thread)
@task(queue, sync=True)
def blocking_legacy_code(data: bytes):
    return process_sync(data)
```

**Task identity constraint:** Tasks MUST be importable from module global scope. Nested functions, lambdas, and partials are rejected at decoration time to prevent "works locally, TASK_NOT_FOUND in prod" bugs.

**CPU-bound async warning:** If an async task does CPU work without `await`, the event loop freezes and lease renewal can't run. Use `sync=True` for CPU-heavy tasks.

---

## Worker

```python
await queue.run_worker(
    queues=["default", "emails"],    # Required, non-empty (IN () is a SQL error)
    concurrency=4,                    # Max concurrent jobs
    poll_interval=1.0,                # Seconds between polls when idle
    lease_recovery_interval=60,       # Seconds between lease expiry scans
)
```

**Worker ID format:** `{hostname}:{pid}:{ulid}` — unique per process, informative in logs.

**Concurrency:** Gated by semaphore. Acquired before claim, released in `finally` after terminalization.

**Startup:** Runs lease recovery once before entering the claim loop (prevents stale jobs from previous crashes sitting until the first recovery tick).

### Worker Loop

```python
async def worker_loop(self):
    await self.recover_expired_leases()

    while self.running:
        await self.concurrency_semaphore.acquire()
        try:
            job = await self.claim_job()
            if job:
                self.task_group.create_task(self.execute_job(job))
            else:
                self.concurrency_semaphore.release()
                await asyncio.sleep(self.poll_interval)
        except Exception:
            self.concurrency_semaphore.release()
            raise

        if time_for_lease_recovery():
            await self.recover_expired_leases()
```

### Graceful Shutdown

```python
await queue.shutdown(timeout=30)
# 1. Stop claiming new jobs
# 2. Wait up to timeout for running jobs to complete
# 3. Unfinished jobs recovered via lease expiry
```

Signal handling: SIGTERM/Ctrl+C triggers graceful shutdown. SIGKILL forces immediate exit (lease recovery handles it).

---

## Task Resolution

Workers must resolve `job.task` (e.g., `"myapp.tasks.send_email"`) to a function.

```bash
# --module: import these modules to register tasks
qler worker --db qler.db --module myapp.tasks --module myapp.jobs

# --app: import module, get queue from it (recommended)
qler worker --app myapp.qler:queue
```

**Failure modes:**
- `TaskNotFoundError` — module/function doesn't exist → permanent failure, no retries
- `SignatureMismatch` — args don't match current code → permanent failure, no retries

---

## CLI

Human-readable text output by default. `--json` flag on all commands for machine-parseable JSON.

### Commands (MVP)

| Command | Purpose |
|---------|---------|
| `qler init` | Create DB, set PRAGMAs, auto-add to .gitignore |
| `qler worker` | Start worker process |
| `qler status` | Queue depths and totals |
| `qler jobs` | List/filter jobs |
| `qler job <id>` | Job detail with last attempt |
| `qler attempts <id>` | Full attempt history for a job |
| `qler retry` | Re-enqueue failed jobs (by ID or filter) |
| `qler cancel` | Cancel pending jobs (by ID or filter) |
| `qler purge` | Delete terminal jobs older than threshold |
| `qler doctor` | Health checks (schema, WAL, orphaned tasks, stale jobs) |

### Examples

```bash
# Human-readable (default)
$ qler status --db qler.db
Queue       Pending  Running
default          12        2
emails          156        8
critical          0        1

# Machine-readable
$ qler status --db qler.db --json
{"queues": {"default": {"pending": 12, "running": 2}, ...}}

# Filter jobs
$ qler jobs --db qler.db --status failed --since 1h --limit 5

# Job detail (includes last attempt error + logler command)
$ qler job 01ARZ3NDEKTSV4RRFFQ69G5FAV --db qler.db

# Bulk retry
$ qler retry --db qler.db --status failed --task myapp.tasks.send_email

# Health check
$ qler doctor --db qler.db --module myapp.tasks
```

**JSON robustness:** CLI MUST NOT crash on corrupt `payload_json` or `result_json`. Render as `{"_invalid": true, "raw": "...", "parse_error": "..."}` instead of crashing.

---

## SQLite Configuration

qler delegates PRAGMA management to sqler. For standalone `Queue("qler.db")`, these PRAGMAs are applied:

```sql
PRAGMA journal_mode = WAL;
PRAGMA busy_timeout = 5000;
PRAGMA synchronous = NORMAL;
PRAGMA wal_autocheckpoint = 1000;
```

**Schema version:** `PRAGMA user_version = 1`. Checked on startup; clear error message if mismatched.

---

## Queue Configuration

```python
queue = Queue(
    db="qler.db",                    # Path or sqler Database instance
    default_lease_duration=300,
    default_max_retries=0,
    default_retry_delay=60,          # Must be >= 1
    max_payload_size=1_000_000,      # 1MB default
)
```

Environment variables:

```bash
QLER_DB=qler.db
QLER_DEFAULT_LEASE_DURATION=300
QLER_MAX_PAYLOAD_SIZE=1000000
```

---

## Testing Strategy

### Immediate Mode (Unit Tests)

```python
queue = Queue("test.db", immediate=True)

@task(queue)
async def my_task(x: int):
    return x * 2

# enqueue() still returns Job, but auto-completes
job = await my_task.enqueue(x=5)
assert job.status == JobStatus.COMPLETED
assert job.result == 10

# Direct execution without Job overhead
result = await my_task.run_now(x=5)
assert result == 10
```

JSON validation enforced in immediate mode too — non-serializable results raise `TypeError`.

### Integration Tests (Real Worker)

```python
@pytest.fixture
async def queue_with_worker():
    with tempfile.TemporaryDirectory() as tmpdir:
        queue = Queue(str(Path(tmpdir) / "test.db"))
        worker_task = asyncio.create_task(
            queue.run_worker(queues=["default"], concurrency=1)
        )
        yield queue
        await queue.shutdown(timeout=5)
        worker_task.cancel()
```

**Integration tests MUST cover:**
- Job completes through worker
- Retry on failure (exhausts budget → FAILED)
- Lease expiry recovery
- Attempt history preserved
- Idempotency key dedup

```bash
pytest tests/unit/           # Fast, immediate mode
pytest tests/integration/    # Slow, real workers
```

---

## Dependencies

### Required

| Package | Why |
|---------|-----|
| `sqler` >= TBD | Database operations (requires gap features) |
| `python` >= 3.12 | Runtime |
| `python-ulid` | Time-sorted IDs |
| `click` | CLI |

### Optional

| Package | Why |
|---------|-----|
| `logler` | Correlation context |
| `uvloop` | Performance |

---

## File Structure

```
src/qler/
├── __init__.py          # Public API: Queue, task, current_job
├── queue.py             # Queue class
├── task.py              # @task decorator
├── models/
│   ├── __init__.py
│   ├── job.py           # Job model
│   └── attempt.py       # JobAttempt model
├── worker.py            # Worker loop + lease renewal
├── cli.py               # Click CLI
├── exceptions.py        # PayloadTooLargeError, TaskNotFoundError, etc.
└── py.typed             # PEP 561 marker
tests/
├── unit/
│   ├── test_queue.py
│   ├── test_task.py
│   └── test_models.py
└── integration/
    ├── conftest.py
    ├── test_worker.py
    ├── test_retry.py
    └── test_lease.py
```

---

## MVP Scope

### In (v0.1)

- Task decorator (async + sync)
- Job + JobAttempt models (sqler, promoted columns)
- Enqueue with delay/eta/priority/idempotency_key
- Lease-based claiming (SafeModel optimistic locking)
- Automatic lease renewal
- Separate attempts vs retries counters
- Retry with exponential backoff + jitter
- Lease expiry recovery
- Cancel (pending only), wait, retry
- Payload validation + size limits
- Task resolution (--module / --app)
- Task signature mismatch handling
- Graceful shutdown (SIGTERM)
- CLI: init, worker, status, jobs, job, attempts, retry, cancel, purge, doctor
- Human-first CLI with --json flag
- Immediate mode for testing
- Integration test harness

### Out (v0.2+)

See [ROADMAP.md](ROADMAP.md) for phased plan.
