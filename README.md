# qler

Background jobs without Redis, with first-class debugging.

qler is an async-first background job queue for Python, built on SQLite via [sqler](https://github.com/gabu/sqler).

## Install

```bash
uv add qler
```

## Quick Start

Define a task, enqueue it, and run a worker:

```python
import asyncio
from qler import Queue, task, Worker

queue = Queue("jobs.db")

@task(queue, max_retries=3)
async def send_email(to: str, subject: str, body: str):
    # your email sending logic here
    print(f"Sending to {to}: {subject}")
    return {"sent": True}

async def main():
    # Enqueue a job
    job = await send_email.enqueue(
        to="user@example.com",
        subject="Hello",
        body="Welcome!",
    )
    print(f"Enqueued job {job.ulid}")

    # Start a worker to process jobs
    worker = Worker(queue, queues=["default"], concurrency=4)
    await worker.run()

asyncio.run(main())
```

## CLI

qler ships with a CLI for managing queues and jobs:

```bash
# Initialize a database
qler init --db jobs.db

# Start a worker (--app points to your Queue instance)
qler worker --app myapp.queue --queues default --concurrency 4

# Check queue status
qler status --db jobs.db

# List jobs (with optional filters)
qler jobs --db jobs.db --status failed --limit 10

# Inspect a specific job
qler job <ULID> --db jobs.db

# View attempt history
qler attempts <ULID> --db jobs.db

# Retry failed jobs
qler retry --db jobs.db --all

# Cancel pending jobs
qler cancel --db jobs.db --all

# Purge old completed jobs
qler purge --db jobs.db --older-than 7d

# Health check
qler doctor --db jobs.db

# All commands support --json for machine-readable output
qler status --db jobs.db --json
```

## Testing

qler provides two modes for test-friendly usage:

### Immediate Mode

`Queue(immediate=True)` executes jobs inline during `enqueue()` — no worker needed:

```python
import asyncio
from qler import Queue, task, JobStatus

async def test_email_task():
    queue = Queue(":memory:", immediate=True)

    @task(queue)
    async def send_email(to: str):
        return {"sent_to": to}

    job = await send_email.enqueue(to="test@example.com")

    assert job.status == JobStatus.COMPLETED.value
    assert job.result == {"sent_to": "test@example.com"}
```

### Direct Execution

`task.run_now()` calls the function directly without touching the database:

```python
result = await send_email.run_now(to="test@example.com")
assert result == {"sent_to": "test@example.com"}
```

## Configuration

### Queue Options

```python
queue = Queue(
    "jobs.db",
    immediate=False,             # Execute inline on enqueue (for testing)
    default_lease_duration=300,   # Worker lease timeout in seconds
    default_max_retries=0,       # Default retry count for tasks
    default_retry_delay=60,      # Base retry delay in seconds (exponential backoff)
    max_payload_size=1_000_000,  # Max payload size in bytes
)
```

### Task Options

```python
@task(
    queue,
    queue_name="emails",     # Route to a specific queue
    max_retries=3,           # Override default retry count
    retry_delay=30,          # Override default retry delay
    priority=10,             # Higher priority = claimed first
    lease_duration=600,      # Override default lease timeout
    sync=True,               # For sync functions (runs via asyncio.to_thread)
)
def cpu_bound_task(data):
    return process(data)
```

### Enqueue Options

```python
job = await my_task.enqueue(
    arg1, arg2,
    _delay=60,                         # Delay execution by N seconds
    _eta=1700000000,                   # Execute at specific epoch timestamp
    _priority=5,                       # Override task default priority
    _idempotency_key="order:123",      # Deduplicate by key
    _correlation_id="req-abc-123",     # Link related jobs for debugging
)
```

## The -ler Ecosystem

| Package | Purpose |
|---------|---------|
| [sqler](https://github.com/gabu/sqler) | SQLite ORM (qler's storage layer) |
| **qler** | Background job queue |
| [logler](https://github.com/gabu/logler) | Log aggregation with correlation IDs |

## License

MIT
