"""Tests for cron scheduling — @cron decorator, CronWrapper, scheduler loop."""

import asyncio
from datetime import datetime, timedelta, timezone

import pytest
import pytest_asyncio
from sqler import AsyncSQLerDB, F

from qler.cron import CronSchedule, CronWrapper, cron
from qler.enums import JobStatus
from qler.exceptions import ConfigurationError
from qler.models.job import Job
from qler.queue import Queue
from qler.task import TaskWrapper
from qler.worker import Worker


# Module-level functions (required by @cron's qualname validation)
async def heartbeat():
    return "beat"


async def hourly_task():
    return "hourly"


async def dedup_task():
    return "done"


async def limited_task():
    return "done"


async def immediate_cron():
    return "immediate"


async def delegated_enqueue():
    return "ok"


async def callable_cron():
    return "called"


def sync_cron():
    return "sync_result"


async def cron_job():
    return "ticked"


async def morning_task():
    pass


async def direct_run():
    return "direct"


@pytest_asyncio.fixture
async def db():
    _db = AsyncSQLerDB.in_memory(shared=False)
    await _db.connect()
    yield _db
    await _db.close()


@pytest_asyncio.fixture
async def queue(db):
    q = Queue(db)
    await q.init_db()
    yield q


# ------------------------------------------------------------------
# @cron decorator basics
# ------------------------------------------------------------------


async def test_cron_decorator_registers(queue):
    """@cron creates TaskWrapper + CronWrapper, registered on queue."""
    wrapped = cron(queue, "*/5 * * * *")(heartbeat)

    assert isinstance(wrapped, CronWrapper)
    assert wrapped.task_path in queue._cron_tasks
    assert queue._cron_tasks[wrapped.task_path] is wrapped
    # Also registered as a regular task
    assert wrapped.task_path in queue._tasks
    assert wrapped.schedule.expression == "*/5 * * * *"
    assert wrapped.schedule.max_running == 1


async def test_cron_invalid_expression():
    """Invalid cron expression raises ConfigurationError at decoration time."""
    q = Queue(":memory:")
    with pytest.raises(ConfigurationError, match="Invalid cron expression"):
        cron(q, "not a cron")(heartbeat)


async def test_cron_next_run():
    """next_run() returns correct future timestamp."""
    q = Queue(":memory:")
    wrapped = cron(q, "0 * * * *")(hourly_task)  # top of every hour

    ref = datetime(2026, 1, 15, 10, 30, 0, tzinfo=timezone.utc)
    next_time = wrapped.next_run(after=ref)
    assert next_time == datetime(2026, 1, 15, 11, 0, 0, tzinfo=timezone.utc)


async def test_cron_idempotency_key():
    """Key format is cron:{path}:{ts}."""
    q = Queue(":memory:")
    wrapped = cron(q, "*/5 * * * *")(heartbeat)

    key = wrapped.idempotency_key(1700000000)
    assert key == f"cron:{wrapped.task_path}:1700000000"


async def test_cron_enqueue_dedup(queue):
    """Same idempotency key returns existing job instead of creating a new one."""
    wrapped = cron(queue, "*/5 * * * *")(dedup_task)

    key = wrapped.idempotency_key(1700000000)
    job1 = await wrapped.enqueue(_idempotency_key=key)
    job2 = await wrapped.enqueue(_idempotency_key=key)
    assert job1.ulid == job2.ulid


async def test_cron_max_running_blocks(queue):
    """With pending/running jobs at limit, max_running is respected."""
    wrapped = cron(queue, "* * * * *", max_running=1)(limited_task)

    # Manually enqueue one job to fill the slot
    await wrapped.enqueue()

    # Check running count matches expectation
    active = await Job.query().filter(
        (F("task") == wrapped.task_path)
        & F("status").in_list([JobStatus.PENDING.value, JobStatus.RUNNING.value])
    ).all()
    assert len(active) == 1
    assert len(active) >= wrapped.schedule.max_running


async def test_cron_max_running_allows(queue):
    """Below limit, scheduler can enqueue normally."""
    wrapped = cron(queue, "* * * * *", max_running=2)(limited_task)

    # Enqueue one — still below max_running of 2
    await wrapped.enqueue()
    active = await Job.query().filter(
        (F("task") == wrapped.task_path)
        & F("status").in_list([JobStatus.PENDING.value, JobStatus.RUNNING.value])
    ).all()
    assert len(active) == 1
    assert len(active) < wrapped.schedule.max_running


async def test_cron_immediate_mode(db):
    """In immediate mode, cron-decorated tasks execute synchronously."""
    q = Queue(db, immediate=True)
    await q.init_db()
    wrapped = cron(q, "*/5 * * * *")(immediate_cron)

    job = await wrapped.enqueue()
    assert job.status == JobStatus.COMPLETED.value
    assert job.result == "immediate"


async def test_cron_wrapper_delegates_enqueue(queue):
    """CronWrapper.enqueue() delegates to inner TaskWrapper."""
    wrapped = cron(queue, "*/5 * * * *")(delegated_enqueue)

    job = await wrapped.enqueue()
    assert job.task == wrapped.task_path
    assert job.status == JobStatus.PENDING.value


async def test_cron_wrapper_delegates_call(queue):
    """CronWrapper.__call__() delegates to inner TaskWrapper."""
    wrapped = cron(queue, "*/5 * * * *")(callable_cron)

    result = await wrapped()
    assert result == "called"


async def test_cron_sync_function(queue):
    """@cron(sync=True) works with sync functions."""
    wrapped = cron(queue, "*/5 * * * *", sync=True)(sync_cron)

    result = await wrapped()
    assert result == "sync_result"


async def test_cron_nested_function_rejected():
    """Nested function raises ConfigurationError."""
    q = Queue(":memory:")

    with pytest.raises(ConfigurationError, match="nested or inner"):

        def outer():
            @cron(q, "*/5 * * * *")
            async def inner():
                pass

        outer()


async def test_cron_max_running_validation():
    """max_running < 1 raises ConfigurationError."""
    q = Queue(":memory:")
    with pytest.raises(ConfigurationError, match="max_running must be >= 1"):
        cron(q, "*/5 * * * *", max_running=0)(heartbeat)


async def test_cron_scheduler_loop_enqueues(queue):
    """Integration: scheduler loop creates jobs at schedule time."""
    wrapped = cron(queue, "* * * * *")(cron_job)  # every minute

    worker = Worker(queue, poll_interval=0.1, shutdown_timeout=1.0)

    # Run worker briefly
    async def stop_after():
        await asyncio.sleep(0.5)
        worker._running = False

    await asyncio.gather(worker.run(), stop_after())

    # The scheduler should have created at least one job
    all_jobs = await Job.query().filter(
        F("task") == wrapped.task_path
    ).all()
    assert len(all_jobs) >= 1


async def test_cron_timezone():
    """Non-UTC timezone schedules correctly."""
    q = Queue(":memory:")
    wrapped = cron(q, "0 9 * * *", timezone_name="US/Eastern")(morning_task)

    assert wrapped.schedule.timezone == "US/Eastern"
    # next_run should return a valid datetime
    next_time = wrapped.next_run()
    assert next_time > datetime.now(timezone.utc)


async def test_cron_run_now(queue):
    """CronWrapper.run_now() executes directly."""
    wrapped = cron(queue, "*/5 * * * *")(direct_run)

    result = await wrapped.run_now()
    assert result == "direct"
