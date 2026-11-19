"""Tests for connection pool health on on-disk databases.

These tests use disk_db/disk_queue fixtures (pool_size=4) to exercise the
connection pool — the exact path that was invisible with :memory: DBs
(pool_size=1). Every test calls assert_pool_healthy() after Worker shutdown.
"""

import asyncio
import contextlib
import re

import pytest
from conftest import assert_pool_healthy, make_worker
from sqler import F

from qler.enums import AttemptStatus, JobStatus
from qler.models.attempt import JobAttempt
from qler.models.job import Job
from qler.task import task

_ULID_PATTERN = re.compile(r"^[0-9A-Z]{26}$")


# ---------------------------------------------------------------------------
# Module-level task functions (required — @task rejects nested functions)
# ---------------------------------------------------------------------------

_pool_log: list[str] = []


async def pool_task(name):
    _pool_log.append(name)
    return name


async def pool_slow_task(name, duration=0.05):
    _pool_log.append(name)
    await asyncio.sleep(duration)
    return name


async def pool_failing_task():
    raise RuntimeError("intentional failure")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _run_worker_until_idle(worker, expected_count, timeout=10.0):
    """Run worker until expected_count jobs from _pool_log, then stop."""
    worker_task = asyncio.create_task(worker.run())
    try:
        start = asyncio.get_event_loop().time()
        while len(_pool_log) < expected_count:
            elapsed = asyncio.get_event_loop().time() - start
            if elapsed > timeout:
                pytest.fail(
                    f"Timed out after {elapsed:.1f}s: "
                    f"{len(_pool_log)}/{expected_count} jobs completed. "
                    f"Log: {_pool_log}"
                )
            await asyncio.sleep(0.02)
    finally:
        worker._running = False
        await asyncio.sleep(0.05)
        worker_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await worker_task


# ---------------------------------------------------------------------------
# TestWorkerPoolHealth
# ---------------------------------------------------------------------------


class TestWorkerPoolHealth:
    """Verify connection pool is fully returned after Worker shutdown."""

    async def test_worker_returns_all_connections(self, disk_db, disk_queue):
        """Run Worker with concurrency=2, process 6 jobs. Pool must be full after."""
        _pool_log.clear()
        wrapped = task(disk_queue)(pool_task)

        for i in range(6):
            await wrapped.enqueue(f"job_{i}")

        w = make_worker(disk_queue, concurrency=2)
        await _run_worker_until_idle(w, 6)

        assert len(_pool_log) == 6
        assert set(_pool_log) == {f"job_{i}" for i in range(6)}
        await assert_pool_healthy(disk_db)

    async def test_dependency_resolution_no_leak(self, disk_db, disk_queue):
        """Complete a parent job → dep resolution fires → pool stays healthy."""
        _pool_log.clear()
        wrapped = task(disk_queue)(pool_task)

        parent = await wrapped.enqueue("parent")
        child = await disk_queue.enqueue(
            wrapped.task_path,
            args=("child",),
            depends_on=[parent.ulid],
        )

        # Worker completes parent, dep resolution unblocks child, worker completes child
        w = make_worker(disk_queue, concurrency=1)
        await _run_worker_until_idle(w, 2)

        assert set(_pool_log) == {"parent", "child"}

        await child.refresh()
        assert child.status == JobStatus.COMPLETED.value

        await assert_pool_healthy(disk_db)

    async def test_cascade_cancel_no_leak(self, disk_db, disk_queue):
        """Fail a parent → cascade cancel fires → pool stays healthy."""
        _pool_log.clear()
        wrapped_fail = task(disk_queue)(pool_failing_task)
        wrapped_ok = task(disk_queue)(pool_task)

        parent = await wrapped_fail.enqueue()
        child = await disk_queue.enqueue(
            wrapped_ok.task_path,
            args=("child",),
            depends_on=[parent.ulid],
        )

        w = make_worker(disk_queue, concurrency=1)
        worker_task = asyncio.create_task(w.run())
        try:
            # Wait for parent to fail
            for _ in range(200):
                await asyncio.sleep(0.02)
                await parent.refresh()
                if parent.status == JobStatus.FAILED.value:
                    break
            assert parent.status == JobStatus.FAILED.value
        finally:
            w._running = False
            await asyncio.sleep(0.05)
            worker_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await worker_task

        # Child should be cascade-cancelled
        await child.refresh()
        assert child.status == JobStatus.CANCELLED.value

        await assert_pool_healthy(disk_db)

    async def test_worker_batch_execution_on_disk(self, disk_db, disk_queue):
        """Full end-to-end: enqueue 5 → Worker processes all → pool healthy."""
        _pool_log.clear()
        wrapped = task(disk_queue)(pool_slow_task)

        for i in range(5):
            await wrapped.enqueue(f"e2e_{i}", duration=0.02)

        w = make_worker(disk_queue, concurrency=2)
        await _run_worker_until_idle(w, 5)

        assert len(_pool_log) == 5
        assert set(_pool_log) == {f"e2e_{i}" for i in range(5)}

        # All jobs completed
        completed = await Job.query().filter(F("status") == JobStatus.COMPLETED.value).count()
        assert completed == 5

        # All attempts finalized
        running_attempts = (
            await JobAttempt.query().filter(F("status") == AttemptStatus.RUNNING.value).count()
        )
        assert running_attempts == 0

        await assert_pool_healthy(disk_db)


# ---------------------------------------------------------------------------
# TestPoolHealthMethod
# ---------------------------------------------------------------------------


async def _hold_transaction(db):
    """Hold one pooled connection through a public call: a transaction a task keeps open.

    Returns (task, release); call release() and await the task to give it back.
    """
    held = asyncio.Event()
    release = asyncio.Event()

    async def holder():
        async with db.transaction():
            held.set()
            await release.wait()

    task_ = asyncio.create_task(holder())
    await asyncio.wait_for(held.wait(), 5)
    return task_, release


class TestPoolHealthMethod:
    """Verify Queue.pool_health() returns correct pool state."""

    async def test_pool_health_method(self, disk_db, disk_queue):
        """pool_health() reports size, available, and healthy status."""
        # Idle pool: all 4 connections available
        result = await disk_queue.pool_health()
        assert result["pool_size"] == 4
        assert result["available"] == 4
        assert result["healthy"] is True

        # Hold a connection — pool becomes unhealthy
        holder, release = await _hold_transaction(disk_db)
        try:
            result = await disk_queue.pool_health()
            assert result["pool_size"] == 4
            assert result["available"] == 3
            assert result["healthy"] is False
        finally:
            release.set()
            await holder

        # After release — healthy again
        result = await disk_queue.pool_health()
        assert result["available"] == 4
        assert result["healthy"] is True

    async def test_health_response_includes_pool(self, disk_db, disk_queue):
        """Worker._health_response() includes pool stats with concrete values."""
        w = make_worker(disk_queue, concurrency=2)
        await disk_queue.init_db()

        # Idle: all connections returned
        resp = w._health_response()
        pool = resp["pool"]
        assert pool["size"] == 4
        assert pool["available"] == 4
        assert pool["healthy"] is True

        # Hold a connection — health response reflects it
        holder, release = await _hold_transaction(disk_db)
        try:
            resp = w._health_response()
            pool = resp["pool"]
            assert pool["available"] == 3
            assert pool["healthy"] is False
        finally:
            release.set()
            await holder


# ---------------------------------------------------------------------------
# Queue read methods give their connection back
# ---------------------------------------------------------------------------


async def _read_depth(q):
    return await q._query_queue_depth()


async def _read_archive_count(q):
    return await q.archive_count()


async def _read_archived_jobs(q):
    return await q.archived_jobs()


async def _read_pool_health(q):
    return await q.pool_health()


# Every read method of Queue (the rest of its public methods write).
_READS = [
    pytest.param(_read_depth, id="_query_queue_depth"),
    pytest.param(_read_archive_count, id="archive_count"),
    pytest.param(_read_archived_jobs, id="archived_jobs"),
    pytest.param(_read_pool_health, id="pool_health"),
]


class TestQueueReadsReleaseConnection:
    @pytest.mark.parametrize("read", _READS)
    async def test_live_tasks_after_read_do_not_hold_connections(self, disk_db, disk_queue, read):
        hold = asyncio.Event()
        have_read = [asyncio.Event() for _ in range(4)]

        async def reader(mine):
            await read(disk_queue)
            mine.set()
            await hold.wait()

        tasks = [asyncio.create_task(reader(e)) for e in have_read]
        try:
            # Each reader says when its read has returned; no polling.
            await asyncio.wait_for(asyncio.gather(*(e.wait() for e in have_read)), 30)

            job = await asyncio.wait_for(disk_queue.enqueue("m.f"), 2)
            assert job.status == JobStatus.PENDING.value
            await assert_pool_healthy(disk_db)
        finally:
            hold.set()
            await asyncio.gather(*tasks)
        await assert_pool_healthy(disk_db)

    async def test_archived_jobs_does_not_redirect_other_tasks(self, disk_db, disk_queue):
        for i in range(3):
            await disk_queue.enqueue("m.f", args=(i,))
        claimed = await disk_queue.claim_job("w-test", ["default"])
        await disk_queue.complete_job(claimed, "w-test", result="ok")
        await Job.query().filter(F("ulid") == claimed.ulid).update_one(finished_at=1)
        assert await disk_queue.archive_jobs(older_than_seconds=300) == 1
        assert await Job.query().count() == 2

        counts = []
        stop = asyncio.Event()

        async def counter():
            while not stop.is_set():
                counts.append(await Job.query().count())
                await asyncio.sleep(0)

        t = asyncio.create_task(counter())
        try:
            for _ in range(30):
                archived = await disk_queue.archived_jobs()
                assert len(archived) == 1
        finally:
            stop.set()
            await t
        assert counts
        assert set(counts) == {2}
