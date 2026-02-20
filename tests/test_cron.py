"""Tests for cron scheduling — @cron decorator, CronWrapper, scheduler loop."""

import asyncio
from datetime import datetime, timedelta, timezone

import pytest
import pytest_asyncio
from sqler import AsyncSQLerDB, F

from qler.cron import CronSchedule, CronWrapper, _normalize_catchup, cron
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


async def catchup_task():
    return "caught_up"


async def catchup_latest_task():
    return "latest"


async def catchup_limited_task():
    return "limited"


async def catchup_nohistory_task():
    return "nohistory"


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
    count = await Job.query().filter(
        (F("task") == wrapped.task_path)
        & F("status").in_list([JobStatus.PENDING.value, JobStatus.RUNNING.value])
    ).count()
    assert count == 1
    assert count >= wrapped.schedule.max_running


async def test_cron_max_running_allows(queue):
    """Below limit, scheduler can enqueue normally."""
    wrapped = cron(queue, "* * * * *", max_running=2)(limited_task)

    # Enqueue one — still below max_running of 2
    await wrapped.enqueue()
    count = await Job.query().filter(
        (F("task") == wrapped.task_path)
        & F("status").in_list([JobStatus.PENDING.value, JobStatus.RUNNING.value])
    ).count()
    assert count == 1
    assert count < wrapped.schedule.max_running


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

    worker = Worker(queue, poll_interval=0.05, shutdown_timeout=1.0)
    worker_task = asyncio.create_task(worker.run())
    await asyncio.sleep(0.3)
    worker._running = False
    await asyncio.sleep(0.1)
    worker_task.cancel()
    try:
        await worker_task
    except asyncio.CancelledError:
        pass

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


# ------------------------------------------------------------------
# Catchup parameter validation
# ------------------------------------------------------------------


async def test_catchup_parameter_latest():
    """catchup='latest' normalizes to catchup=1 on CronSchedule."""
    q = Queue(":memory:")
    wrapped = cron(q, "0 * * * *", catchup="latest")(catchup_latest_task)
    assert wrapped.schedule.catchup == 1


async def test_catchup_parameter_int():
    """catchup=3 stored correctly on CronSchedule."""
    q = Queue(":memory:")
    wrapped = cron(q, "0 * * * *", catchup=3)(catchup_task)
    assert wrapped.schedule.catchup == 3


async def test_catchup_false_default():
    """Default is catchup=0 (disabled)."""
    q = Queue(":memory:")
    wrapped = cron(q, "0 * * * *")(hourly_task)
    assert wrapped.schedule.catchup == 0


async def test_catchup_true_rejected():
    """catchup=True raises ConfigurationError."""
    q = Queue(":memory:")
    with pytest.raises(ConfigurationError, match="catchup=True is not supported"):
        cron(q, "0 * * * *", catchup=True)(heartbeat)


async def test_catchup_exceeds_cap():
    """catchup=101 raises ConfigurationError."""
    q = Queue(":memory:")
    with pytest.raises(ConfigurationError, match="catchup must be <= 100"):
        cron(q, "0 * * * *", catchup=101)(heartbeat)


async def test_catchup_zero_rejected():
    """catchup=0 (int) raises ConfigurationError (must be >= 1)."""
    q = Queue(":memory:")
    with pytest.raises(ConfigurationError, match="catchup must be >= 1"):
        cron(q, "0 * * * *", catchup=0)(heartbeat)


async def test_catchup_invalid_type_rejected():
    """catchup='invalid' raises ConfigurationError."""
    q = Queue(":memory:")
    with pytest.raises(ConfigurationError, match="catchup must be"):
        cron(q, "0 * * * *", catchup="invalid")(heartbeat)


# ------------------------------------------------------------------
# missed_runs() helper
# ------------------------------------------------------------------


async def test_missed_runs_returns_timestamps():
    """missed_runs() walks croniter forward and returns correct timestamps."""
    q = Queue(":memory:")
    # Every hour
    wrapped = cron(q, "0 * * * *")(catchup_task)

    # 3 hours ago
    now = datetime(2026, 1, 15, 15, 0, 0, tzinfo=timezone.utc)
    since = datetime(2026, 1, 15, 12, 0, 0, tzinfo=timezone.utc)
    since_ts = int(since.timestamp())
    now_ts = int(now.timestamp())

    runs = wrapped.missed_runs(since_ts, now_ts)
    # Should be 13:00, 14:00, 15:00
    assert len(runs) == 3
    expected = [
        int(datetime(2026, 1, 15, 13, 0, 0, tzinfo=timezone.utc).timestamp()),
        int(datetime(2026, 1, 15, 14, 0, 0, tzinfo=timezone.utc).timestamp()),
        int(datetime(2026, 1, 15, 15, 0, 0, tzinfo=timezone.utc).timestamp()),
    ]
    assert runs == expected


async def test_missed_runs_empty_when_current():
    """No missed runs if since_ts is recent (within the current interval)."""
    q = Queue(":memory:")
    # Every hour
    wrapped = cron(q, "0 * * * *")(catchup_task)

    now = datetime(2026, 1, 15, 15, 30, 0, tzinfo=timezone.utc)
    since = datetime(2026, 1, 15, 15, 0, 0, tzinfo=timezone.utc)
    since_ts = int(since.timestamp())
    now_ts = int(now.timestamp())

    runs = wrapped.missed_runs(since_ts, now_ts)
    # since_ts is the 15:00 run, next would be 16:00 which is > now
    assert len(runs) == 0


async def test_missed_runs_oldest_first():
    """Missed runs are returned in chronological order."""
    q = Queue(":memory:")
    # Every 15 minutes
    wrapped = cron(q, "*/15 * * * *")(catchup_task)

    now = datetime(2026, 1, 15, 16, 0, 0, tzinfo=timezone.utc)
    since = datetime(2026, 1, 15, 15, 0, 0, tzinfo=timezone.utc)
    since_ts = int(since.timestamp())
    now_ts = int(now.timestamp())

    runs = wrapped.missed_runs(since_ts, now_ts)
    # 15:15, 15:30, 15:45, 16:00
    assert len(runs) == 4
    assert runs == sorted(runs)


# ------------------------------------------------------------------
# _find_last_enqueued_ts()
# ------------------------------------------------------------------


async def test_find_last_enqueued_ts(queue):
    """Queries DB, parses idempotency key correctly."""
    wrapped = cron(queue, "0 * * * *", catchup=1)(catchup_task)

    # Seed a job with a cron idempotency key
    ts = int(datetime(2026, 1, 15, 12, 0, 0, tzinfo=timezone.utc).timestamp())
    await wrapped.enqueue(_idempotency_key=wrapped.idempotency_key(ts))

    result = await wrapped._find_last_enqueued_ts()
    assert result == ts


async def test_find_last_enqueued_ts_no_history(queue):
    """Returns None when no jobs exist for this task."""
    wrapped = cron(queue, "0 * * * *", catchup=1)(catchup_nohistory_task)

    result = await wrapped._find_last_enqueued_ts()
    assert result is None


async def test_find_last_enqueued_ts_non_cron_job(queue):
    """Returns None when existing jobs don't have cron idempotency keys."""
    wrapped = cron(queue, "0 * * * *", catchup=1)(catchup_task)

    # Enqueue without cron idempotency key (like a manual enqueue)
    await wrapped.enqueue()

    result = await wrapped._find_last_enqueued_ts()
    assert result is None


# ------------------------------------------------------------------
# Integration: catchup with scheduler loop
# ------------------------------------------------------------------


async def _run_worker_briefly(queue, duration=0.3):
    """Start a worker and let it run briefly, then shut down cleanly."""
    worker = Worker(queue, poll_interval=0.05, shutdown_timeout=1.0)
    worker_task = asyncio.create_task(worker.run())
    await asyncio.sleep(duration)
    worker._running = False
    await asyncio.sleep(0.1)
    worker_task.cancel()
    try:
        await worker_task
    except asyncio.CancelledError:
        pass


async def test_catchup_enqueues_missed(queue):
    """Seed old job, start worker, verify catchup jobs created."""
    # max_running=10 so catchup isn't blocked by the seed job being pending
    wrapped = cron(queue, "0 * * * *", catchup=5, max_running=10)(catchup_task)

    # Seed a job from 4 hours ago
    now = datetime.now(timezone.utc)
    old_ts = int((now - timedelta(hours=4)).replace(minute=0, second=0, microsecond=0).timestamp())
    await wrapped.enqueue(_idempotency_key=wrapped.idempotency_key(old_ts))

    await _run_worker_briefly(queue)

    # Should have the original + up to 5 catchup jobs (at least 2 hourly in 4h window)
    all_jobs = await Job.query().filter(
        F("task") == wrapped.task_path
    ).all()
    # At minimum: original seed + at least 1 catchup job
    assert len(all_jobs) >= 2
    # All should have cron idempotency keys
    for job in all_jobs:
        assert job.idempotency_key is not None
        assert job.idempotency_key.startswith(f"cron:{wrapped.task_path}:")


async def test_catchup_latest_only_one(queue):
    """With catchup='latest' and multiple missed, only 1 is enqueued."""
    wrapped = cron(queue, "0 * * * *", catchup="latest", max_running=10)(catchup_latest_task)

    # Seed a job from 5 hours ago
    now = datetime.now(timezone.utc)
    old_ts = int((now - timedelta(hours=5)).replace(minute=0, second=0, microsecond=0).timestamp())
    await wrapped.enqueue(_idempotency_key=wrapped.idempotency_key(old_ts))

    await _run_worker_briefly(queue)

    all_jobs = await Job.query().filter(
        F("task") == wrapped.task_path
    ).all()
    # The key check: only 1 catchup job (not 4-5), plus possibly the normal tick
    cron_timestamps = set()
    for job in all_jobs:
        ts_str = job.idempotency_key.rsplit(":", 1)[1]
        cron_timestamps.add(int(ts_str))

    # Remove the seed timestamp
    cron_timestamps.discard(old_ts)
    # Should have at most 2 new timestamps (1 catchup + 1 normal tick)
    assert len(cron_timestamps) <= 2


async def test_catchup_respects_max_running(queue):
    """Catchup blocked when at max_running."""
    wrapped = cron(queue, "0 * * * *", catchup=5, max_running=1)(catchup_limited_task)

    # Seed a job from 3 hours ago (PENDING = counts toward max_running)
    now = datetime.now(timezone.utc)
    old_ts = int((now - timedelta(hours=3)).replace(minute=0, second=0, microsecond=0).timestamp())
    await wrapped.enqueue(_idempotency_key=wrapped.idempotency_key(old_ts))

    await _run_worker_briefly(queue)

    all_jobs = await Job.query().filter(
        F("task") == wrapped.task_path
    ).all()
    pending_running = [
        j for j in all_jobs
        if j.status in (JobStatus.PENDING.value, JobStatus.RUNNING.value)
    ]
    # max_running guard should have prevented pile-up
    assert len(pending_running) <= 1


async def test_catchup_idempotent(queue):
    """Running catchup twice doesn't duplicate jobs."""
    wrapped = cron(queue, "0 * * * *", catchup=3, max_running=10)(catchup_task)

    # Seed a job from 2 hours ago
    now = datetime.now(timezone.utc)
    old_ts = int((now - timedelta(hours=2)).replace(minute=0, second=0, microsecond=0).timestamp())
    await wrapped.enqueue(_idempotency_key=wrapped.idempotency_key(old_ts))

    # Run worker twice
    for _ in range(2):
        await _run_worker_briefly(queue, duration=0.2)

    all_jobs = await Job.query().filter(
        F("task") == wrapped.task_path
    ).all()
    # Count unique idempotency keys — no duplicates
    keys = [j.idempotency_key for j in all_jobs]
    assert len(keys) == len(set(keys))


async def test_catchup_no_history_skips(queue):
    """First-ever run with no prior jobs — no catchup, just normal scheduling."""
    wrapped = cron(queue, "* * * * *", catchup=5)(catchup_nohistory_task)

    await _run_worker_briefly(queue)

    all_jobs = await Job.query().filter(
        F("task") == wrapped.task_path
    ).all()
    # Should have at most 1-2 jobs (from normal scheduling), not a flood
    assert len(all_jobs) <= 2
