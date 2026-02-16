# qler - Brainstorm

**A job queue where the -ler suite is a first-class citizen.**

---

## North Star

> "I just want background jobs without running Redis."

qler is for developers who:
- Want async job processing without infrastructure overhead
- Value simplicity over scale (until scale matters)
- Already use SQLite for their app (or don't mind it)
- Want to debug failed jobs without digging through disparate systems

**Tagline candidates:**
- "Background jobs, zero infrastructure"
- "The job queue that fits in your repo"
- "SQLite-backed task queue for Python"

---

## The Leifer Factor

Charles Leifer (coleifer) has created some of the most elegant Python libraries:
- **peewee** - Simple ORM that just works
- **huey** - Lightweight task queue
- **walrus** - Redis toolkit
- **sqlite-web** - SQLite browser

His philosophy: do one thing well, minimize dependencies, stay pragmatic.

**Huey is excellent.** So why qler?

| Aspect | Huey | qler (aspirational) |
|--------|------|---------------------|
| Philosophy | Redis-first, SQLite as option | SQLite-first, designed around it |
| Storage | Generic backend interface | Deep sqler integration (JSON models, migrations) |
| Logging | Standard Python logging | logler integration (correlation, investigation) |
| CLI | Functional | LLM-first JSON output |
| Web UI | None built-in | Vue dashboard (like procler/sshler) |
| Worker mgmt | Manual | procler integration |
| Ecosystem | Standalone | Part of -ler suite |

**Not trying to replace Huey** - it's battle-tested and great. qler is for the -ler ecosystem: if you're already using sqler/logler/procler, qler slots in naturally.

---

## Current Landscape

| | qler | Celery | RQ | Huey | Dramatiq | arq |
|---|:---:|:---:|:---:|:---:|:---:|:---:|
| Zero infra (no Redis/RabbitMQ) | ✅ | ❌ | ❌ | ⚠️* | ❌ | ❌ |
| SQLite-native | ✅ | ❌ | ❌ | ⚠️* | ❌ | ❌ |
| LLM-first JSON CLI | ✅ | ❌ | ❌ | ❌ | ❌ | ❌ |
| Free web dashboard | ✅ | ❌💰 | ❌ | ❌ | ❌ | ❌ |
| Built-in log correlation | ✅ | ❌ | ❌ | ❌ | ❌ | ❌ |
| Async workers | ✅ | ✅ | ❌ | ⚠️ | ✅ | ✅✅ |
| Periodic tasks | ✅ | ✅ | ⚠️ | ✅ | ⚠️ | ✅ |
| Retries w/ backoff | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| Priority queues | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| Result storage | ✅ | ✅ | ✅ | ✅ | ⚠️ | ✅ |
| Lightweight | ✅ | ❌ | ✅ | ✅ | ✅ | ✅ |
| Battle-tested | ❌ | ✅✅ | ✅ | ✅ | ✅ | ✅ |

*Huey supports SQLite but positions it as "for development" - Redis is recommended for production. The SQLite backend is a generic adapter, not deeply optimized.

**Where qler wins:** Zero-infra + observability + -ler suite integration + async-native
**Where others win:** Scale, maturity, Redis features (pub/sub, etc.)

### Future: Pub/Sub?

SQLite doesn't have native pub/sub, but options exist if needed later:
- Smart polling with exponential backoff (fine for 99% of use cases)
- `sqlite3_update_hook` for C-level change notifications  
- Unix socket notification layer
- Reality: if you need true pub/sub at scale, you've outgrown SQLite anyway

---

## The Integration Angle

```
┌──────────────────────────────────────────────────────────────────┐
│                         YOUR APP                                 │
│                                                                  │
│   from qler import Queue, task                                   │
│   from sqler import Database                                     │
│                                                                  │
│   db = Database("app.db")                                        │
│   queue = Queue(db)  # Uses same DB, or separate qler.db         │
│                                                                  │
│   @task(queue)                                                   │
│   def send_email(to: str, subject: str, body: str):              │
│       # logler automatically correlates logs to job_id           │
│       logger.info(f"Sending email to {to}")                      │
│       smtp.send(to, subject, body)                               │
│                                                                  │
│   # Enqueue                                                      │
│   job = send_email.enqueue(to="user@example.com", ...)           │
│   print(job.id)  # UUID for tracking                             │
│                                                                  │
└──────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌──────────────────────────────────────────────────────────────────┐
│                         sqler                                    │
│                                                                  │
│   Job model with JSON payload, status, retries, timestamps       │
│   - Optimistic locking prevents double-execution                 │
│   - Full query power: Job.filter(status="failed").all()          │
│   - Migrations handled automatically                             │
│   - Same DB = transactional job creation with your data          │
│                                                                  │
└──────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌──────────────────────────────────────────────────────────────────┐
│                         logler                                   │
│                                                                  │
│   $ logler llm search app.log --correlation-id job_abc123        │
│                                                                  │
│   Shows all logs from that job execution, across retries         │
│   - "Why did this job fail?" → instant answer                    │
│   - Thread tracking through async boundaries                     │
│   - Investigation sessions for complex debugging                 │
│                                                                  │
└──────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌──────────────────────────────────────────────────────────────────┐
│                         procler                                  │
│                                                                  │
│   # config.yaml                                                  │
│   processes:                                                     │
│     qler-worker:                                                 │
│       command: qler worker --queues default,email --concurrency 4│
│       health_check:                                              │
│         type: http                                               │
│         url: http://localhost:8765/health                        │
│       restart: on-failure                                        │
│       replicas: 2                                                │
│                                                                  │
│   Worker lifecycle managed alongside your app processes          │
│                                                                  │
└──────────────────────────────────────────────────────────────────┘
```

---

## Core API Design

### Decorating Tasks

```python
from qler import Queue, task

queue = Queue("qler.db")  # Standalone DB
# OR
queue = Queue(existing_sqler_db)  # Reuse app DB

@task(queue)
async def process_upload(file_id: int, user_id: int):
    """Tasks are async functions (sync supported via compat layer)."""
    file = await File.get(file_id)
    # ... processing ...
    return {"pages": 42, "size_mb": 12.5}

@task(queue, retries=3, retry_delay=60, priority=10)
async def send_notification(user_id: int, message: str):
    """With options."""
    pass

@task(queue, queue_name="critical")
async def charge_payment(order_id: int):
    """Named queue for priority processing."""
    pass

# Sync tasks also supported (run in thread pool)
@task(queue, sync=True)
def legacy_blocking_operation(data: dict):
    """For code that can't be async."""
    pass
```

### Enqueueing

```python
# Fire and forget
send_notification.enqueue(user_id=123, message="Hello")

# Get job handle
job = process_upload.enqueue(file_id=456, user_id=789)
print(job.id)        # "job_abc123"
print(job.status)    # "pending"

# Delay execution
send_notification.enqueue(user_id=123, message="Hi", delay=300)  # 5 min

# Schedule for specific time
from datetime import datetime, timedelta
send_notification.enqueue(
    user_id=123, 
    message="Reminder",
    eta=datetime.now() + timedelta(hours=1)
)

# Transactional enqueue (same DB)
with db.transaction():
    order = Order(user_id=123, total=99.99).save()
    charge_payment.enqueue(order_id=order.id)
    # Job only created if transaction commits!
```

### Job Lifecycle

```python
job = process_upload.enqueue(file_id=456, user_id=789)

# Poll status
job.refresh()
print(job.status)      # pending → running → completed/failed
print(job.result)      # {"pages": 42, "size_mb": 12.5}
print(job.error)       # Exception info if failed
print(job.retries)     # Current retry count
print(job.started_at)  # When execution began
print(job.finished_at) # When execution ended

# Wait for completion (blocking)
result = job.wait(timeout=30)

# Cancel pending job
job.cancel()

# Retry failed job
job.retry()
```

### Periodic Tasks (Cron)

```python
from qler import Queue, task, cron

queue = Queue("qler.db")

@task(queue)
@cron("0 * * * *")  # Every hour
def cleanup_expired_sessions():
    Session.filter(expired=True).delete()

@task(queue)
@cron("0 0 * * *")  # Daily at midnight
def generate_daily_report():
    # ...
    pass

# Or register dynamically
queue.schedule("0 */6 * * *", cleanup_old_jobs)
```

### Workers

```python
# Simple: run in same process (dev mode)
await queue.run_worker()

# Or with uvloop for performance
import uvloop
uvloop.install()
asyncio.run(queue.run_worker())

# Production: CLI
# $ qler worker --db qler.db --queues default,email --concurrency 4
```

---

## CLI Design (LLM-First)

```bash
# Status overview
$ qler status --db app.db
{
  "queues": {
    "default": {"pending": 12, "running": 2, "workers": 4},
    "email": {"pending": 156, "running": 8, "workers": 8},
    "critical": {"pending": 0, "running": 0, "workers": 2}
  },
  "jobs": {
    "total": 45231,
    "pending": 168,
    "running": 10,
    "completed_24h": 2341,
    "failed_24h": 12
  },
  "workers": {
    "active": 14,
    "idle": 0
  }
}

# List jobs with filters
$ qler jobs --status failed --since 1h --limit 10
{
  "jobs": [
    {
      "id": "job_abc123",
      "task": "send_email",
      "status": "failed",
      "error": "ConnectionRefusedError: SMTP server unavailable",
      "retries": 3,
      "created_at": "2026-02-10T08:15:00Z",
      "failed_at": "2026-02-10T08:15:45Z"
    }
  ]
}

# Job details
$ qler job job_abc123
{
  "id": "job_abc123",
  "task": "send_email",
  "queue": "email",
  "payload": {"to": "user@example.com", "subject": "Welcome"},
  "status": "failed",
  "error": "ConnectionRefusedError: ...",
  "traceback": "...",
  "retries": 3,
  "max_retries": 3,
  "created_at": "...",
  "started_at": "...",
  "failed_at": "...",
  "correlation_id": "job_abc123",
  "logler_hint": "logler llm search app.log --correlation-id job_abc123"
}

# Retry failed jobs
$ qler retry --status failed --task send_email
{"retried": 12, "skipped": 0}

# Cancel pending jobs
$ qler cancel --task send_email --older-than 1h
{"cancelled": 45}

# Purge old completed jobs
$ qler purge --status completed --older-than 7d
{"purged": 12453}

# Worker management
$ qler worker --queues default,email --concurrency 4

# Web dashboard
$ qler serve --port 8823
```

---

## Web Dashboard

Vue 3 + Naive UI (consistent with procler/sshler):

### Views

1. **Overview**
   - Queue stats cards (pending/running/completed/failed)
   - Throughput graph (jobs/minute over time)
   - Worker status list
   - Recent failures highlight

2. **Jobs Browser**
   - Filterable table (status, queue, task, date range)
   - Click job → detail panel
   - Bulk actions (retry, cancel, delete)
   - Real-time updates via WebSocket

3. **Job Detail**
   - Payload (pretty JSON)
   - Result or error + traceback
   - Retry history timeline
   - "View in logler" button → deep link to logs

4. **Workers**
   - Active workers with current job
   - CPU/memory usage (if available)
   - Uptime, jobs processed
   - Kill/restart buttons (via procler integration)

5. **Schedules**
   - Cron jobs list
   - Next run time
   - Enable/disable toggle
   - Run now button

6. **Settings**
   - Queue configuration
   - Retention policies
   - Worker defaults

---

## Technical Details

### Job Model (sqler)

```python
class Job(Model):
    id: str                    # UUID
    task: str                  # Function name
    queue: str                 # Queue name
    payload: dict              # JSON args/kwargs
    status: str                # pending/running/completed/failed/cancelled
    priority: int              # Higher = sooner
    result: dict | None        # Return value
    error: str | None          # Exception message
    traceback: str | None      # Full traceback
    retries: int               # Current retry count
    max_retries: int           # Limit
    retry_delay: int           # Seconds between retries
    eta: datetime | None       # Scheduled execution time
    started_at: datetime | None
    finished_at: datetime | None
    worker_id: str | None      # Which worker claimed it
    correlation_id: str        # For logler (defaults to job id)
    created_at: datetime
    updated_at: datetime
```

### Worker Execution (Async-First)

```python
# Claim job atomically (optimistic locking)
async with db.transaction():
    job = await Job.filter(
        status="pending",
        queue__in=worker_queues,
        eta__lte=now()  # Due for execution
    ).order_by("-priority", "created_at").first()
    
    if job:
        # sqler's _version prevents double-claim
        job.status = "running"
        job.worker_id = worker_id
        job.started_at = now()
        await job.save()  # Raises StaleVersionError if claimed by another

# Execute (task functions are async)
try:
    result = await task_func(**job.payload)
    job.status = "completed"
    job.result = result
except Exception as e:
    if job.retries < job.max_retries:
        job.status = "pending"
        job.retries += 1
        job.eta = now() + timedelta(seconds=job.retry_delay * (2 ** job.retries))
    else:
        job.status = "failed"
        job.error = str(e)
        job.traceback = traceback.format_exc()
finally:
    job.finished_at = now()
    await job.save()
```

### Logler Integration

```python
# In qler worker, before executing task:
import logging
import logler

# Set correlation ID for all logs during this job
with logler.correlation_context(job.correlation_id):
    result = task_func(**job.payload)

# Now: logler llm search --correlation-id job_abc123
# Shows exactly the logs from this job execution
```

---

## What qler is NOT

- **Not a Celery replacement for high scale** - If you need 10k jobs/sec, use Celery+Redis
- **Not distributed** - Single SQLite DB (you can use PostgreSQL via sqler's backends if needed later)
- **Not real-time** - Polling-based, not pub/sub (fine for most use cases)
- **Not for giant payloads** - JSON in SQLite, keep payloads small

---

## Target Users

1. **Solo devs / small teams** who want background jobs without DevOps overhead
2. **Prototypers** who want to add job processing fast and iterate
3. **-ler suite users** who want tight integration
4. **SQLite enthusiasts** (the "SQLite is enough" crowd)
5. **AI-assisted developers** who want LLM-friendly tooling

---

## Aspirations

### Short Term (MVP)
- [ ] Core: task decorator, enqueue, **async** worker loop
- [ ] CLI: status, jobs, retry, cancel, worker
- [ ] sqler integration: Job model, migrations
- [ ] Basic retries with exponential backoff
- [ ] Priority queues
- [ ] FastAPI integration example

### Medium Term  
- [ ] Web dashboard (Vue 3)
- [ ] logler integration (correlation IDs)
- [ ] Periodic tasks (cron)
- [ ] procler worker definition
- [ ] Job result storage + expiry
- [ ] Middleware/hooks (before_execute, after_execute)
- [ ] Sync compatibility layer (for non-async apps)

### Long Term
- [ ] Rate limiting per queue
- [ ] Dead letter queue
- [ ] Workflow/chaining (job A → job B → job C)
- [ ] Prometheus metrics
- [ ] Job dependencies ("run after job X completes")
- [ ] PostgreSQL support (maybe, low priority)

---

## Name Alternatives

- **qler** - queue-ler, clean, fits the suite
- **jobbler** - job-ler, a bit awkward
- **taskler** - taken (conceptually) by task runners
- **workler** - worker-ler? meh

**qler** wins.

---

## Open Questions

1. **Separate DB or same DB?** 
   - Same: transactional job creation, simpler
   - Separate: job churn doesn't bloat app DB
   - **Decision: Support both, recommend separate for prod**

2. **How to handle long-running jobs?**
   - Heartbeat mechanism? Job expires if no heartbeat?
   - Or just let SQLite's locking handle it?
   - **TODO: Needs more discussion - pros/cons of each approach**
   - Heartbeat: More complex, but catches stuck workers
   - No heartbeat: Simpler, but orphaned jobs if worker dies mid-execution
   - Maybe: configurable per-task timeout + heartbeat for long tasks only?

3. **Async support priority?**
   - Many Python apps are async now (FastAPI, etc.)
   - ~~Start sync-only, add async workers later?~~
   - **Decision: Async is TOP PRIORITY - FastAPI support is essential**
   - Start with async-first design, sync as compatibility layer
   - Follow arq's lead here (async-native)

4. **PostgreSQL support?**
   - sqler is SQLite-focused but could expand
   - **Decision: TODO but not priority**
   - SQLite covers the target use case (zero-infra)
   - If you need Postgres scale, Celery/Dramatiq are better fits

---

## Inspiration & Prior Art

- **Huey** (coleifer) - Simple, clean API (biggest inspiration)
  - Note: Has SQLite backend but it's positioned as "for dev/testing"
  - Redis is the recommended production backend
  - SQLite support is generic storage adapter, not deeply integrated
  - qler: SQLite-first, not SQLite-as-afterthought
  
- **arq** (Samuel Colvin, Pydantic author) - Async-first design
  - Redis-only, uses Redis streams
  - Type hints, Pydantic integration
  - Very lightweight, modern Python
  - qler should follow arq's async-native approach
  
- **dramatiq** (Bogdan Popa) - Middleware concept
  - "Celery but simpler" pitch
  - Middleware architecture (composable like WSGI)
  - Redis or RabbitMQ backends
  - Actor-model inspired, focus on reliability
  
- **RQ** - Redis simplicity
- **Django-Q** - Django integration patterns
- **Celery** - The 800lb gorilla (what NOT to be - too complex)
- **SQLite as queue** - Various blog posts on using SQLite for queues

---

## Why Now?

- SQLite has gotten very good (WAL, JSON1, concurrent reads)
- "Zero infra" is increasingly valued
- LLM-assisted development wants structured output
- FastAPI is everywhere and it's async - job queues should be too
- The -ler suite exists and ~~wants~~ *demands* a queue
