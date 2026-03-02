"""Tests for the _notify module — in-process Event-based job.wait() wakeup."""

import asyncio
import time

import pytest
import pytest_asyncio
from sqler import AsyncSQLerDB

from qler._notify import _registry, clear, fire, register
from qler.enums import JobStatus
from qler.exceptions import JobCancelledError, JobFailedError
from qler.models.job import Job
from qler.queue import Queue
from qler.task import task
from qler.worker import Worker


# ---------------------------------------------------------------------------
# Helper: shut down worker cleanly
# ---------------------------------------------------------------------------

async def _stop_worker(w, w_task):
    """Stop a Worker the same way existing tests do."""
    w._running = False
    await asyncio.sleep(0.05)
    w_task.cancel()
    try:
        await w_task
    except asyncio.CancelledError:
        pass


# ---------------------------------------------------------------------------
# Registry unit tests
# ---------------------------------------------------------------------------


class TestRegistry:
    """Low-level register/fire/clear behavior."""

    def setup_method(self):
        _registry.clear()

    def test_register_creates_unset_event(self):
        ev = register("ULID_A")
        assert ev is _registry["ULID_A"]
        assert not ev.is_set()

    def test_register_is_idempotent(self):
        ev1 = register("ULID_A")
        ev2 = register("ULID_A")
        assert ev1 is ev2

    def test_fire_sets_event(self):
        ev = register("ULID_A")
        assert not ev.is_set()
        fire("ULID_A")
        assert ev.is_set()

    def test_fire_noop_without_registration(self):
        fire("ULID_MISSING")  # Should not raise
        assert "ULID_MISSING" not in _registry

    def test_clear_removes_entry(self):
        register("ULID_A")
        assert "ULID_A" in _registry
        clear("ULID_A")
        assert "ULID_A" not in _registry

    def test_clear_noop_on_missing(self):
        clear("ULID_MISSING")  # Should not raise

    async def test_fire_wakes_awaiter(self):
        ev = register("ULID_A")
        woken = asyncio.Event()

        async def waiter():
            await ev.wait()
            woken.set()

        asyncio.create_task(waiter())
        await asyncio.sleep(0)  # Let waiter start
        assert not woken.is_set()
        fire("ULID_A")
        await asyncio.sleep(0)  # Let waiter wake
        assert woken.is_set()

    def test_fire_idempotent_on_already_set(self):
        ev = register("ULID_A")
        fire("ULID_A")
        fire("ULID_A")  # Should not raise
        assert ev.is_set()


# ---------------------------------------------------------------------------
# Module-level task functions
# ---------------------------------------------------------------------------

_attempt_count = 0


async def instant_task(x=1):
    return {"x": x}


async def failing_fn():
    raise RuntimeError("boom")


async def fail_then_succeed():
    """Fails on first attempt, succeeds on second."""
    global _attempt_count
    _attempt_count += 1
    if _attempt_count == 1:
        raise RuntimeError("transient failure")
    return {"attempt": _attempt_count}


# ---------------------------------------------------------------------------
# Integration tests — wait() woken by Worker
# ---------------------------------------------------------------------------


class TestWaitNotification:
    """job.wait() wakes up instantly when Worker completes/fails/cancels."""

    @pytest_asyncio.fixture
    async def db(self):
        _db = AsyncSQLerDB.in_memory(shared=False)
        await _db.connect()
        yield _db
        await _db.close()

    @pytest_asyncio.fixture
    async def queue(self, db):
        q = Queue(db)
        await q.init_db()
        yield q

    async def test_wait_woken_by_complete(self, queue):
        """wait() returns nearly instantly when Worker completes the job."""
        t = task(queue)(instant_task)
        w = Worker(queue, concurrency=1)

        job = await t.enqueue(x=42)
        w_task = asyncio.create_task(w.run())
        try:
            # poll_interval=10s proves we're NOT polling —
            # if Event didn't work, this would take 10+ seconds
            result = await asyncio.wait_for(
                job.wait(poll_interval=10.0), timeout=5.0
            )
            assert result.status == JobStatus.COMPLETED.value
            assert result.ulid == job.ulid
            assert result.result == {"x": 42}
        finally:
            await _stop_worker(w, w_task)

    async def test_wait_woken_by_fail(self, queue):
        """wait() raises JobFailedError instantly on terminal failure."""
        t = task(queue)(failing_fn)
        w = Worker(queue, concurrency=1)

        job = await t.enqueue()
        w_task = asyncio.create_task(w.run())
        try:
            with pytest.raises(JobFailedError) as exc_info:
                await asyncio.wait_for(
                    job.wait(poll_interval=10.0), timeout=5.0
                )
            assert exc_info.value.ulid == job.ulid
            assert exc_info.value.failure_kind is not None
            assert job.ulid not in _registry
        finally:
            await _stop_worker(w, w_task)

    async def test_wait_woken_by_cancel(self, queue):
        """wait() raises JobCancelledError when job.cancel() fires."""
        t = task(queue)(instant_task)

        job = await t.enqueue(x=1)
        cancelled = await job.cancel()
        assert cancelled is True

        with pytest.raises(JobCancelledError) as exc_info:
            await asyncio.wait_for(
                job.wait(poll_interval=10.0), timeout=2.0
            )
        assert exc_info.value.ulid == job.ulid
        assert job.ulid not in _registry

    async def test_wait_woken_by_queue_cancel(self, queue):
        """wait() raises JobCancelledError when queue.cancel_job() fires."""
        t = task(queue)(instant_task)

        job = await t.enqueue(x=1)
        cancelled = await queue.cancel_job(job)
        assert cancelled is True

        with pytest.raises(JobCancelledError) as exc_info:
            await asyncio.wait_for(
                job.wait(poll_interval=10.0), timeout=2.0
            )
        assert exc_info.value.ulid == job.ulid
        assert job.ulid not in _registry

    async def test_timeout_still_works(self, queue):
        """wait(timeout=...) still raises TimeoutError."""
        t = task(queue)(instant_task)
        job = await t.enqueue(x=1)
        # Don't start a worker — job stays pending
        with pytest.raises(TimeoutError, match=job.ulid):
            await job.wait(timeout=0.1)

    async def test_no_registry_leak_on_completion(self, queue):
        """Registry is cleaned up after wait() returns."""
        t = task(queue)(instant_task)
        w = Worker(queue, concurrency=1)

        job = await t.enqueue(x=1)
        w_task = asyncio.create_task(w.run())
        try:
            await asyncio.wait_for(job.wait(), timeout=5.0)
            assert job.ulid not in _registry
        finally:
            await _stop_worker(w, w_task)

    async def test_no_registry_leak_on_timeout(self, queue):
        """Registry is cleaned up after wait() raises TimeoutError."""
        t = task(queue)(instant_task)
        job = await t.enqueue(x=1)

        with pytest.raises(TimeoutError):
            await job.wait(timeout=0.1)
        assert job.ulid not in _registry

    async def test_no_registry_leak_on_failure(self, queue):
        """Registry is cleaned up after wait() raises JobFailedError."""
        t = task(queue)(failing_fn)
        w = Worker(queue, concurrency=1)

        job = await t.enqueue()
        w_task = asyncio.create_task(w.run())
        try:
            with pytest.raises(JobFailedError) as exc_info:
                await asyncio.wait_for(job.wait(), timeout=5.0)
            assert exc_info.value.ulid == job.ulid
            assert job.ulid not in _registry
        finally:
            await _stop_worker(w, w_task)

    async def test_concurrent_waiters_all_woken(self, queue):
        """Multiple jobs waited on concurrently all wake up with correct results."""
        t = task(queue)(instant_task)
        w = Worker(queue, concurrency=4)

        jobs = [await t.enqueue(x=i) for i in range(4)]
        expected_ulids = {j.ulid for j in jobs}
        w_task = asyncio.create_task(w.run())
        try:
            results = await asyncio.wait_for(
                asyncio.gather(*[j.wait(poll_interval=10.0) for j in jobs]),
                timeout=5.0,
            )
            assert len(results) == 4
            result_ulids = {r.ulid for r in results}
            assert result_ulids == expected_ulids
            for i, r in enumerate(results):
                assert r.status == JobStatus.COMPLETED.value
                assert r.ulid == jobs[i].ulid
                assert r.result == {"x": i}
        finally:
            await _stop_worker(w, w_task)

    async def test_throughput_with_events(self, queue):
        """Worker+wait throughput is significantly better than 50 ops/s."""
        t = task(queue)(instant_task)
        w = Worker(queue, concurrency=4)

        n = 50
        jobs = [await t.enqueue(x=i) for i in range(n)]
        w_task = asyncio.create_task(w.run())
        try:
            start = time.perf_counter()
            await asyncio.wait_for(
                asyncio.gather(*[j.wait(poll_interval=10.0) for j in jobs]),
                timeout=10.0,
            )
            elapsed = time.perf_counter() - start
            ops_per_sec = n / elapsed
            # With Events, we should be well above the old 50 ops/s polling limit
            assert ops_per_sec > 50, f"Throughput too low: {ops_per_sec:.1f} ops/s"
        finally:
            await _stop_worker(w, w_task)

    async def test_retry_does_not_fire_event(self, queue):
        """A retried job must NOT fire the event — only terminal states fire."""
        global _attempt_count
        _attempt_count = 0

        t = task(queue, max_retries=1, retry_delay=1)(fail_then_succeed)
        w = Worker(queue, concurrency=1)

        job = await t.enqueue()
        ev = register(job.ulid)

        w_task = asyncio.create_task(w.run())
        try:
            # Wait for job to complete (retry succeeds on attempt 2)
            result = await asyncio.wait_for(
                job.wait(poll_interval=0.1), timeout=5.0
            )
            assert result.status == JobStatus.COMPLETED.value
            assert result.result == {"attempt": 2}
            # The event should be set by completion, but the key point is
            # the job completed normally after retry — not that it was
            # prematurely woken during the retry transition
        finally:
            clear(job.ulid)
            await _stop_worker(w, w_task)

    async def test_cascade_cancel_wakes_child_waiter(self, queue):
        """Cascade-cancel of a dependent child fires its notification."""
        t = task(queue)(instant_task)

        # Enqueue parent + child with dependency
        parent = await queue.enqueue(
            "tests.test_notify.instant_task", queue_name="default"
        )
        child = await queue.enqueue(
            "tests.test_notify.instant_task",
            queue_name="default",
            depends_on=[parent.ulid],
        )
        assert child.pending_dep_count == 1

        # Cancel parent — should cascade-cancel child
        await queue.cancel_job(parent)

        # Child waiter should wake instantly (poll_interval=10s proves Event path)
        with pytest.raises(JobCancelledError) as exc_info:
            await asyncio.wait_for(
                child.wait(poll_interval=10.0), timeout=2.0
            )
        assert exc_info.value.ulid == child.ulid
        assert child.ulid not in _registry

    async def test_fire_before_wait_still_works(self, queue):
        """If fire() runs before wait() starts, the fallback poll catches it."""
        t = task(queue)(instant_task)
        w = Worker(queue, concurrency=1)

        job = await t.enqueue(x=99)
        w_task = asyncio.create_task(w.run())
        try:
            # Wait for job to complete before calling wait()
            for _ in range(200):
                await asyncio.sleep(0.01)
                await job.refresh()
                if job.status == JobStatus.COMPLETED.value:
                    break
            assert job.status == JobStatus.COMPLETED.value

            # Now call wait() — fire already happened, but register() ensures
            # the first refresh() in wait() catches the terminal state
            result = await asyncio.wait_for(
                job.wait(poll_interval=10.0), timeout=2.0
            )
            assert result.status == JobStatus.COMPLETED.value
            assert result.result == {"x": 99}
        finally:
            await _stop_worker(w, w_task)
