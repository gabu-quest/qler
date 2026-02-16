# qler - Technical Specification

**Version:** 0.1.0 (MVP)  
**Status:** Draft  
**Date:** 2026-02-10

---

## Overview

qler is an async-first background job queue for Python, built on SQLite via sqler. It prioritizes debuggability, zero infrastructure, and integration with the -ler suite.

**One sentence:** "Background jobs without Redis, with first-class debugging."

---

## Design Principles

1. **Async-native** - asyncio first, sync compatibility layer second
2. **SQLite-native** - Not "SQLite as fallback", SQLite as the design center
3. **sqler-native** - All DB operations through sqler's model API. No raw SQL. If sqler can't do it, fix sqler.
4. **Debuggability > Features** - "Why did this fail?" should be trivial to answer
5. **At-least-once delivery** - Be explicit; encourage idempotent tasks
6. **Lease-based claiming** - Predictable failure recovery, no separate coordinator/daemon
7. **LLM-friendly** - JSON CLI output, structured errors, correlation IDs
8. **Git-friendly** - All config as code, no UI-only settings, auto-gitignore DB files

---

## sqler Integration

**qler uses sqler's native API for ALL database operations.** No `db.adapter.execute()`, no raw SQL strings. This is the -ler Stack principle: if sqler can't express an operation, we fix sqler — not work around it.

sqler features qler depends on (see [SQLER_GAPS.md](SQLER_GAPS.md) for details):

| Feature | Status | Used For |
|---------|--------|----------|
| `AsyncSQLerSafeModel` | **Exists** | Job/Attempt models with optimistic locking |
| `F()` expressions in filter | **Exists** | All queries |
| `db.transaction()` | **Exists** | Atomic multi-model operations |
| Multi-field `order_by()` | **Being built** | Claim query ordering |
| Promoted columns | **Being built** | Named columns for hot fields (status, eta, etc.) |
| `F()` expressions in `update()` | **Being built** | Atomic counter increments |
| `update_one()` returning model | **Being built** | Atomic claim (performance optimization) |

**Note:** The API signatures for "being built" features are aspirational. The exact sqler API may differ slightly from what's shown in this spec. The semantics (what the operations do) are stable; the method names and syntax may evolve.

**Architecture:** sqler uses a hybrid column model — a small set of query-hot fields (status, queue_name, eta, priority, lease_expires_at) are **promoted** to real SQLite columns for indexes and CHECK constraints. The rest (payload, result, error, worker_id, counters, timestamps) stays in the JSON blob. Promoted fields are **column only** — excluded from JSON, same as `_id` and `_version`. Only promote what needs SQL-level performance; everything else stays in the document store.

---

## Delivery Semantics

**qler provides at-least-once delivery.**

This means:
- A job will be executed *at least* once
- A job *may* be executed more than once (worker crash, network issue, lease expiry)
- Tasks SHOULD be idempotent or handle duplicates gracefully

qler does NOT guarantee:
- Exactly-once execution
- Ordering between jobs (except via dependencies, later)

**Crash-before-completion:** If a worker finishes executing a task but crashes before persisting the result, the job will be recovered and re-executed. This is expected at-least-once behavior—not a bug. Design tasks accordingly.

**Idempotency helpers** (future):
```python
@task(queue, idempotency_key=lambda order_id: f"charge:{order_id}")
async def charge_payment(order_id: int):
    # Won't enqueue if key exists within dedupe window
    pass
```

---

## Core Concepts

### Queue

A Queue instance connects to SQLite (standalone or shared with app).

```python
from qler import Queue

# Standalone DB (recommended for production)
queue = Queue("qler.db")

# Shared with sqler app DB (transactional enqueue)
from sqler import Database
db = Database("app.db")
queue = Queue(db)
```

### Task

A decorated async function that can be enqueued.

**Task identity constraint:** Tasks must be importable from module global scope. The following are rejected at decoration time:
- Nested functions (`def outer(): @task def inner(): ...`)
- Lambdas
- Partials or dynamically generated callables

This prevents "works locally, TASK_NOT_FOUND in prod" bugs when module structure doesn't match.

```python
from qler import task

@task(queue)
async def send_email(to: str, subject: str, body: str):
    await smtp.send(to, subject, body)
    return {"sent": True}

@task(queue, max_retries=3, retry_delay=60, queue_name="critical")
async def charge_payment(order_id: int):
    # ...
    pass
```

### Job

A single execution of a task with specific arguments.

```python
job = await send_email.enqueue(to="user@example.com", subject="Hi", body="Hello")
print(job.ulid)    # "01ARZ3NDEKTSV4RRFFQ69G5FAV" (ULID)
print(job.status)  # "pending"
```

### Worker

A process that claims and executes jobs.

```python
await queue.run_worker(queues=["default", "critical"], concurrency=4)
```

---

## Job Model (sqler)

### Status Enum

```python
from enum import Enum

class JobStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"

class AttemptStatus(str, Enum):
    """Status for individual attempts (not reused after terminal)."""
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    LEASE_EXPIRED = "lease_expired"

class FailureKind(str, Enum):
    """Structured reason for job/attempt failures (enables smart doctor/UI).
    
    EXCEPTION may retry; others are permanent by default.
    """
    EXCEPTION = "exception"           # Task raised an exception (retryable)
    LEASE_EXPIRED = "lease_expired"   # Worker died or too slow (handled separately)
    TASK_NOT_FOUND = "task_not_found" # Module/function doesn't exist (permanent)
    SIGNATURE_MISMATCH = "signature_mismatch"  # Args don't match current code (permanent)
    PAYLOAD_INVALID = "payload_invalid"  # See below (permanent)
    CANCELLED = "cancelled"           # Explicitly cancelled (permanent)

# Retryable failure kinds (others are permanent)
RETRYABLE_FAILURES = {FailureKind.EXCEPTION}
```

**PAYLOAD_INVALID** covers these cases (all permanent, won't self-heal):
- JSON decode fails (DB corruption or manual edit)
- Payload missing `args` or `kwargs` keys
- Types aren't JSON primitives (shouldn't happen if enqueue validated)

**SIGNATURE_MISMATCH** is always permanent:
- Task code changed, old job has incompatible arguments
- Retrying won't help; requires code fix or manual cancellation

Using enums prevents typos turning into ghost states.

### Job Table

```python
from sqler import AsyncSQLerSafeModel, F
from typing import Optional, Any
from ulid import ULID  # pip install python-ulid

class Job(AsyncSQLerSafeModel):  # Optimistic locking via _version
    # Identity
    ulid: str                        # ULID, e.g., "01ARZ3NDEKTSV4RRFFQ69G5FAV"
    task: str                        # Function path, e.g., "myapp.tasks.send_email"
    queue_name: str = "default"      # Queue for routing
    
    # Payload (JSON blob — big, rarely queried)
    payload_json: str = "{}"         # {"args": [...], "kwargs": {...}}
    
    # Status (enum, stored as text with CHECK constraint via promoted column)
    status: str = "pending"
    
    # Scheduling
    priority: int = 0                # Higher = claimed sooner
    eta: int = 0                     # Unix epoch seconds (UTC), never NULL
    
    # Execution
    worker_id: str | None = None     # Which worker claimed it
    
    # Lease (for failure recovery)
    lease_expires_at: int | None = None  # Unix epoch seconds (UTC)
    lease_duration: int = 300            # Seconds; task can override (stays in JSON)
    
    # sqler built-in: _version (optimistic locking)
    # Incremented on every save(); StaleVersionError if out-of-sync
    
    # Results (JSON blob — big, rarely queried)
    result_json: str | None = None   # Return value if completed
    last_error: str | None = None    # Most recent error (for quick lookup)
    last_failure_kind: str | None = None  # Structured reason
    
    # Attempts & Retries (separate concepts!)
    attempts: int = 0                # Total execution attempts (including lease expiry)
    retry_count: int = 0             # Retries consumed (only task failures, not lease expiry)
    max_retries: int = 0             # Retry limit (0 = no retries)
    retry_delay: int = 60            # Base delay (exponential backoff applied)
    
    # Pointer to latest attempt (for quick lookup)
    last_attempt_id: str | None = None
    
    # Observability
    correlation_id: str = ""         # For logler; defaults to job.ulid
    
    # Timestamps (Unix epoch seconds, UTC)
    created_at: int = 0
    updated_at: int = 0
    finished_at: int | None = None   # When terminal state reached

    @classmethod
    def generate_ulid(cls) -> str:
        """Generate ULID - naturally time-sorted without relying on created_at."""
        return str(ULID())
```

**Note:** sqler provides `_id` (auto-increment INTEGER) and `_version` (optimistic locking) automatically. `ulid` is the application-facing identifier used in CLI, API, and cross-references. See Schema & Models section for which fields are promoted to real SQLite columns vs stored in the JSON blob.

**ID Choice: ULID over UUID**
- ULIDs are time-sorted, so `qler jobs` output is naturally chronological
- Still globally unique like UUIDs
- Looks like: `01ARZ3NDEKTSV4RRFFQ69G5FAV`

**ETA: Never NULL**
- At enqueue, `eta = now()` by default
- Delayed jobs set a future `eta`
- Claim query is simple `eta <= now()` (no OR, index-friendly)

**Attempts vs Retries (Two Separate Counters)**

| Counter | Incremented By | Used For |
|---------|----------------|----------|
| `attempts` | Every claim (including lease recovery) | Debugging, attempt history count |
| `retry_count` | Only task failures that schedule retries | Retry budget, backoff exponent |

- `max_retries` = number of additional tries **after the first attempt** (0 = no retries)
- Terminal failure occurs when `retry_count >= max_retries`
- Lease expiry increments `attempts` but **not** `retry_count` (by default)
- Backoff exponent uses `retry_count` (not `attempts`)

Examples:
- `max_retries=0`: 1 attempt, if it fails → permanent failure
- `max_retries=3`: up to 4 attempts if task keeps failing (1 initial + 3 retries)
- Lease expiry + re-claim doesn't consume `max_retries` budget

**Why two counters?** Lease expiry happens for innocent reasons (GC pause, laptop sleep). Without `retry_count`, a job with `max_retries=0` would fail permanently after one lease expiry, even though the task never actually ran.

### JobAttempt Table (Attempt History)

For debuggability, we track every execution attempt:

```python
class JobAttempt(AsyncSQLerSafeModel):
    """Record of each job execution attempt.
    
    Lifecycle: Created as RUNNING, updated to terminal state (COMPLETED/FAILED/LEASE_EXPIRED).
    Never reused - one row per attempt.
    """
    ulid: str                        # ULID
    job_ulid: str                    # References Job.ulid
    attempt_number: int              # 1, 2, 3...
    
    # Execution
    worker_id: str
    started_at: int                  # Unix epoch seconds (UTC)
    finished_at: int | None = None   # Unix epoch seconds (UTC)
    
    # Outcome (enum)
    status: str = "running"
    
    # Error details (only for failed/lease_expired)
    failure_kind: str | None = None  # Structured reason
    error: str | None = None         # Human-readable message
    traceback: str | None = None     # Full stack trace
    
    # Lease state at this attempt
    lease_expires_at: int | None = None  # Unix epoch seconds (UTC)
```

This enables:
- "Show me all attempts for job X" → full retry timeline
- "Why did attempt #2 fail?" → preserved even after attempt #3
- Dashboard retry history without overwriting state
- `qler doctor` can identify permanent failures by `failure_kind`

### Time Model

**All timestamps are Unix epoch seconds (INTEGER), stored in UTC.**

Why:
- Faster comparisons (integer vs string parsing)
- No timezone ambiguity (no "why is lease off by 9 hours?" bugs)
- SQLite stores efficiently

**Conversion at boundaries:**
- CLI output: ISO 8601 UTC with `Z` suffix (e.g., `2026-02-10T08:15:00Z`)
- Python API: `datetime.fromtimestamp(ts, tz=timezone.utc)`

**Time granularity:** All queue timing values (`retry_delay`, `lease_duration`, `eta`, `poll_interval` for DB timestamps) are **integer seconds**. Sub-second values are not supported for retry or lease timing. `retry_delay` must be >= 1 (enforced at `@task` decoration time). `poll_interval` and `wait()` intervals are Python-side floats and do not round-trip through the database.

```python
import time
def now_epoch() -> int:
    return int(time.time())
```

### Schema & Models

**Job model** — sqler `AsyncSQLerSafeModel` with promoted columns:

```python
from sqler import AsyncSQLerSafeModel, F

class Job(AsyncSQLerSafeModel):
    """Background job record.
    
    Only query-hot fields are promoted to real SQLite columns (for indexes,
    CHECK constraints, WHERE performance). Everything else stays in JSON.
    Promoted fields are column-only — excluded from the JSON blob, same
    as _id and _version.
    """
    
    # Promoted columns: ONLY fields that need real columns
    # (indexes, CHECK constraints, SQL WHERE/ORDER BY performance)
    __promoted__ = {
        "ulid": "TEXT UNIQUE NOT NULL",              # UNIQUE + cross-table FK
        "status": "TEXT NOT NULL DEFAULT 'pending'",  # CHECK + partial index + every query
        "queue_name": "TEXT NOT NULL DEFAULT 'default'",  # claim query WHERE
        "priority": "INTEGER NOT NULL DEFAULT 0",    # claim query ORDER BY
        "eta": "INTEGER NOT NULL",                   # claim query WHERE + ORDER BY
        "lease_expires_at": "INTEGER",               # recovery query WHERE
    }
    
    __checks__ = {
        "status": "status IN ('pending', 'running', 'completed', 'failed', 'cancelled')",
    }
    
    # --- Promoted (column only, not in JSON) ---
    ulid: str                                    # ULID primary identifier
    status: str = "pending"                      # pending/running/completed/failed/cancelled
    queue_name: str = "default"
    priority: int = 0                            # Higher = claimed first
    eta: int = 0                                 # Unix epoch seconds (UTC)
    lease_expires_at: int | None = None
    
    # --- JSON blob (everything else) ---
    task: str = ""                               # e.g. "myapp.tasks.send_email"
    worker_id: str | None = None                 # ownership checks are Python-side
    lease_duration: int = 300                    # seconds
    
    payload_json: str = "{}"                     # big, rarely queried
    result_json: str | None = None
    last_error: str | None = None
    last_failure_kind: str | None = None
    
    # Counters
    attempts: int = 0                            # Total claims (including lease recovery)
    retry_count: int = 0                         # Only task failures (retry budget)
    max_retries: int = 0
    retry_delay: int = 60                        # Seconds, must be >= 1
    
    # Pointers
    last_attempt_id: str | None = None           # ULID of latest attempt
    correlation_id: str = ""                     # For logler integration
    
    # Timestamps (Unix epoch seconds)
    created_at: int = 0
    updated_at: int = 0
    finished_at: int | None = None


class JobAttempt(AsyncSQLerSafeModel):
    """Record of a single job execution attempt.
    
    Created when a worker claims a job, terminalized when the attempt
    completes, fails, or the lease expires.
    """
    
    # Promoted: only fields used in query WHERE clauses
    __promoted__ = {
        "ulid": "TEXT UNIQUE NOT NULL",              # UNIQUE
        "job_ulid": "TEXT NOT NULL",                 # attempt lookup index
        "status": "TEXT NOT NULL DEFAULT 'running'", # terminal guard WHERE
    }
    
    __checks__ = {
        "status": "status IN ('running', 'completed', 'failed', 'lease_expired')",
    }
    
    # --- Promoted (column only) ---
    ulid: str                                    # ULID primary identifier
    job_ulid: str                                # References Job.ulid
    status: str = "running"
    
    # --- JSON blob ---
    attempt_number: int = 0
    worker_id: str = ""
    started_at: int = 0                          # Unix epoch
    finished_at: int | None = None
    
    failure_kind: str | None = None
    error: str | None = None
    traceback: str | None = None
    
    lease_expires_at: int | None = None
```

**Indexes** — declared via sqler's index API:

```python
# On Queue initialization (or qler init)

# These are the logical indexes qler needs:
# 1. Claim query: (queue_name, priority DESC, eta, ulid) WHERE status = 'pending'
#    — all promoted columns, proper composite index
# 2. Lease expiry: (lease_expires_at) WHERE status = 'running'
#    — both promoted, partial index
# 3. Correlation: json_extract(data, '$.correlation_id')
#    — JSON field, expression index
# 4. Attempt lookup: (job_ulid) on JobAttempt
#    — promoted column
# 5. Finished jobs: json_extract(data, '$.finished_at') WHERE finished_at IS NOT NULL
#    — JSON field, expression index (purge is infrequent, doesn't need a real column)
```

**Key schema decisions:**
- Only 6 fields promoted on Job, 3 on JobAttempt — everything else stays in JSON. Promoting everything defeats the point of sqler's document store.
- Promoted fields are **column only** — excluded from JSON blob, same as `_id` and `_version`. No dual-storage, no sync risk.
- CHECK constraint on `status` — ghost states rejected at DB level.
- `last_failure_kind` validation is Python-side (it's in JSON, not promoted). The enum check in the model class prevents bad values.
- `_version` (sqler built-in): Optimistic locking for all state transitions.
- `_id` (sqler built-in): Internal auto-increment PK. `ulid` is the application-facing identifier.

**Attempt cleanup:** When a job is purged, qler deletes its attempts first (application-level cascade). If sqler adds foreign key support for promoted columns in the future, this becomes a schema-level CASCADE.

---

## State Invariants (Must Hold)

These invariants are enforced by qler logic and validated by `qler doctor`.
They are not fully expressible as SQLite constraints (cross-column and cross-table),
so the worker must maintain them transactionally.

### Ownership / Lease invariants

- If `status IN ('completed', 'failed', 'cancelled')` then:
  - `worker_id IS NULL`
  - `lease_expires_at IS NULL`
  - `finished_at IS NOT NULL`

- If `status = 'pending'` then:
  - `worker_id IS NULL`
  - `lease_expires_at IS NULL`

- If `status = 'running'` then:
  - `worker_id IS NOT NULL`
  - `lease_expires_at IS NOT NULL`

### Attempt invariants

- Each attempt row is created as `status='running'` and transitions exactly once to a terminal attempt status:
  - `completed`, `failed`, or `lease_expired`
- Attempt terminalization must never overwrite an already-terminal attempt row.
  - i.e. updates MUST include `WHERE status='running'` guards.

### Transactional consistency

- Job row update + corresponding attempt terminalization MUST occur in the same SQLite transaction to prevent inconsistent audit history if the worker crashes mid-update.

### Terminal immutability

Once a job reaches a terminal status (`completed`, `failed`, `cancelled`), the following fields are immutable:

- `payload_json`
- `task`
- `created_at`
- All existing `job_attempts` rows

`updated_at` may still be set to reflect operational metadata changes, but application-level data must not be rewritten. This prevents "rewriting history" surprises during debugging.

The only legitimate mutation of terminal jobs is `qler retry` / `job.retry()`, which resets the job to `pending` (a new lifecycle, not a rewrite).

---

## Job Lifecycle

```
                    ┌──────────────────────────┐
                    │         PENDING          │
                    │  (waiting to be claimed) │
                    └────────────┬─────────────┘
                                 │ worker claims job
                                 │ sets lease_expires_at
                                 ▼
                    ┌──────────────────────────┐
              ┌─────│         RUNNING          │─────┐
              │     │   (lease_expires_at set) │     │
              │     └────────────┬─────────────┘     │
              │                  │                   │
              │ lease expires    │ task completes    │ task raises exception
              │ (worker died?)   │                   │
              ▼                  ▼                   ▼
    ┌─────────────────┐ ┌─────────────────┐ ┌─────────────────┐
    │     PENDING     │ │    COMPLETED    │ │  retries < max? │
    │  (re-enqueued)  │ │  (result set)   │ │                 │
    └─────────────────┘ └─────────────────┘ └────────┬────────┘
                                                     │
                                      ┌──────────────┴──────────────┐
                                      │ yes                         │ no
                                      ▼                             ▼
                            ┌─────────────────┐           ┌─────────────────┐
                            │     PENDING     │           │     FAILED      │
                            │ (eta = backoff) │           │ (error set)     │
                            └─────────────────┘           └─────────────────┘
```

---

## Lease-Based Claiming

### SafeModel Claim (Primary)

All claiming uses sqler's `AsyncSQLerSafeModel` with optimistic locking. Within a SQLite transaction, the `SELECT ... FOR UPDATE` semantics are provided by SQLite's locking model—only one writer proceeds at a time via `BEGIN IMMEDIATE`.

```python
async def claim_job(
    worker_id: str, 
    queues: list[str],
    max_claim_attempts: int = 3,
) -> Job | None:
    """Claim the highest-priority ready job using SafeModel optimistic locking.
    
    The entire claim + attempt creation is wrapped in a single transaction.
    On StaleVersionError (another worker won the race), retries with jitter.
    """
    for claim_attempt in range(max_claim_attempts):
        now_ts = now_epoch()
        attempt_id = generate_ulid()
        
        async with db.transaction():
            # Find the best candidate
            job = await Job.query().filter(
                F("status") == JobStatus.PENDING
                & F("queue_name").in_list(queues)
                & F("eta") <= now_ts
            ).order_by("priority", desc=True).order_by("eta").order_by("ulid").first()
            
            if not job:
                return None
            
            # Poison pill check: validate payload before claiming
            try:
                payload = json.loads(job.payload_json) if isinstance(job.payload_json, str) else job.payload_json
                if not isinstance(payload, dict) or "args" not in payload or "kwargs" not in payload:
                    raise ValueError("Missing 'args' or 'kwargs' keys")
            except (json.JSONDecodeError, TypeError, ValueError) as e:
                # Quarantine: mark as permanently failed to prevent infinite claim loop
                job.status = JobStatus.FAILED
                job.finished_at = now_ts
                job.last_error = f"Payload parse failed: {e}"
                job.last_failure_kind = FailureKind.PAYLOAD_INVALID
                job.worker_id = None
                job.lease_expires_at = None
                job.updated_at = now_ts
                await job.save()
                
                # Create a failed attempt for the audit trail
                poison_attempt = JobAttempt(
                    ulid=attempt_id,
                    job_ulid=job.ulid,
                    attempt_number=job.attempts + 1,
                    worker_id=worker_id,
                    started_at=now_ts,
                    finished_at=now_ts,
                    status=AttemptStatus.FAILED,
                    failure_kind=FailureKind.PAYLOAD_INVALID,
                    error=f"Payload parse failed: {e}",
                )
                await poison_attempt.save()
                return None  # Quarantined, move on
            
            # Claim the job
            job.status = JobStatus.RUNNING
            job.worker_id = worker_id
            job.attempts += 1
            job.lease_expires_at = now_ts + job.lease_duration
            job.last_attempt_id = attempt_id
            job.updated_at = now_ts
            
            try:
                await job.save()  # CAS via _version — raises StaleVersionError on race
            except StaleVersionError:
                # Another worker claimed this job — retry with jitter
                await asyncio.sleep(random.uniform(0.01, 0.1 * (claim_attempt + 1)))
                continue
            
            # Create attempt record (same transaction)
            attempt = JobAttempt(
                ulid=attempt_id,
                job_ulid=job.ulid,
                attempt_number=job.attempts,
                worker_id=worker_id,
                started_at=now_ts,
                status=AttemptStatus.RUNNING,
                lease_expires_at=job.lease_expires_at,
            )
            await attempt.save()
            
            return job
    
    return None  # Gave up after max_claim_attempts
```

**Why SafeModel?** sqler's `AsyncSQLerSafeModel.save()` uses `WHERE _version = ?` (compare-and-swap). If two workers SELECT the same pending job, both try to UPDATE—only one's version check passes. The loser gets `StaleVersionError` and retries to find the next available job.

**Why transactional?** All operations are wrapped in `db.transaction()`. The claim + attempt creation must all succeed or all fail. If attempt creation fails, the transaction rolls back and the job remains pending.

**Thundering herd mitigation:** With SQLite's `BEGIN IMMEDIATE`, only one writer holds the write lock at a time. The second worker's transaction blocks until the first commits, then sees the updated (now running) job and skips it. Under moderate concurrency this serialization is sufficient; for high-throughput scenarios, the `update_one()` optimization below reduces round-trips.

### Atomic Claim via update_one (Future Optimization)

When sqler adds F-expressions in `update()` and `update_one()` returning a model instance, the claim becomes a single-roundtrip atomic operation:

```python
# FUTURE — when sqler ships update_one() + F-expressions
async def claim_job_atomic(worker_id: str, queues: list[str]) -> Job | None:
    """Atomic claim: find + update + return in one operation. Zero contention."""
    now_ts = now_epoch()
    attempt_id = generate_ulid()
    
    job = await Job.query().filter(
        F("status") == JobStatus.PENDING
        & F("queue_name").in_list(queues)
        & F("eta") <= now_ts
    ).order_by("priority", desc=True).order_by("eta").order_by("ulid").limit(1).update_one(
        status=JobStatus.RUNNING,
        worker_id=worker_id,
        attempts=F("attempts") + 1,
        lease_expires_at=now_ts + F("lease_duration"),
        last_attempt_id=attempt_id,
        updated_at=now_ts,
    )
    
    if not job:
        return None
    
    # Create attempt record...
    return job
```

This eliminates the retry loop entirely. Only one worker wins the UPDATE; others get `None`. See [SQLER_GAPS.md](SQLER_GAPS.md) Gap 3 for details.

### Lease Renewal

**Automatic Renewal (Default)**

Worker auto-renews leases for running jobs every `lease_duration / 3` seconds:

```python
class Worker:
    def __init__(self, worker_id: str, default_lease_duration: int = 300):
        self.worker_id = worker_id  # Unique ID for this worker instance
        self.default_lease_duration = default_lease_duration
        self.active_jobs: dict[str, Job] = {}
        self.running = False
```

**Worker ID format:** `worker_id` SHOULD be globally unique and stable for the process lifetime. Recommended format: `{hostname}:{pid}:{ulid}` (e.g., `web-01:12345:01ARZ3NDEKTSV4RRFFQ69G5FAV`). This makes attempt timelines and `doctor` output more informative.

```python
    async def _lease_renewal_loop(self):
        """Background task that renews leases for all running jobs."""
        while self.running:
            # Handle empty active_jobs (no jobs running)
            if not self.active_jobs:
                await asyncio.sleep(self.default_lease_duration / 3)
                continue
            
            now_ts = now_epoch()
            jobs_to_renew = []
            
            # Collect jobs that need renewal (within 1/3 of lease remaining)
            for job in self.active_jobs.values():
                # Guard against missing lease (shouldn't happen, but defensive)
                if job.lease_expires_at is None:
                    continue
                remaining = job.lease_expires_at - now_ts
                threshold = job.lease_duration / 3
                if remaining < threshold:
                    jobs_to_renew.append(job)
            
            # Update each job that needs renewal (with ownership guards)
            if jobs_to_renew:
                for job in jobs_to_renew:
                    new_expires = now_ts + job.lease_duration
                    
                    # Refresh to get latest version, then verify ownership
                    await job.refresh()
                    
                    if job.status != JobStatus.RUNNING or job.worker_id != self.worker_id:
                        # Lost ownership (job was recovered by another worker)
                        self.active_jobs.pop(job.ulid, None)
                        continue
                    
                    # Update lease via SafeModel
                    job.lease_expires_at = new_expires
                    job.updated_at = now_ts
                    
                    try:
                        await job.save()  # CAS via _version
                    except StaleVersionError:
                        # Lost ownership between refresh and save (rare but possible)
                        self.active_jobs.pop(job.ulid, None)
                        continue
                    
                    # Also update the attempt record's lease
                    if job.last_attempt_id:
                        attempt = await JobAttempt.query().filter(
                            F("ulid") == job.last_attempt_id
                            & F("status") == AttemptStatus.RUNNING
                        ).first()
                        if attempt:
                            attempt.lease_expires_at = new_expires
                            await attempt.save()
            
            # Sleep for shortest lease / 3, clamped to prevent busy-loop
            min_lease = min(
                (j.lease_duration for j in self.active_jobs.values()),
                default=self.default_lease_duration
            )
            await asyncio.sleep(max(0.1, min_lease / 3))
```

This keeps the "no heartbeat daemon" promise (it's part of the worker, not separate) while being robust.

**Manual Renewal (Escape Hatch)**

For tasks that need explicit control:

```python
from qler import current_job

@task(queue, lease_duration=3600, auto_renew=False)  # Disable auto-renewal
async def long_processing_job(file_id: int):
    for chunk in chunks:
        await process_chunk(chunk)
        await current_job().renew_lease()  # Manual renewal
```

**ContextVar for current job:**

```python
from contextvars import ContextVar

_current_job: ContextVar[Job] = ContextVar('current_job')

def current_job() -> Job:
    """Get the currently executing job (within a task)."""
    return _current_job.get()
```

### Lease Expiry Recovery

Workers periodically scan for expired leases:

```python
async def recover_expired_leases(
    lease_expiry_counts_as_retry: bool = False,
    max_per_tick: int = 100,
):
    """Recover jobs with expired leases.
    
    Args:
        lease_expiry_counts_as_retry: If True, lease expiry increments retry_count
                                       and can exhaust max_retries budget.
                                       If False (default), always requeues.
        max_per_tick: Maximum jobs to recover per invocation (prevents spending
                      all time recovering instead of executing).
    """
    now_ts = now_epoch()
    recovered = 0
    
    while recovered < max_per_tick:
        async with db.transaction():
            # Find one expired job
            job = await Job.query().filter(
                F("status") == JobStatus.RUNNING
                & F("lease_expires_at") < now_ts
            ).first()
            
            if not job:
                break  # No more expired jobs
            
            last_attempt_id = job.last_attempt_id
            
            if lease_expiry_counts_as_retry:
                # Count against retry budget
                job.retry_count += 1
                
                if job.retry_count >= job.max_retries:
                    # Budget exhausted — permanent failure
                    job.status = JobStatus.FAILED
                    job.finished_at = now_ts
                    job.last_error = "Max retries exceeded (last: lease expired)"
                    job.last_failure_kind = FailureKind.LEASE_EXPIRED
                else:
                    # Re-enqueue with jitter to prevent thundering herd
                    job.status = JobStatus.PENDING
                    job.eta = now_ts + random.randint(0, 3)
            else:
                # Always re-enqueue (default — lease expiry is not a task failure)
                job.status = JobStatus.PENDING
                job.eta = now_ts + random.randint(0, 3)
            
            # Clear ownership regardless of outcome
            job.worker_id = None
            job.lease_expires_at = None
            job.updated_at = now_ts
            
            try:
                await job.save()  # CAS via _version
            except StaleVersionError:
                # Another recovery worker got this job — skip it
                continue
            
            recovered += 1
            
            # Terminalize the attempt record
            if last_attempt_id:
                attempt = await JobAttempt.query().filter(
                    F("ulid") == last_attempt_id
                    & F("status") == AttemptStatus.RUNNING
                ).first()
                if attempt:
                    attempt.status = AttemptStatus.LEASE_EXPIRED
                    attempt.finished_at = attempt.lease_expires_at or now_ts
                    attempt.error = "Lease expired (worker died, GC pause, or task too slow)"
                    attempt.failure_kind = FailureKind.LEASE_EXPIRED
                    await attempt.save()
```

**Why separate attempts from retries?**

Lease expiry can happen for innocent reasons:
- Worker is alive but long GC pause
- Laptop sleep / network hiccup
- DB lock prevented renewal

Counting these against retry budget can unfairly fail jobs. By default, only explicit task failures consume retries.

---

## API: Task Definition

```python
from qler import Queue, task

queue = Queue("qler.db")

@task(queue)
async def simple_task(x: int, y: int) -> int:
    return x + y

@task(
    queue,
    queue_name="emails",       # Route to specific queue
    max_retries=3,              # Retry up to 3 times on failure
    retry_delay=60,             # Base delay (exponential: 60, 120, 240...)
    priority=10,                # Higher priority = claimed first
    lease_duration=600,         # 10 minute lease
)
async def send_email(to: str, subject: str, body: str):
    await smtp.send(to, subject, body)

# Sync tasks (run in thread pool)
@task(queue, sync=True)
def blocking_legacy_code(data: bytes):
    # Called via asyncio.to_thread()
    return process_sync(data)
```

**⚠️ CPU-bound async tasks:** If an async task does CPU-heavy work without `await`, the event loop freezes and lease renewal can't run—causing spurious lease expiry and duplicate execution. Either:
- Use `sync=True` for CPU-heavy tasks (runs in thread pool)
- Add periodic `await asyncio.sleep(0)` to yield control

---

## API: Enqueueing

```python
# Basic enqueue
job = await send_email.enqueue(to="user@example.com", subject="Hi", body="Hello")

# With delay (run in 5 minutes)
job = await send_email.enqueue(..., _delay=300)

# With specific ETA (accepts datetime, converts to Unix epoch internally)
from datetime import datetime, timedelta
job = await send_email.enqueue(..., _eta=datetime.now() + timedelta(hours=1))

# With priority override
job = await send_email.enqueue(..., _priority=100)

# With custom correlation ID (for logler)
job = await send_email.enqueue(..., _correlation_id="request_xyz")

# Transactional enqueue (same DB)
async with db.transaction():
    order = Order(user_id=123, total=99.99)
    await order.save()
    await charge_payment.enqueue(order_id=order.id)
    # Both committed together, or both rolled back
```

**Note:** The `_eta` parameter accepts `datetime` objects for convenience. Internally, qler converts to Unix epoch (UTC) before storing.

---

## API: Job Operations

```python
job = await send_email.enqueue(...)

# Check status
await job.refresh()
print(job.status)           # "pending" | "running" | "completed" | "failed"
print(job.result)           # Return value if completed
print(job.last_error)       # Error message from last failure (if any)
print(job.last_failure_kind) # "exception" | "lease_expired" | "task_not_found" | etc.

# Get full attempt history (includes all errors/tracebacks)
attempts = await JobAttempt.query().filter(
    F("job_ulid") == job.ulid
).order_by("attempt_number").all()
for a in attempts:
    print(f"Attempt {a.attempt_number}: {a.status} - {a.error}")

# Wait for completion (async with polling)
result = await job.wait(timeout=30)  # Raises TimeoutError if not done

# Cancel pending job
await job.cancel()  # Only works if status == "pending"

# Retry failed job (conditional SQL, race-safe)
await job.retry()   # Re-enqueues if status == 'failed', no-op otherwise

# Get by ULID
job = await Job.query().filter(F("ulid") == "01ARZ3NDEKTSV4RRFFQ69G5FAV").first()
```

### Cancel Semantics

**Pending jobs:** Can be cancelled immediately. No attempt is created.

```python
async def cancel(self) -> bool:
    """Cancel a pending job.
    
    Returns True if cancelled, False if not cancellable (already running/finished).
    Uses SafeModel optimistic locking to be race-safe.
    """
    now_ts = now_epoch()
    
    # Refresh to get latest state
    await self.refresh()
    
    if self.status != JobStatus.PENDING:
        return False  # Can't cancel non-pending jobs
    
    # Transition to cancelled
    self.status = JobStatus.CANCELLED
    self.finished_at = now_ts
    self.updated_at = now_ts
    self.last_failure_kind = FailureKind.CANCELLED
    self.worker_id = None
    self.lease_expires_at = None
    
    try:
        await self.save()  # CAS via _version — race-safe
    except StaleVersionError:
        # Job was claimed or modified between refresh and save
        return False
    
    return True
```

**Running jobs:** Cannot be cancelled in MVP. The job will complete (or fail/expire).

Future: Support cooperative cancellation via a `cancel_requested` flag that tasks can check.

**Cancelled jobs:** Do NOT create a JobAttempt record for cancellation (they never started executing). The `finished_at` timestamp and `FailureKind.CANCELLED` on the Job record are sufficient.
```

### `Job.wait()` Contract

```python
async def wait(
    self, 
    timeout: float | None = None,
    poll_interval: float = 0.5,
    max_interval: float = 5.0,
    backoff: float = 1.5,
) -> Any:
    """Wait for job to reach terminal state (completed/failed/cancelled).
    
    Args:
        timeout: Max seconds to wait. None = wait forever.
        poll_interval: Initial polling interval in seconds.
        max_interval: Maximum polling interval (caps exponential backoff).
        backoff: Multiplier for exponential backoff (default 1.5x).
    
    Returns:
        job.result if completed successfully
    
    Raises:
        TimeoutError: If timeout exceeded before terminal state
        JobFailedError: If job reached 'failed' status (contains job object)
        JobCancelledError: If job was cancelled
    """
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
        
        if timeout is not None:
            elapsed = time.monotonic() - start
            if elapsed >= timeout:
                raise TimeoutError(f"Job {self.ulid} didn't complete within {timeout}s")
        
        await asyncio.sleep(interval)
        interval = min(interval * backoff, max_interval)

class JobFailedError(Exception):
    """Raised when awaited job fails permanently."""
    def __init__(self, job: Job):
        self.job = job
        super().__init__(
            f"Job {job.ulid} failed after {job.attempts} attempts: {job.last_error}"
        )

class JobCancelledError(Exception):
    """Raised when awaited job was cancelled."""
    def __init__(self, job: Job):
        self.job = job
        super().__init__(f"Job {job.ulid} was cancelled")
```

---

## API: Worker

```python
from qler import Queue

queue = Queue("qler.db")

# Run worker (blocking)
await queue.run_worker(
    queues=["default", "emails", "critical"],  # Which queues to process (required, non-empty)
    concurrency=4,                              # Max concurrent jobs
    poll_interval=1.0,                          # Seconds between polls when idle
    lease_recovery_interval=60,                 # Seconds between expired lease scans
)

# Note: queues=[] is invalid (raises ValueError). Use explicit queue names.
# Rationale: IN () is a SQL syntax error; failing fast prevents silent no-ops.

# Graceful shutdown (for integration with app lifecycle)
await queue.shutdown(timeout=30)
# - Stops claiming new jobs
# - Waits for running jobs to finish (up to timeout)
# - Jobs not finished will be recovered via lease expiry
```

### Worker Loop (simplified)

**Concurrency invariant:** A worker never claims more than `concurrency` jobs at a time. Claiming is gated by a semaphore; capacity is acquired before claim and released after terminalization (success, failure, or ownership loss).

```python
async def worker_loop(self):
    # Self-healing: recover any stale running jobs from previous crashes
    # before entering the claim loop (prevents stale jobs sitting until
    # the first recovery tick).
    await self.recover_expired_leases()
    
    while self.running:
        await self.concurrency_semaphore.acquire()
        try:
            job = await self.claim_job()
            if job:
                # execute_job MUST release semaphore in a finally block
                self.task_group.create_task(self.execute_job(job))
            else:
                self.concurrency_semaphore.release()
                await asyncio.sleep(self.poll_interval)
        except Exception:
            self.concurrency_semaphore.release()
            raise
        
        # Periodically recover expired leases
        if time_for_lease_recovery():
            await self.recover_expired_leases()
```

**Semaphore release contract:** `execute_job()` MUST release the semaphore permit in a `finally` clause after the job is terminalized (success, failure) or ownership is lost. This prevents semaphore leaks.

---

## CLI Specification

All commands output JSON for LLM consumption.

**JSON robustness:** CLI must never crash on corrupt `payload_json` or `result_json` in the database. If JSON decoding fails, the CLI renders the field as `{"_invalid": true, "raw": "...(maybe truncated)...", "parse_error": "..."}` instead of the parsed value. This keeps "LLM-friendly JSON output" honest even when the database contains garbage.

### `qler status`

```bash
$ qler status --db qler.db
```

```json
{
  "queues": {
    "default": {"pending": 12, "running": 2},
    "emails": {"pending": 156, "running": 8},
    "critical": {"pending": 0, "running": 1}
  },
  "totals": {
    "pending": 168,
    "running": 11,
    "completed_24h": 2341,
    "failed_24h": 12
  }
}
```

**Note:** Worker counts are not included in v0.1 (would require heartbeat infrastructure). The `running` count per queue shows active work.

### `qler jobs`

```bash
$ qler jobs --db qler.db --status failed --since 1h --limit 5
```

```json
{
  "jobs": [
    {
      "id": "01ARZ3NDEKTSV4RRFFQ69G5FAV",
      "task": "myapp.tasks.send_email",
      "queue": "emails",
      "status": "failed",
      "last_error": "ConnectionRefusedError: SMTP unavailable",
      "last_failure_kind": "exception",
      "attempts": 4,
      "max_retries": 3,
      "created_at": "2026-02-10T08:15:00Z",
      "finished_at": "2026-02-10T08:18:45Z"
    }
  ],
  "total": 12,
  "showing": 5
}
```

### `qler job <id>`

```bash
$ qler job 01ARZ3NDEKTSV4RRFFQ69G5FAV --db qler.db
```

```json
{
  "id": "01ARZ3NDEKTSV4RRFFQ69G5FAV",
  "task": "myapp.tasks.send_email",
  "queue": "emails",
  "status": "failed",
  "payload": {
    "args": [],
    "kwargs": {"to": "user@example.com", "subject": "Welcome", "body": "..."}
  },
  "last_error": "ConnectionRefusedError: SMTP server unavailable",
  "last_failure_kind": "exception",
  "attempts": 4,
  "max_retries": 3,
  "created_at": "2026-02-10T08:15:00Z",
  "finished_at": "2026-02-10T08:18:45Z",
  "correlation_id": "01ARZ3NDEKTSV4RRFFQ69G5FAV",
  "logler_command": "logler llm search --correlation-id 01ARZ3NDEKTSV4RRFFQ69G5FAV",
  "last_attempt": {
    "id": "01ARZ3NDEKTSV4RRFFQ69G5FBX",
    "attempt_number": 4,
    "started_at": "2026-02-10T08:17:00Z",
    "finished_at": "2026-02-10T08:18:45Z",
    "error": "ConnectionRefusedError: SMTP server unavailable",
    "traceback": "Traceback (most recent call last):\n  ..."
  }
}
```

**Note:** Detailed error/traceback comes from `last_attempt`. Use `qler attempts <job_id>` to see full retry timeline.

### `qler attempts`

```bash
$ qler attempts 01ARZ3NDEKTSV4RRFFQ69G5FAV --db qler.db
```

```json
{
  "job_id": "01ARZ3NDEKTSV4RRFFQ69G5FAV",
  "attempts": [
    {
      "id": "01ARZ3NDEKTSV4RRFFQ69G5FA1",
      "attempt_number": 1,
      "worker_id": "worker-abc",
      "status": "failed",
      "failure_kind": "exception",
      "started_at": "2026-02-10T08:15:01Z",
      "finished_at": "2026-02-10T08:15:02Z",
      "error": "ConnectionRefusedError: SMTP server unavailable"
    },
    {
      "id": "01ARZ3NDEKTSV4RRFFQ69G5FA2",
      "attempt_number": 2,
      "worker_id": "worker-abc",
      "status": "lease_expired",
      "failure_kind": "lease_expired",
      "started_at": "2026-02-10T08:16:02Z",
      "finished_at": "2026-02-10T08:16:32Z",
      "error": "Lease expired (worker died, GC pause, or task too slow)"
    },
    {
      "id": "01ARZ3NDEKTSV4RRFFQ69G5FA3",
      "attempt_number": 3,
      "worker_id": "worker-def",
      "status": "failed",
      "failure_kind": "exception",
      "started_at": "2026-02-10T08:16:32Z",
      "finished_at": "2026-02-10T08:16:33Z",
      "error": "ConnectionRefusedError: SMTP server unavailable"
    },
    {
      "id": "01ARZ3NDEKTSV4RRFFQ69G5FBX",
      "attempt_number": 4,
      "worker_id": "worker-def",
      "status": "failed",
      "failure_kind": "exception",
      "started_at": "2026-02-10T08:17:00Z",
      "finished_at": "2026-02-10T08:18:45Z",
      "error": "ConnectionRefusedError: SMTP server unavailable",
      "traceback": "Traceback (most recent call last):\n  ..."
    }
  ],
  "total_attempts": 4
}
```

Full attempt history for debugging retry behavior. Note that attempt #2 was a lease expiry (doesn't consume retry budget by default).

### `qler retry`

```bash
$ qler retry --db qler.db --status failed --task send_email
{"retried": 12, "skipped": 0}

$ qler retry --db qler.db 01ARZ3NDEKTSV4RRFFQ69G5FAV
{"retried": 1}
```

**Filter notes:**
- `--task` matches full task path (e.g., `myapp.tasks.send_email`). No fuzzy matching in v0.1.
- `--older-than` / `--since` compare `finished_at` for failed jobs.

### `qler cancel`

```bash
$ qler cancel --db qler.db --status pending --older-than 1h
{"cancelled": 45}
```

**Filter notes:**
- `--task` matches full task path. No fuzzy matching in v0.1.
- `--older-than` / `--since` compare `created_at` for pending jobs.

### `qler purge`

```bash
$ qler purge --db qler.db --status completed --older-than 7d
{"purged": 12453}
```

**Filter notes:**
- `--older-than` compares `finished_at` for completed/failed jobs.

### `qler worker`

```bash
$ qler worker --db qler.db --queues default,emails --concurrency 4

# Graceful shutdown:
# - Ctrl+C or SIGTERM: finish running jobs (up to 30s), then exit
# - SIGKILL: immediate exit (jobs recovered via lease expiry)

# With procler integration
$ procler define --name qler-worker --command "qler worker --db qler.db --queues default --concurrency 4"
```

### `qler serve` (v0.2+)

```bash
$ qler serve --db qler.db --port 8823
# Opens web dashboard at http://localhost:8823
```

**Note:** `qler serve` requires the web dashboard, which is out of scope for v0.1.

### `qler init`

```bash
$ qler init --db qler.db
```

```json
{
  "created": "qler.db",
  "gitignore_updated": true,
  "entries_added": ["qler.db", "qler.db-wal", "qler.db-shm"]
}
```

Automatically:
- Creates empty database with schema
- Sets `PRAGMA user_version = 1` (schema version for upgrade guard)
- Adds DB files to `.gitignore` (creates if missing)
- Prevents accidental commit of queue state

### `qler tasks`

```bash
$ qler tasks --module myapp.tasks
```

```json
{
  "tasks": [
    {
      "name": "myapp.tasks.send_email",
      "queue": "emails",
      "max_retries": 3,
      "retry_delay": 60,
      "lease_duration": 300
    },
    {
      "name": "myapp.tasks.charge_payment",
      "queue": "critical",
      "max_retries": 0,
      "retry_delay": 60,
      "lease_duration": 600
    }
  ]
}
```

Lists all registered tasks with their configuration. Useful for:
- LLM understanding available tasks
- Verifying task registration
- Debugging "task not found" errors

### `qler backup`

```bash
$ qler backup --db qler.db --output backup-2026-02-10.db
```

```json
{
  "source": "qler.db",
  "destination": "backup-2026-02-10.db",
  "wal_checkpointed": true,
  "size_bytes": 1048576,
  "job_count": 12453
}
```

Safe backup that:
- Creates consistent snapshot using SQLite backup API (`sqlite3.Connection.backup` or `VACUUM INTO`), after checkpointing WAL
- Checkpoints WAL first (ensures all pending writes included)
- Uses backup API for a race-free copy even under concurrent writers
- Prevents the "copied DB without WAL" corruption footgun

### `qler doctor`

```bash
$ qler doctor --db qler.db --module myapp.tasks
```

```json
{
  "status": "warnings",
  "checks": {
    "db_exists": {"status": "ok"},
    "db_in_gitignore": {"status": "ok"},
    "wal_mode": {"status": "ok", "mode": "wal"},
    "orphaned_tasks": {
      "status": "warning",
      "tasks": ["myapp.old_module.deleted_task"],
      "job_count": 3,
      "suggestion": "qler cancel --task myapp.old_module.deleted_task"
    },
    "stale_running_jobs": {
      "status": "ok",
      "count": 0
    }
  }
}
```

Health checks:
- DB exists and is valid SQLite
- Schema version matches expected (`PRAGMA user_version`); errors with clear message if mismatched
- DB files in `.gitignore`
- WAL mode enabled
- Required PRAGMAs set on current connection
- No orphaned tasks (jobs referencing deleted task code)
- No stale running jobs (lease expired but not recovered)

---

## logler Integration

### Automatic Correlation

```python
# In qler worker, before executing task:
import logler

async def execute_job(self, job: Job):
    with logler.correlation_context(job.correlation_id):
        # All logs within this context tagged with correlation_id
        result = await self.run_task(job)
```

### CLI Integration

```bash
# Job detail includes logler command
$ qler job 01ARZ3NDEKTSV4RRFFQ69G5FAV
{
  ...
  "logler_command": "logler llm search app.log --correlation-id 01ARZ3NDEKTSV4RRFFQ69G5FAV"
}

# Direct integration
$ qler logs 01ARZ3NDEKTSV4RRFFQ69G5FAV --db qler.db
# Internally runs: logler llm search --correlation-id 01ARZ3NDEKTSV4RRFFQ69G5FAV
```

---

## procler Integration

### Worker Definition

```yaml
# procler config.yaml
processes:
  qler-default:
    command: qler worker --db /app/qler.db --queues default --concurrency 4
    health_check:
      type: http
      url: http://localhost:8765/health  # qler worker exposes health endpoint
    restart: on-failure
    stop_signal: SIGTERM        # Triggers graceful shutdown
    stop_timeout: 30            # Wait for jobs to finish
    
  qler-critical:
    command: qler worker --db /app/qler.db --queues critical --concurrency 2
    restart: always
    stop_signal: SIGTERM
    stop_timeout: 60            # Longer timeout for critical jobs
    
groups:
  workers:
    - qler-default
    - qler-critical
```

### Health Endpoint

Workers expose a simple health endpoint:

```
GET /health → {"status": "healthy", "jobs_processed": 1234, "uptime": 3600}
```

---

## Web Dashboard (v0.2+)

Vue 3 + Naive UI (consistent with procler/sshler).

### Routes

| Route | View |
|-------|------|
| `/` | Overview: queue stats, throughput graph, recent failures |
| `/jobs` | Job browser: filterable table, bulk actions |
| `/jobs/:id` | Job detail: payload, result/error, retry timeline, logler link |
| `/workers` | Worker status: active jobs, uptime, processed count |
| `/schedules` | Periodic tasks (future): cron list, next run, enable/disable |

### Real-time Updates

WebSocket connection for live updates:
- Job status changes
- Queue depth changes
- Worker connect/disconnect

---

## Error Handling

### Retries with Exponential Backoff

```python
def calculate_retry_eta(job: Job) -> int:
    """Calculate next retry time with exponential backoff + jitter.
    
    Uses retry_count (not attempts) so lease expiries don't inflate backoff.
    Returns Unix epoch timestamp.
    """
    # retry_count is 0 for first retry, 1 for second, etc.
    base_delay = job.retry_delay * (2 ** job.retry_count)
    jitter = random.uniform(0, base_delay * 0.1)
    return now_epoch() + int(base_delay + jitter)
```

### Failure Recording

```python
async def handle_task_failure(
    job: Job, 
    worker_id: str,
    exc: Exception, 
    failure_kind: FailureKind = FailureKind.EXCEPTION
):
    """Record failure to both Job and JobAttempt tables.
    
    Uses SafeModel with ownership verification. Job + attempt updates are
    wrapped in a single transaction to prevent inconsistent audit history.
    """
    now_ts = now_epoch()
    error_msg = str(exc)
    tb = traceback.format_exc()
    
    can_retry = (
        failure_kind in RETRYABLE_FAILURES and
        job.retry_count < job.max_retries
    )
    
    async with db.transaction():
        # Refresh to get latest version + verify ownership
        await job.refresh()
        
        if job.status != JobStatus.RUNNING or job.worker_id != worker_id:
            # Lost ownership — job was recovered by another worker
            return
        
        last_attempt_id = job.last_attempt_id
        
        if can_retry:
            new_eta = calculate_retry_eta(job)
            job.status = JobStatus.PENDING
            job.eta = new_eta
            job.retry_count += 1
            job.last_error = error_msg
            job.last_failure_kind = failure_kind
            job.worker_id = None
            job.lease_expires_at = None
        else:
            # Permanent failure
            job.status = JobStatus.FAILED
            job.finished_at = now_ts
            job.last_error = error_msg
            job.last_failure_kind = failure_kind
            job.worker_id = None
            job.lease_expires_at = None
        
        job.updated_at = now_ts
        
        try:
            await job.save()  # CAS via _version
        except StaleVersionError:
            # Lost ownership between refresh and save (very rare within transaction)
            return
        
        # Terminalize the attempt record (guard: only if still running)
        if last_attempt_id:
            attempt = await JobAttempt.query().filter(
                F("ulid") == last_attempt_id
                & F("status") == AttemptStatus.RUNNING
            ).first()
            if attempt:
                attempt.status = AttemptStatus.FAILED
                attempt.finished_at = now_ts
                attempt.error = error_msg
                attempt.traceback = tb
                attempt.failure_kind = failure_kind
                await attempt.save()
```

### Success Completion

```python
async def complete_job(job: Job, worker_id: str, result: Any):
    """Record successful task completion to both Job and JobAttempt tables.
    
    Uses SafeModel with ownership verification. Job + attempt updates are
    wrapped in a single transaction to prevent inconsistent audit history.
    """
    now_ts = now_epoch()
    
    # Validate result is JSON-serializable (fail fast)
    try:
        result_json = json.dumps(result)
    except (TypeError, ValueError) as e:
        # Treat as task failure
        await handle_task_failure(
            job, 
            worker_id,
            ValueError(f"Result not JSON-serializable: {e}"),
            FailureKind.EXCEPTION
        )
        return
    
    async with db.transaction():
        # Refresh to get latest version + verify ownership
        await job.refresh()
        
        if job.status != JobStatus.RUNNING or job.worker_id != worker_id:
            # Lost ownership — job was recovered by another worker
            return
        
        last_attempt_id = job.last_attempt_id
        
        # Transition to completed
        # Clear last_error/last_failure_kind so completed jobs don't show stale errors
        job.status = JobStatus.COMPLETED
        job.result_json = result_json
        job.finished_at = now_ts
        job.updated_at = now_ts
        job.last_error = None
        job.last_failure_kind = None
        job.worker_id = None
        job.lease_expires_at = None
        
        try:
            await job.save()  # CAS via _version
        except StaleVersionError:
            # Lost ownership between refresh and save (very rare within transaction)
            return
        
        # Terminalize the attempt record (guard: only if still running)
        if last_attempt_id:
            attempt = await JobAttempt.query().filter(
                F("ulid") == last_attempt_id
                & F("status") == AttemptStatus.RUNNING
            ).first()
            if attempt:
                attempt.status = AttemptStatus.COMPLETED
                attempt.finished_at = now_ts
                await attempt.save()
```

---

## Git-Friendly Guarantees

| Thing | Git-tracked? | Notes |
|-------|:------------:|-------|
| Task definitions (`@task`) | ✅ | Python code |
| Queue configuration | ✅ | Code or env vars |
| Retry/backoff settings | ✅ | In `@task` decorator |
| Cron schedules (future) | ✅ | `@cron` decorator, not UI |
| Procler worker config | ✅ | YAML file |
| Job data | ❌ | SQLite DB (expected) |
| `qler.db`, `*-wal`, `*-shm` | ❌ | Auto-added to `.gitignore` |

**Principle:** If it affects behavior, it's code. The dashboard is read-only + operational (retry, cancel), never configuration.

---

## Safety Features

### Payload Validation

Payloads must be JSON-serializable. Fail fast at enqueue time:

```python
async def enqueue(self, *args, **kwargs):
    payload = {"args": args, "kwargs": kwargs}
    
    # Validate JSON-serializable
    try:
        json.dumps(payload)
    except (TypeError, ValueError) as e:
        raise PayloadNotSerializableError(
            f"Payload must be JSON-serializable: {e}"
        ) from e
    
    # Validate size
    payload_size = len(json.dumps(payload).encode())
    if payload_size > self.max_payload_size:
        raise PayloadTooLargeError(
            f"Payload size {payload_size} exceeds limit {self.max_payload_size}"
        )
    
    # ... create job
```

**Result types:** Task can return any JSON-serializable value (`dict`, `list`, `str`, `int`, `None`, etc.). Validated at completion (see `complete_job()` in Error Handling section, which handles non-serializable results via `handle_task_failure()`).

### Payload Size Limits

Prevents DB bloat from accidentally large payloads:

```python
# Default: 1MB max payload
queue = Queue("qler.db", max_payload_size=1_000_000)

# Override per-task for legitimate large payloads
@task(queue, max_payload_size=10_000_000)  # 10MB
async def process_large_data(data: dict):
    pass

# Exceeding limit raises PayloadTooLargeError at enqueue time
await big_task.enqueue(huge_data={...})  # Fails fast, not at execution
```

### Task Signature Mismatch Handling

When task code changes but old jobs exist:

```python
# Old job was enqueued with: send_email(to, subject)
# New code expects: send_email(to, subject, priority)

# qler detects this and fails gracefully:
{
  "status": "failed",
  "error": "Task signature mismatch: missing required argument 'priority'",
  "hint": "Job was enqueued with old task signature. Consider: qler retry <job_id> or qler cancel --task send_email --older-than 1h",
  "retries_skipped": true  # Don't retry signature mismatches
}
```

### Sensitive Data Warning

Documented anti-pattern:

```python
# BAD: Secrets in payload (stored in plaintext in DB)
await reset_password.enqueue(user_id=123, new_password="secret123")

# GOOD: Only IDs, fetch secrets at runtime
await reset_password.enqueue(user_id=123, reset_token_id=456)
```

Future: Optional payload encryption for sensitive fields.

### Graceful Shutdown

For safe deploys without losing jobs:

```python
# In your app
await queue.shutdown(timeout=30)
# 1. Stop accepting new jobs
# 2. Wait up to 30s for running jobs to complete
# 3. Running jobs that don't finish will be recovered via lease expiry
```

Signal handling in CLI:

```bash
$ qler worker --db qler.db --queues default
# Ctrl+C or SIGTERM triggers graceful shutdown
# SIGKILL forces immediate exit (jobs recovered via lease)
```

---

## SQLite Configuration

**qler delegates PRAGMA management to sqler.** When using a shared sqler `Database` instance, sqler handles connection setup. For standalone `Queue("qler.db")`, qler creates an sqler `Database` internally and ensures these PRAGMAs are applied:

```sql
PRAGMA journal_mode = WAL;     -- Enables concurrent reads during writes
PRAGMA busy_timeout = 5000;    -- Wait 5s for locks instead of failing immediately
PRAGMA synchronous = NORMAL;   -- Balanced durability/performance for WAL mode
PRAGMA wal_autocheckpoint = 1000;  -- Prevent WAL growing forever on busy systems
```

**Why these matter:**

| PRAGMA | Without it... |
|--------|---------------|
| `journal_mode=WAL` | "database is locked" errors with 2+ workers |
| `busy_timeout` | Transient failures on any contention |
| `synchronous=NORMAL` | Unnecessary I/O overhead in WAL mode |
| `wal_autocheckpoint` | WAL file grows unbounded under sustained write load |

**Note:** `foreign_keys` is not listed because sqler uses application-level cascade (qler deletes attempts before purging jobs). If sqler adds foreign key support for promoted columns in the future, this becomes a schema-level CASCADE.

**Per-connection enforcement:** PRAGMAs are per-connection state in SQLite. sqler's Database class applies them on every new connection (or on checkout from pool). `Queue` initialization verifies via sqler; `doctor` validates by opening a fresh connection.

**Schema version check:** On startup, `Queue(...)` reads `PRAGMA user_version` and errors with a clear message if the version is unsupported (e.g., "Database schema version 0 — run `qler init` or upgrade").

---

## Configuration

### Queue Options

```python
queue = Queue(
    db="qler.db",                    # Path or sqler Database instance
    default_lease_duration=300,       # Default lease for tasks
    default_max_retries=0,            # Default retry count
    default_retry_delay=60,           # Default retry delay (seconds, >= 1)
    max_payload_size=1_000_000,       # 1MB default
    threadpool_workers=None,          # Dedicated thread pool size for sync=True tasks
                                      # (None = use default asyncio thread pool)
)
```

### Environment Variables

```bash
QLER_DB=qler.db
QLER_DEFAULT_LEASE_DURATION=300
QLER_MAX_PAYLOAD_SIZE=1000000
QLER_LOG_LEVEL=INFO
```

---

## MVP Scope

### In Scope (v0.1)

- [x] Task decorator with async support
- [x] Job model with sqler (ULID IDs, Status enum)
- [x] **JobAttempt model for attempt history**
- [x] Enqueue with delay/eta/priority
- [x] **ETA never NULL (defaults to now())**
- [x] **Atomic job claiming (SafeModel optimistic locking)**
- [x] Lease-based worker claiming
- [x] **Automatic lease renewal (not just manual)**
- [x] **Separate attempts vs retries (lease expiry doesn't consume retry budget)**
- [x] Retry with exponential backoff + jitter
- [x] CLI: status, jobs, job, attempts, retry, cancel, purge, worker
- [x] CLI: init (with auto-gitignore), tasks, backup, doctor
- [x] **Task resolution contract (--module or --app)**
- [x] logler correlation integration
- [x] Sync task compatibility (via to_thread)
- [x] **Payload JSON validation (fail fast at enqueue)**
- [x] Payload size limits
- [x] Task signature mismatch handling (permanent failure)
- [x] Graceful shutdown (SIGTERM handling)
- [x] **Integration test harness**

### Out of Scope (v0.2+)

- [ ] Web dashboard
- [ ] Periodic/cron tasks
- [ ] Rate limiting
- [ ] Job dependencies/chaining
- [ ] Dead letter queue
- [ ] Idempotency keys
- [ ] Prometheus metrics
- [ ] Payload encryption

---

## Dependencies

### Required

- `sqler` >= TBD (requires promoted columns, F-expressions in update, multi-field order_by — see [SQLER_GAPS.md](SQLER_GAPS.md))
- `python` >= 3.12
- `python-ulid` (for time-sorted IDs)

### Optional

- `logler` (for correlation context)
- `uvloop` (for performance)
- `click` (for CLI)

---

## File Structure

```
qler/
├── __init__.py          # Public API: Queue, task, current_job
├── queue.py             # Queue class
├── task.py              # @task decorator
├── models/
│   ├── __init__.py
│   ├── job.py           # Job model (sqler)
│   └── attempt.py       # JobAttempt model (sqler)
├── worker.py            # Worker loop + lease renewal
├── cli.py               # Click CLI
├── exceptions.py        # PayloadTooLargeError, TaskNotFoundError, etc.
├── integrations/
│   ├── logler.py        # Correlation context
│   └── procler.py       # Health endpoint
└── dashboard/           # Vue app (future)
```

---

## Example: FastAPI Integration

```python
from fastapi import FastAPI
from contextlib import asynccontextmanager
from qler import Queue, task

queue = Queue("qler.db")

@task(queue, max_retries=3)
async def send_welcome_email(user_id: int):
    user = await User.get(user_id)
    await email.send(user.email, "Welcome!", "...")

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Start background worker
    worker_task = asyncio.create_task(
        queue.run_worker(queues=["default"], concurrency=2)
    )
    yield
    # Graceful shutdown: wait for running jobs to complete
    await queue.shutdown(timeout=30)
    worker_task.cancel()

app = FastAPI(lifespan=lifespan)

@app.post("/users")
async def create_user(name: str, email: str):
    user = User(name=name, email=email)
    await user.save()
    await send_welcome_email.enqueue(user_id=user.id)
    return {"id": user.id}
```

---

## Task Resolution Contract

Workers must be able to resolve `job.task` (e.g., `"myapp.tasks.send_email"`) → function. This is the **#1 source of "it works locally but fails in prod"** bugs.

### The Contract

**One explicit way to register tasks:**

```bash
# Option 1: --module (import these modules to register tasks)
$ qler worker --db qler.db --module myapp.tasks --module myapp.jobs

# Option 2: --app (Celery-style: import this module, get queue from it)
$ qler worker --app myapp.qler:queue

# Option 3: Autodiscover (future, maybe)
$ qler worker --db qler.db --autodiscover myapp
```

**Recommended:** Use `--app` for most projects. It's explicit and matches the Celery mental model.

### Failure Modes

```python
# Job references task that doesn't exist (module deleted, renamed, etc.)
{
  "status": "failed",
  "error": "TaskNotFoundError: myapp.old_tasks.deleted_function",
  "hint": "Task module was removed or renamed. Consider: qler cancel --task myapp.old_tasks.deleted_function",
  "retries_skipped": true  # Don't retry - won't self-heal
}
```

### Signature Mismatch = Permanent Failure

```python
# Old job: send_email(to, subject)
# New code: send_email(to, subject, priority)  # new required arg

# Result: permanent failure, no retries
{
  "status": "failed", 
  "error": "TaskSignatureMismatch: missing required argument 'priority'",
  "retries_skipped": true
}
```

**Rationale:** Retrying won't fix a signature mismatch. Fail loudly so the developer notices.

---

## Testing Strategy

### Unit Tests: Immediate Mode

```python
# Test mode: execute immediately without worker
queue = Queue("test.db", immediate=True)

@task(queue)
async def my_task(x: int):
    return x * 2

# In immediate mode, enqueue() still returns Job, but job is auto-completed
job = await my_task.enqueue(x=5)
assert job.status == JobStatus.COMPLETED
assert job.result == 10

# For direct execution without Job overhead, use run_now()
result = await my_task.run_now(x=5)
assert result == 10
```

**API contract:** `enqueue()` always returns `Job`. Use `run_now()` when you want the result directly.

Immediate mode is perfect for testing task logic in isolation.

**JSON validation in immediate mode:** Results are validated for JSON-serializability the same as in worker mode. Non-serializable return values raise `TypeError` immediately, ensuring behavior matches production.

### Integration Tests: Real Worker

Immediate mode won't catch the scary parts (claiming, leases, retries). Add integration tests:

```python
import pytest
import asyncio
import tempfile
from pathlib import Path
from qler import Queue, task

@pytest.fixture
async def queue_with_worker():
    """Real queue with real worker for integration tests."""
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "test.db"
        queue = Queue(str(db_path))
        
        # Start worker in background
        worker_task = asyncio.create_task(
            queue.run_worker(queues=["default"], concurrency=1)
        )
        
        yield queue
        
        # Cleanup
        await queue.shutdown(timeout=5)
        worker_task.cancel()


async def test_job_completes(queue_with_worker):
    \"\"\"Test that jobs actually complete through the worker.\"\"\"
    queue = queue_with_worker
    results = []
    
    @task(queue)
    async def capture_task(value: int):
        results.append(value)
        return value * 2
    
    job = await capture_task.enqueue(value=42)
    
    # Wait for completion
    result = await job.wait(timeout=5)
    
    assert result == 84
    assert results == [42]


async def test_retry_on_failure(queue_with_worker):
    \"\"\"Test that retries work correctly.\"\"\"
    queue = queue_with_worker
    attempt_count = 0
    
    @task(queue, max_retries=2, retry_delay=1)
    async def flaky_task():
        nonlocal attempt_count
        attempt_count += 1
        if attempt_count < 3:
            raise ValueError("Not yet!")
        return "success"
    
    job = await flaky_task.enqueue()
    result = await job.wait(timeout=10)
    
    assert result == "success"
    assert attempt_count == 3  # 1 initial + 2 retries


async def test_lease_expiry_recovery(queue_with_worker):
    \"\"\"Test that expired leases get recovered.\"\"\"
    queue = queue_with_worker
    
    @task(queue, lease_duration=1)  # 1 second lease
    async def slow_task():
        await asyncio.sleep(10)  # Will exceed lease
    
    job = await slow_task.enqueue()
    
    # Wait for lease to expire and recovery to happen
    await asyncio.sleep(3)
    
    await job.refresh()
    # Job should be back to pending (lease expired, recovered)
    # or running with a new attempt
    assert job.attempts >= 2


async def test_attempt_history_preserved(queue_with_worker):
    \"\"\"Test that attempt history is kept for debugging.\"\"\"
    queue = queue_with_worker
    
    @task(queue, max_retries=1, retry_delay=1)
    async def failing_task():
        raise ValueError("Always fails")
    
    job = await failing_task.enqueue()
    
    # Wait for all retries to exhaust
    await asyncio.sleep(2)
    
    await job.refresh()
    assert job.status == JobStatus.FAILED
    
    # Check attempt history
    attempts = await JobAttempt.query().filter(
        F("job_ulid") == job.ulid
    ).order_by("attempt_number").all()
    assert len(attempts) == 2  # Initial + 1 retry
    assert all(a.status == "failed" for a in attempts)
    assert all(a.error == "Always fails" for a in attempts)


async def test_crash_after_completion(queue_with_worker):
    """Test at-least-once semantics: crash after task returns but before DB commit.
    
    This simulates the canonical at-least-once gotcha: task executes successfully
    but worker crashes before persisting the result. The job should be re-executed.
    
    NOTE: This test simulates "crash" by raising in complete_job(). This only works
    if the worker treats complete_job() exceptions as fatal (doesn't write to DB).
    A more faithful simulation would kill the worker process entirely and observe
    lease-based recovery. This test validates the recovery path, not true process death.
    """
    queue = queue_with_worker
    execution_count = 0
    
    @task(queue, lease_duration=1)  # Short lease for faster recovery
    async def idempotent_task():
        nonlocal execution_count
        execution_count += 1
        return "success"
    
    # Patch complete_job to simulate crash after task returns
    original_complete = queue._worker.complete_job
    crash_on_first = True
    
    async def crashing_complete(job, worker_id, result):
        nonlocal crash_on_first
        if crash_on_first:
            crash_on_first = False
            # Simulate crash: don't write to DB, just return
            # (In real crash, process dies here; lease expiry recovers)
            raise RuntimeError("Simulated crash")
        return await original_complete(job, worker_id, result)
    
    queue._worker.complete_job = crashing_complete
    
    job = await idempotent_task.enqueue()
    
    # Wait for recovery and re-execution
    await asyncio.sleep(3)
    
    await job.refresh()
    
    # Task ran twice (at-least-once), but ultimately completed
    assert execution_count >= 2
    assert job.status == JobStatus.COMPLETED
    
    # Attempt history shows the story
    attempts = await JobAttempt.query().filter(
        F("job_ulid") == job.ulid
    ).order_by("attempt_number").all()
    assert len(attempts) >= 2
    # First attempt either failed or lease_expired
```

**Run integration tests separately** (they're slower):

```bash
pytest tests/unit/           # Fast, immediate mode
pytest tests/integration/    # Slow, real workers
```

