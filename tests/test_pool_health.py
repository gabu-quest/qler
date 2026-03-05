"""Tests for connection pool health on on-disk databases.

These tests use disk_db/disk_queue fixtures (pool_size=4) to exercise the
connection pool — the exact path that was invisible with :memory: DBs
(pool_size=1). Every test calls assert_pool_healthy() after Worker shutdown.
"""

import asyncio
import re

import pytest
from conftest import assert_pool_healthy
from qler.enums import AttemptStatus, JobStatus
from qler.models.attempt import JobAttempt
from qler.models.job import Job
from qler.task import task
from qler.worker import Worker
from sqler import F

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


def _make_worker(queue, **kwargs):
    defaults = {
        "queues": ["default"],
        "concurrency": 2,
        "poll_interval": 0.01,
        "lease_recovery_interval": 60.0,
        "shutdown_timeout": 2.0,
    }
    defaults.update(kwargs)
    return Worker(queue, **defaults)


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
        try:
            await worker_task
        except asyncio.CancelledError:
            pass


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

        w = _make_worker(disk_queue, concurrency=2)
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
        w = _make_worker(disk_queue, concurrency=1)
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

        w = _make_worker(disk_queue, concurrency=1)
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
            try:
                await worker_task
            except asyncio.CancelledError:
                pass

        # Child should be cascade-cancelled
        await child.refresh()
        assert child.status == JobStatus.CANCELLED.value

        await assert_pool_healthy(disk_db)

    async def test_batch_claim_last_attempt_id_synced(self, disk_db, disk_queue):
        """claim_jobs() returns objects with last_attempt_id set (no refresh needed)."""
        for i in range(3):
            await disk_queue.enqueue(f"task_{i}", queue_name="default")

        jobs = await disk_queue.claim_jobs("w-test", ["default"], n=3)
        assert len(jobs) == 3

        for job in jobs:
            assert job.last_attempt_id is not None, (
                f"Job {job.ulid} has last_attempt_id=None without refresh"
            )
            assert _ULID_PATTERN.match(job.last_attempt_id), (
                f"Expected ULID, got {job.last_attempt_id!r}"
            )

        # Verify attempt records exist with correct data
        for job in jobs:
            attempts = await JobAttempt.query().filter(
                F("job_ulid") == job.ulid
            ).all()
            assert len(attempts) == 1
            assert attempts[0].ulid == job.last_attempt_id
            assert attempts[0].worker_id == "w-test"
            assert attempts[0].attempt_number == 1
            assert attempts[0].status == AttemptStatus.RUNNING.value

        await assert_pool_healthy(disk_db)

    async def test_worker_batch_execution_on_disk(self, disk_db, disk_queue):
        """Full end-to-end: enqueue 5 → Worker processes all → pool healthy."""
        _pool_log.clear()
        wrapped = task(disk_queue)(pool_slow_task)

        for i in range(5):
            await wrapped.enqueue(f"e2e_{i}", duration=0.02)

        w = _make_worker(disk_queue, concurrency=2)
        await _run_worker_until_idle(w, 5)

        assert len(_pool_log) == 5
        assert set(_pool_log) == {f"e2e_{i}" for i in range(5)}

        # All jobs completed
        completed = await Job.query().filter(
            F("status") == JobStatus.COMPLETED.value
        ).count()
        assert completed == 5

        # All attempts finalized
        running_attempts = await JobAttempt.query().filter(
            F("status") == AttemptStatus.RUNNING.value
        ).count()
        assert running_attempts == 0

        await assert_pool_healthy(disk_db)


# ---------------------------------------------------------------------------
# TestPoolHealthMethod
# ---------------------------------------------------------------------------


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
        conn = await disk_db.adapter._pool.get()
        try:
            result = await disk_queue.pool_health()
            assert result["pool_size"] == 4
            assert result["available"] == 3
            assert result["healthy"] is False
        finally:
            await disk_db.adapter._pool.put(conn)

        # After release — healthy again
        result = await disk_queue.pool_health()
        assert result["available"] == 4
        assert result["healthy"] is True

    async def test_health_response_includes_pool(self, disk_db, disk_queue):
        """Worker._health_response() includes pool stats with concrete values."""
        w = _make_worker(disk_queue)
        await disk_queue.init_db()

        # Idle: all connections returned
        resp = w._health_response()
        pool = resp["pool"]
        assert pool["size"] == 4
        assert pool["available"] == 4
        assert pool["healthy"] is True

        # Hold a connection — health response reflects it
        conn = await disk_db.adapter._pool.get()
        try:
            resp = w._health_response()
            pool = resp["pool"]
            assert pool["available"] == 3
            assert pool["healthy"] is False
        finally:
            await disk_db.adapter._pool.put(conn)
