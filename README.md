# qler

qler is a background job queue for async Python that keeps its jobs in one SQLite file. There is no broker to run: the queue, the job history and every failed attempt are rows you can read with the `qler` command or any SQLite tool. It is built on [sqler](https://github.com/gabu-quest/sqler).

## Install

```bash
pip install qler
pip install 'qler[metrics]'    # adds the Prometheus metrics endpoint
pip install 'qler[logler]'     # adds `qler logs`, a job's log lines through logler
```

Python 3.12 or newer.

## Quick start

A task is a module-level function. Put the queue and the tasks in a module the worker can import:

```python
# tasks.py
from qler import Queue, task

queue = Queue("jobs.db")

@task(queue, max_retries=2, retry_delay=1)
async def send_email(to: str, subject: str):
    if to.endswith("@invalid"):
        raise ValueError(f"cannot deliver to {to}")
    print(f"sending to {to}: {subject}")
    return {"sent": True}
```

Enqueue from anywhere in your application:

```python
# enqueue.py
import asyncio
from tasks import queue, send_email

async def main():
    job = await send_email.enqueue(to="user@example.com", subject="Hello")
    print("enqueued", job.ulid)
    await send_email.enqueue(to="nobody@invalid", subject="Hello")
    await queue.close()

asyncio.run(main())
```

Run a worker:

```bash
python enqueue.py
qler worker --app tasks:queue --queues default --concurrency 4
```

`--app` is `module:attribute`. Ctrl-C lets running jobs finish, then stops.

## Seeing what happened

Every job and every attempt stays in the database. After the quick start:

```console
$ qler status --db jobs.db
Queue    Pending  Running  Completed  Failed  Cancelled
-------  -------  -------  ---------  ------  ---------
default  0        0        1          1       0

Total: 2 jobs

$ qler jobs --db jobs.db --status failed
ULID             Status  Queue    Task              Created               Error
---------------  ------  -------  ----------------  --------------------  --------------------------------
01M7S9R2C6VJ...  failed  default  tasks.send_email  2026-11-19T07:01:14Z  cannot deliver to nobody@invalid

1 job shown

$ qler attempts 01M7S9R2C6VJGT3S4GN330N00T --db jobs.db
#  Status  Worker             Started               Finished              Error
-  ------  -----------------  --------------------  --------------------  --------------------------------
1  failed  myhost:41234:...   2026-11-19T07:01:15Z  2026-11-19T07:01:15Z  cannot deliver to nobody@invalid
2  failed  myhost:41234:...   2026-11-19T07:01:16Z  2026-11-19T07:01:16Z  cannot deliver to nobody@invalid
3  failed  myhost:41234:...   2026-11-19T07:01:18Z  2026-11-19T07:01:18Z  cannot deliver to nobody@invalid
```

`qler job <ULID>` prints the payload, the last error and its kind, the retry count and the lease. Every command takes `--json`. When the cause is fixed: `qler retry <ULID> --db jobs.db`.

The same records can be read from Python. A queue has three read properties, `queue.jobs`, `queue.attempts` and `queue.archive` (jobs moved out by `qler archive`); each is a [sqler](https://github.com/gabu-quest/sqler) query on that queue's own table:

```python
# report.py
import asyncio
from sqler import F
from tasks import queue

async def main():
    await queue.init_db()
    for job in await queue.jobs.filter(F("status") == "failed").all():
        tries = await queue.attempts.filter(F("job_ulid") == job.ulid).count()
        print(job.ulid, job.task, tries)
    print("archived:", await queue.archive.count())
    await queue.close()

asyncio.run(main())
```

They are for reading. To change a job, call its own methods (`cancel`, `retry`) instead of writing through these queries.

## What qler guarantees

- **A job runs at least once.** A worker holds a lease on the job it runs (300 seconds by default) and renews it while the job runs. If the worker dies, the lease runs out and another worker picks the job up again. So a job can run more than once: write tasks that are safe to repeat.
- **A job that raises is retried** up to `max_retries` times, waiting `retry_delay × 2^retry` seconds (plus up to 10% jitter, at most 24 hours) between tries. The default is `max_retries=0`: the job fails on its first error.
- **A lost lease is not a retry.** A job recovered from a dead worker keeps its retry count.
- **After the last retry the job is `failed`** and stays in the database with its error and traceback. With `Queue(..., dlq="dead")` it also moves to that dead-letter queue, where `qler dlq list`, `qler dlq replay` and `qler dlq purge` work on it. A retry or a replay puts it back in the queue it came from.
- **A key names one job, and the database enforces it.** While a job with a `_unique_key` is pending or running, enqueueing that key again returns the same job. An `_idempotency_key` does the same for as long as its job exists and is not cancelled, so a finished job still answers for its key. Two processes that enqueue the same key at the same moment get the same job.
- **Each step is one write.** Claiming a job, finishing it and releasing the jobs that wait on it, and recovering a lost lease each happen in one transaction: a crash leaves a job either before the step or after it.
- **A worker that is stopped lets its jobs finish** for `--shutdown-timeout` seconds (30 by default), then stops the ones still running. A stopped job whose task has ended goes back to the queue at once, so another worker picks it up without waiting for its lease. A task that ignores the stop, or a sync task still running in its thread, keeps its lease and is run again when the lease ends. The job's work counts until it has all ended: its task, the tasks it started and did not wait for (the stop cancels the job's own task only), a sync task's thread, and every function the job handed to the loop's default thread pool with `asyncio.to_thread` or `loop.run_in_executor(None, ...)`. A thread started with `threading.Thread`, a pool the job made itself, or a subprocess is not seen: a job must wait for those itself. To see those submits, `Worker.run()` makes its own pool (the same kind and size as Python's) the loop's default, in place of any default pool the program had set, which it leaves open, and leaves it installed when it returns, so the loop shuts it down as it would its own.

What it does not do:

- **Exactly-once.** See the first point.
- **Many writers.** SQLite allows one writer at a time. qler is for one machine, or a few workers sharing one file on a local disk; it is not for a network file system.
- **Sync tasks without a thread.** `@task(queue, sync=True)` runs a plain function in a thread.

## Features

```python
from qler import cron, task

@task(queue, queue_name="emails", priority=10, timeout=30, rate_limit="100/m")
async def send(to: str): ...

@cron(queue, "*/5 * * * *", timezone_name="UTC")      # every five minutes, one job per slot
async def sweep(): ...

job = await send.enqueue(
    "user@example.com",
    _delay=60,                        # run in 60 seconds (or _eta=<epoch seconds>)
    _priority=5,
    _idempotency_key="order:123",     # the same key returns the existing job
    _unique_key="send:user@example.com",   # at most one pending or running job with this key
    _correlation_id="req-abc-123",    # carried into the job's records and logs
    _depends_on=[other_job.ulid],     # runs after those jobs complete
)
finished = await job.wait(timeout=30)     # raises JobFailedError if it fails
```

Rates are `<number>/<unit>` with `s`, `m`, `h` or `d`. Inside a task, `current_job()` returns the running job, `await set_progress(50, "half way")` records progress, and `await is_cancellation_requested()` says whether someone asked the job to stop.

## Commands

| Command | What it does |
|---|---|
| `qler init` | Create the database |
| `qler worker` | Start a worker (`--health-port`, `--archive-interval`, `--memory-limit-mb`) |
| `qler health` | Ask a running worker for its health |
| `qler status` | Queue depths and job counts |
| `qler jobs`, `qler job`, `qler attempts` | List jobs, show one, show its attempts |
| `qler logs` | A job's log lines (needs `qler[logler]`) |
| `qler retry`, `qler cancel` | Retry failed jobs, cancel pending ones, by id or filter |
| `qler tasks`, `qler cron` | Registered tasks and cron schedules |
| `qler dlq list/count/job/replay/purge` | The dead-letter queue |
| `qler archive`, `qler purge` | Move finished jobs to the archive table, delete old ones |
| `qler backup` | An online backup of the database |
| `qler doctor` | Health checks on the database |

## Testing your tasks

`immediate=True` runs a job inside `enqueue()`, with no worker:

```python
import asyncio
from qler import JobStatus, Queue, task
from sqler import AsyncSQLerDB

db = AsyncSQLerDB.in_memory()
queue = Queue(db, immediate=True)

@task(queue)
async def add(a: int, b: int):
    return a + b

async def main():
    job = await add.enqueue(2, 3)
    assert job.status == JobStatus.COMPLETED.value
    assert job.result == 5

    assert await add.run_now(2, 3) == 5     # calls the function, touches no database
    await db.close()

asyncio.run(main())
```

## Upgrading

A database made by qler 0.5 is upgraded in place the first time the new version opens it. The upgrade is one-way: a 0.5 process refuses to start on the upgraded file, so upgrade every process that shares a file together. If the old file holds two active jobs under one key, the queue still starts, logs an error that names them, and enforces that key only after the extra jobs are cancelled or removed.

## The -ler family

| Package | What it is |
|---|---|
| [sqler](https://github.com/gabu-quest/sqler) | JSON documents in SQLite; qler's storage |
| **qler** | This package: a background job queue |
| [logler](https://github.com/gabu-quest/logler) | Log investigation; `qler logs` uses it, and its `--db` option reads a qler database |

## Development

```bash
uv sync --dev --all-extras
uv run ruff check .
uv run ruff format --check .
uv run mypy
uv run pytest -q
```

## License

MIT
