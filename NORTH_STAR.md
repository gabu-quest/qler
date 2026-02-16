# qler North Star

**One sentence:** Background jobs without Redis, with first-class debugging.

---

## What qler Is

qler is a **background job queue** for Python applications that:

1. **Uses SQLite as the only infrastructure** — No Redis, no RabbitMQ, no external services. Your queue lives in a single file next to your app.

2. **Makes debugging trivial** — Every job execution is tracked. "Why did this fail?" has a complete answer: full attempt history, tracebacks, correlation IDs, and integration with logler for log correlation.

3. **Works with the -ler ecosystem** — sqler for storage, logler for observability, procler for process management. Each tool does one thing well.

4. **Is async-native** — Built for `asyncio` from the ground up. Sync compatibility exists but async is the design center.

---

## What qler Is NOT

| qler is NOT | Why not |
|-------------|---------|
| A distributed queue | SQLite is single-file. For multi-node deployments, use Celery/RQ/etc. |
| A cron scheduler | Periodic tasks are planned (v0.2+) but not the core use case |
| A workflow engine | Job dependencies/DAGs are future scope (see dagler below) |
| A message broker | No pub/sub, no fanout. One job → one worker → one result. |
| Eventually consistent | SQLite is ACID. Jobs don't disappear under load. |
| Production-scale at 10k+ jobs/sec | SQLite tops out around 1-5k writes/sec. That's enough for most apps. |

**If you need:** distributed queues, multi-region, millions of jobs/day → **use Celery + Redis/RabbitMQ**.

qler is for the 90% of projects that don't need that complexity.

---

## Who qler Is For

### Perfect Fit

- **Solo developers** shipping side projects that need background jobs
- **Small teams** running monoliths that don't want Redis/RabbitMQ ops burden
- **Prototypes** that need real job queues but can't afford infrastructure complexity
- **SQLite-first applications** (especially those already using sqler)
- **Debugging-heavy workflows** where "why did this fail at 3am?" matters more than throughput

### Good Fit (With Caveats)

- **Medium-scale apps** (up to ~1000 jobs/minute) that can tolerate SQLite's write throughput
- **Development environments** for apps that use Celery in prod but want simpler local setup

### Not a Fit

- **High-throughput systems** (10k+ jobs/sec)
- **Multi-node deployments** with separate databases per node
- **Exactly-once requirements** (qler is at-least-once)
- **Complex workflow orchestration** (use Prefect, Airflow, Temporal)

---

## Core Principles (Non-Negotiable)

These are the hills we die on. Features that violate these don't get merged.

### 1. SQLite Is The Design Center

Not "SQLite as fallback." Everything is designed around SQLite's strengths:
- Single-file deployment
- ACID transactions
- WAL mode for concurrent reads
- `UPDATE ... RETURNING` for atomic operations

We don't add features that "work better with Postgres."

### 2. Debuggability Over Features

Every design decision asks: "Does this make debugging easier?"
- Full attempt history preserved
- Correlation IDs for every job
- Structured failure kinds (`FailureKind` enum)
- CLI outputs JSON for LLM consumption

We don't add features that hide failure information.

### 3. Async-Native, Sync-Compatible

The primary API is async. Sync is a compatibility layer (`asyncio.to_thread`).
- Task functions should be `async def`
- Worker loop is async
- Database operations are async

We don't optimize for sync-first workflows.

### 4. Explicit Over Magic

No "auto-discovery" of tasks. No implicit configuration.
- Tasks must be explicitly decorated
- Workers must specify which queues to process
- Configuration is code, not YAML edited through dashboards

We don't add features that "just work" without the user understanding how.

### 5. Git-Friendly By Default

- Task definitions are Python code (versioned)
- Queue configuration is code or env vars (versioned)
- Database files are auto-added to `.gitignore` (never versioned)

We don't add UI-only settings that can't be code-reviewed.

---

## The -ler Stack Vision

qler is one piece of a cohesive toolkit:

| Layer | Tool | Responsibility |
|-------|------|----------------|
| **Storage** | sqler | SQLite ORM, migrations, async support |
| **Queues** | **qler** | Background job execution |
| **Logs** | logler | Log aggregation, correlation, investigation |
| **Processes** | procler | Process management, health checks, restarts |
| **Future: DAGs** | dagler | Pipeline orchestration (qler+logler under the hood) |

### Integration Points

```
sqler ──────────────────────────────────────┐
  │                                          │
  │ transactional enqueue                    │
  ▼                                          │
qler ─────────────────────────────────────────┤
  │                                          │
  │ correlation_id                           │ shared SQLite DB
  ▼                                          │ (optional)
logler ──────────────────────────────────────┤
  │                                          │
  │ structured logs                          │
  ▼                                          │
procler ─────────────────────────────────────┘
  │
  │ worker lifecycle
  ▼
qler workers
```

**Key integration:** A job's `correlation_id` connects:
- The job record in qler
- All logs emitted during execution (via logler)
- The worker process (via procler)

"Why did this fail?" → `qler job <id>` → includes `logler_command` → full log context.

---

## Future: dagler (After qler v1.0)

**dagler** = DAG runner built on qler + logler.

```python
from dagler import DAG, task

dag = DAG("etl_pipeline")

@dag.task
async def extract():
    return await fetch_data()

@dag.task(depends_on=[extract])
async def transform(data):
    return process(data)

@dag.task(depends_on=[transform])
async def load(data):
    await write_to_db(data)
```

- Each task node runs as a qler job
- Dependencies expressed as job→job relationships
- logler provides waterfall view of pipeline execution
- procler manages the scheduler process

**Not** before qler is stable. qler v1.0 first.

---

## Success Metrics

qler is successful when:

1. **Setup takes < 5 minutes** — `pip install qler`, define a task, run a worker
2. **Debugging takes < 1 minute** — From "job failed" to understanding why
3. **Documentation fits in one README** — No separate "advanced topics" site
4. **Test suite runs without Docker** — Pure Python + SQLite
5. **A curious developer can read the entire codebase in an afternoon**

---

## What We're Optimizing For

In priority order:

1. **Correctness** — Jobs never silently disappear
2. **Debuggability** — Every failure is explainable
3. **Simplicity** — One file, one dependency (sqler)
4. **Developer experience** — Pleasant API, helpful errors
5. **Performance** — Enough for most apps (not a benchmark champion)

If simplicity conflicts with performance, simplicity wins.

If debuggability conflicts with features, debuggability wins.

---

## The One-Liner Test

Every PR should pass the one-liner test:

> "Does this change help someone answer 'why did my job fail?' faster?"

If yes → probably good.
If no → needs strong justification.


# -ler Stack
Sqler, Logler, Procler are all projects in the -ler ecosystem. Therefore if there's a function they don't have that's totally a blocker (my guess is sqler will have missing features, at the time of writing) then we will justify the change and make the change in the core library. 
We will NOT cheat or fudge around it
    - eg we will NOT start making our own private sqlite tables and forget about sqler
    - we will NOT just read raw files for logs
Improving the core libraries will improve qler together. Cutting corners will not help anyone. 