"""Tests for the Worker class — init, execution, concurrency, shutdown, correlation."""

import asyncio
import contextlib
import json
import logging
import re
import sqlite3
import sys
import threading

import pytest
import pytest_asyncio
from conftest import make_worker, run_worker_until_done
from sqler import AsyncSQLerDB, F

from qler._context import current_job
from qler._time import generate_ulid, now_epoch
from qler.enums import AttemptStatus, FailureKind, JobStatus
from qler.exceptions import ConfigurationError, JobFailedError
from qler.models.attempt import JobAttempt
from qler.models.job import Job
from qler.queue import Queue
from qler.task import task
from qler.worker import Worker

_ULID_PATTERN = re.compile(r"^[0-9A-Z]{26}$")
_WORKER_ID_PATTERN = re.compile(r"^.+:\d+:[0-9A-Z]{26}$")


# ---------------------------------------------------------------------------
# Module-level task functions (required for @task decorator)
# ---------------------------------------------------------------------------

_captured_job_ulid = None  # Sentinel for context var test


async def succeeding_task(x, y=1):
    return {"sum": x + y}


async def failing_task():
    raise RuntimeError("boom")


async def slow_task(duration=1.0):
    await asyncio.sleep(duration)
    return "done"


def sync_task(x):
    return x * 2


async def context_var_task():
    global _captured_job_ulid
    job = current_job()
    _captured_job_ulid = job.ulid
    return "ok"


async def wrong_sig_task(a, b, c):
    return a + b + c


# Module-level tasks for concurrency tests
_execution_log: list[str] = []


async def logging_task(name):
    _execution_log.append(f"{name}_start")
    await asyncio.sleep(0.05)
    _execution_log.append(f"{name}_end")
    return name


# Module-level tasks for correlation tests
_captured_correlation: str | None = None


async def capture_correlation_task():
    global _captured_correlation
    job = current_job()
    _captured_correlation = job.correlation_id
    return "ok"


async def capture_no_correlation_task():
    global _captured_correlation
    job = current_job()
    _captured_correlation = job.correlation_id
    return "ok"


async def timeout_slow_task():
    await asyncio.sleep(10)
    return "should not reach"


async def timeout_fast_task():
    await asyncio.sleep(0.01)
    return "fast"


def timeout_sync_task():
    import time

    time.sleep(3)
    return "should not reach"


# Module-level tasks for shutdown-timeout tests; events are made per test.
_held_started: asyncio.Event | None = None
_held_runs = 0


async def held_task():
    """First run outlives any shutdown timeout; a later run returns at once."""
    global _held_runs
    _held_runs += 1
    _held_started.set()
    if _held_runs == 1:
        await asyncio.Event().wait()
    return "second run"


async def swallowing_task():
    _held_started.set()
    try:
        await asyncio.Event().wait()
    except asyncio.CancelledError:
        return "swallowed"


# Set per test by TestShutdownBounds.
_drain_release: asyncio.Event | None = None
_lease_at_finish: list[tuple[int, int]] = []


async def draining_task():
    """First run waits for the test's release, then records (lease, clock)."""
    global _held_runs
    _held_runs += 1
    if _held_runs > 1:
        return "second run"
    _held_started.set()
    await _drain_release.wait()
    row = await Job.query().filter(F("ulid") == current_job().ulid).first()
    _lease_at_finish.append((row.lease_expires_at, now_epoch()))
    return "first run"


async def stubborn_task():
    """Swallows the shutdown's cancel and keeps waiting."""
    _held_started.set()
    try:
        await asyncio.Event().wait()
    except asyncio.CancelledError:
        await _drain_release.wait()
    return "released"


# Set per test by TestShutdownRelease.
_thread_entered: threading.Event | None = None
_thread_go: threading.Event | None = None
_thread_returned: threading.Event | None = None
_on_cut: asyncio.Event | None = None
_resume: asyncio.Event | None = None
_honour_started: asyncio.Event | None = None
_ignore_started: asyncio.Event | None = None
_ignore_release: asyncio.Event | None = None


def blocking_sync_task():
    """First run blocks its thread until the test sets _thread_go; a later run returns."""
    global _held_runs
    _held_runs += 1
    if _held_runs > 1:
        return "second run"
    _thread_entered.set()
    try:
        _thread_go.wait(30)
        return "first run"
    finally:
        _thread_returned.set()


async def pausing_on_cut_task():
    """Honours the cut, but only after the test sets _resume."""
    _held_started.set()
    try:
        await asyncio.Event().wait()
    except asyncio.CancelledError:
        _on_cut.set()
        await _resume.wait()
        raise


async def honouring_task():
    _honour_started.set()
    await asyncio.Event().wait()


async def ignoring_task():
    _ignore_started.set()
    try:
        await asyncio.Event().wait()
    except asyncio.CancelledError:
        await _ignore_release.wait()
    return "ignored"


class _SignalOnAdd(set):
    """A set that calls `on_add` after each add: tells a test the shutdown cut a job."""

    def __init__(self, on_add):
        super().__init__()
        self._on_add = on_add

    def add(self, item):
        super().add(item)
        self._on_add()


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def worker_db():
    """In-memory sqler database for worker tests."""
    _db = AsyncSQLerDB.in_memory(shared=False)
    await _db.connect()
    yield _db
    await _db.close()


@pytest_asyncio.fixture
async def worker_queue(worker_db):
    """Queue with short lease for fast tests."""
    q = Queue(worker_db, default_lease_duration=5, default_max_retries=0)
    await q.init_db()
    yield q


# ---------------------------------------------------------------------------
# TestWorkerInit
# ---------------------------------------------------------------------------


class TestWorkerInit:
    """Worker.__init__ validation and defaults."""

    def test_worker_id_format(self, worker_queue):
        w = make_worker(worker_queue)
        assert _WORKER_ID_PATTERN.match(w.worker_id), (
            f"Expected hostname:pid:ULID format, got {w.worker_id!r}"
        )

    def test_rejects_empty_queues(self, worker_queue):
        with pytest.raises(ConfigurationError, match="must not be empty"):
            Worker(worker_queue, queues=[])

    def test_rejects_zero_concurrency(self, worker_queue):
        with pytest.raises(ConfigurationError, match="concurrency must be >= 1"):
            Worker(worker_queue, concurrency=0)

    def test_defaults(self, worker_queue):
        w = Worker(worker_queue)
        assert w.queues == ["default"]
        assert w.concurrency == 1
        assert w.poll_interval == 1.0
        assert w.shutdown_timeout == 30.0

    def test_rejects_zero_shutdown_timeout(self, worker_queue):
        with pytest.raises(ConfigurationError, match="shutdown_timeout must be > 0"):
            Worker(worker_queue, shutdown_timeout=0)

    def test_rejects_negative_shutdown_timeout(self, worker_queue):
        with pytest.raises(ConfigurationError, match="shutdown_timeout must be > 0"):
            Worker(worker_queue, shutdown_timeout=-1.0)

    def test_custom_queues(self, worker_queue):
        w = Worker(worker_queue, queues=["high", "low"])
        assert w.queues == ["high", "low"]


# ---------------------------------------------------------------------------
# TestWorkerExecution
# ---------------------------------------------------------------------------


class TestWorkerExecution:
    """Worker claims and executes jobs correctly."""

    async def test_single_job_completes(self, worker_queue):
        wrapped = task(worker_queue)(succeeding_task)
        job = await wrapped.enqueue(10, y=5)

        w = make_worker(worker_queue)
        result = await run_worker_until_done(w, job)
        assert result.status == "completed"
        assert result.result == {"sum": 15}

    async def test_sync_task_executes(self, worker_queue):
        wrapped = task(worker_queue, sync=True)(sync_task)
        job = await wrapped.enqueue(21)

        w = make_worker(worker_queue)
        result = await run_worker_until_done(w, job)
        assert result.status == "completed"
        assert result.result == 42

    async def test_task_not_found(self, worker_queue):
        """Unregistered task path → permanent FAILED with TASK_NOT_FOUND."""
        job = await worker_queue.enqueue("nonexistent.task_fn")

        w = make_worker(worker_queue)
        with pytest.raises(JobFailedError) as exc_info:
            await run_worker_until_done(w, job)
        assert exc_info.value.failure_kind == FailureKind.TASK_NOT_FOUND.value

        await job.refresh()
        assert job.status == "failed"
        assert job.last_failure_kind == FailureKind.TASK_NOT_FOUND.value

    async def test_signature_mismatch(self, worker_queue):
        """Wrong args → permanent FAILED with SIGNATURE_MISMATCH."""
        wrapped = task(worker_queue)(wrong_sig_task)
        # Enqueue with wrong number of args (expects a, b, c)
        job = await worker_queue.enqueue(
            wrapped.task_path,
            args=(1,),  # Missing b and c
        )

        w = make_worker(worker_queue)
        with pytest.raises(JobFailedError) as exc_info:
            await run_worker_until_done(w, job)
        assert exc_info.value.failure_kind == FailureKind.SIGNATURE_MISMATCH.value

        await job.refresh()
        assert job.status == "failed"
        assert job.last_failure_kind == FailureKind.SIGNATURE_MISMATCH.value

    async def test_exception_with_retries(self, worker_queue):
        """Task raises → retry if max_retries allows. First failure retries."""
        q = Queue(worker_queue.db, default_lease_duration=5, default_max_retries=1)
        await q.init_db()
        wrapped = task(q)(failing_task)
        job = await wrapped.enqueue()

        w = make_worker(q)
        worker_task = asyncio.create_task(w.run())
        try:
            # Wait for first attempt to fail and job to go back to pending
            for _ in range(100):
                await asyncio.sleep(0.02)
                await job.refresh()
                if job.retry_count >= 1:
                    break
        finally:
            w._running = False
            await asyncio.sleep(0.05)
            worker_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await worker_task

        # After 1 failure with max_retries=1, job goes back to pending for retry
        assert job.status == "pending"
        assert job.retry_count == 1
        assert job.last_failure_kind == FailureKind.EXCEPTION.value
        assert job.last_error == "boom"

    async def test_exception_exhausted(self, worker_queue):
        """Task raises with no retries → permanent FAILED."""
        wrapped = task(worker_queue)(failing_task)
        job = await wrapped.enqueue()

        w = make_worker(worker_queue)
        with pytest.raises(JobFailedError):
            await run_worker_until_done(w, job)

        await job.refresh()
        assert job.status == "failed"
        assert job.last_failure_kind == FailureKind.EXCEPTION.value
        assert job.last_error == "boom"

    async def test_result_stored(self, worker_queue):
        """Completed job stores result in result_json."""
        wrapped = task(worker_queue)(succeeding_task)
        job = await wrapped.enqueue(3, y=7)

        w = make_worker(worker_queue)
        result = await run_worker_until_done(w, job)
        assert result.result_json is not None
        assert result.result == {"sum": 10}

    async def test_context_var_set(self, worker_queue):
        """current_job() works inside task execution."""
        global _captured_job_ulid
        _captured_job_ulid = None

        wrapped = task(worker_queue)(context_var_task)
        job = await wrapped.enqueue()

        w = make_worker(worker_queue)
        await run_worker_until_done(w, job)

        assert _captured_job_ulid == job.ulid

    async def test_attempt_record_created(self, worker_queue):
        """Completed job has a completed attempt record."""
        wrapped = task(worker_queue)(succeeding_task)
        job = await wrapped.enqueue(1)

        w = make_worker(worker_queue)
        await run_worker_until_done(w, job)

        attempts = await JobAttempt.query().filter(F("job_ulid") == job.ulid).all()
        assert len(attempts) == 1
        assert attempts[0].status == AttemptStatus.COMPLETED.value
        assert attempts[0].worker_id == w.worker_id


# ---------------------------------------------------------------------------
# TestWorkerConcurrency
# ---------------------------------------------------------------------------


class TestWorkerConcurrency:
    """Concurrency control via semaphore."""

    async def test_concurrent_jobs(self, worker_queue):
        """concurrency=2 runs two jobs in parallel."""
        wrapped = task(worker_queue)(slow_task)
        job1 = await wrapped.enqueue(duration=0.1)
        job2 = await wrapped.enqueue(duration=0.1)

        w = make_worker(worker_queue, concurrency=2)
        worker_task = asyncio.create_task(w.run())
        try:
            # Both should complete within ~0.2s (parallel), not ~0.4s (serial)
            await job1.wait(timeout=2.0, poll_interval=0.02)
            await job2.wait(timeout=2.0, poll_interval=0.02)
        finally:
            w._running = False
            worker_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await worker_task

        await job1.refresh()
        await job2.refresh()
        assert job1.status == "completed"
        assert job2.status == "completed"

    async def test_semaphore_limits(self, worker_queue):
        """concurrency=1 processes jobs one at a time."""
        _execution_log.clear()

        wrapped = task(worker_queue)(logging_task)
        job1 = await wrapped.enqueue("a")
        job2 = await wrapped.enqueue("b")

        w = make_worker(worker_queue, concurrency=1)
        worker_task = asyncio.create_task(w.run())
        try:
            await job1.wait(timeout=2.0, poll_interval=0.02)
            await job2.wait(timeout=2.0, poll_interval=0.02)
        finally:
            w._running = False
            worker_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await worker_task

        # With concurrency=1, one job must fully complete before the next starts.
        # Order is non-deterministic (a or b first), but must be serial.
        assert len(_execution_log) == 4
        first_name = _execution_log[0].replace("_start", "")
        second_name = "b" if first_name == "a" else "a"
        assert _execution_log == [
            f"{first_name}_start",
            f"{first_name}_end",
            f"{second_name}_start",
            f"{second_name}_end",
        ]


# ---------------------------------------------------------------------------
# TestWorkerShutdown
# ---------------------------------------------------------------------------


class TestWorkerShutdown:
    """Graceful shutdown behavior."""

    async def test_graceful_completes_running(self, worker_queue):
        """Running jobs finish before shutdown completes."""
        wrapped = task(worker_queue)(slow_task)
        job = await wrapped.enqueue(duration=0.1)

        w = make_worker(worker_queue, shutdown_timeout=5.0)
        worker_task = asyncio.create_task(w.run())

        # Wait for job to start executing
        for _ in range(50):
            await asyncio.sleep(0.02)
            if w._active_jobs:
                break

        # Signal shutdown
        w._running = False
        await asyncio.sleep(0.3)  # Let it drain
        worker_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await worker_task

        await job.refresh()
        assert job.status == "completed"

    async def test_running_flag_stops_claim_loop(self, worker_queue):
        """Setting _running = False causes the worker task to exit cleanly."""
        w = make_worker(worker_queue)
        worker_task = asyncio.create_task(w.run())

        await asyncio.sleep(0.05)
        assert w._running is True

        w._running = False
        # Worker has poll_interval=0.01, so 0.15s is plenty of time to exit
        await asyncio.sleep(0.15)
        assert worker_task.done(), "Worker claim loop did not exit after _running=False"

        # Cleanup: ensure no exception was raised
        if not worker_task.cancelled():
            worker_task.result()  # raises if the task raised


# ---------------------------------------------------------------------------
# TestCorrelation
# ---------------------------------------------------------------------------


class TestCorrelation:
    """Correlation context wraps execution."""

    async def test_correlation_id_used(self, worker_queue):
        """Job with correlation_id uses it for context."""
        global _captured_correlation
        _captured_correlation = None

        wrapped = task(worker_queue)(capture_correlation_task)
        job = await worker_queue.enqueue(
            wrapped.task_path,
            correlation_id="my-trace-123",
        )

        w = make_worker(worker_queue)
        await run_worker_until_done(w, job)

        assert _captured_correlation == "my-trace-123"

    async def test_falls_back_to_ulid(self, worker_queue):
        """Job without correlation_id falls back to job ulid."""
        global _captured_correlation
        _captured_correlation = None

        wrapped = task(worker_queue)(capture_no_correlation_task)
        job = await wrapped.enqueue()

        w = make_worker(worker_queue)
        await run_worker_until_done(w, job)

        # correlation_id defaults to "" for jobs enqueued without one
        assert _captured_correlation == ""


# ---------------------------------------------------------------------------
# TestLeaseRenewalLoop
# ---------------------------------------------------------------------------


class TestLeaseRenewalLoop:
    """Verify the background lease renewal loop extends leases mid-execution."""

    async def test_renewal_extends_lease_for_long_running_job(self, worker_db):
        """Job taking longer than lease_duration completes because renewal extends the lease."""
        q = Queue(worker_db, default_lease_duration=2, default_max_retries=0)
        await q.init_db()
        wrapped = task(q)(slow_task)
        job = await wrapped.enqueue(duration=3.0)

        w = make_worker(q, lease_recovery_interval=60.0)
        worker_task = asyncio.create_task(w.run())

        try:
            # Wait for the job to be claimed
            for _ in range(50):
                await asyncio.sleep(0.02)
                if w._active_jobs:
                    break
            assert len(w._active_jobs) == 1

            # Record the original lease expiry
            await job.refresh()
            original_expiry = job.lease_expires_at

            # Wait long enough for the renewal loop to fire (~2s)
            await asyncio.sleep(2.5)

            # Lease should have been extended beyond the original
            await job.refresh()
            assert job.lease_expires_at > original_expiry, (
                f"Lease was not renewed: original={original_expiry}, current={job.lease_expires_at}"
            )

            # Job should still complete successfully despite taking 3s > 2s lease
            await job.wait(timeout=5.0, poll_interval=0.02)
            assert job.status == "completed"
            assert job.result == "done"
        finally:
            w._running = False
            await asyncio.sleep(0.05)
            worker_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await worker_task


# ---------------------------------------------------------------------------
# TestShutdownTimeout
# ---------------------------------------------------------------------------


class TestShutdownTimeout:
    """A job that outlives shutdown_timeout: a user's cancel, an outcome write, a swallowed cut."""

    @pytest.fixture(autouse=True)
    def _reset_shutdown_state(self):
        global _held_started, _held_runs
        _held_started = asyncio.Event()
        _held_runs = 0
        yield

    async def _start(self, w):
        worker_task = asyncio.create_task(w.run())
        await asyncio.wait_for(_held_started.wait(), timeout=5.0)
        return worker_task

    async def _shut_down(self, w, worker_task):
        w._running = False
        await asyncio.wait_for(worker_task, timeout=5.0)

    async def _row(self, job):
        return await Job.query().filter(F("ulid") == job.ulid).first()

    async def _attempts(self, job):
        attempts = await JobAttempt.query().filter(F("job_ulid") == job.ulid).all()
        return sorted(attempts, key=lambda a: a.attempt_number)

    async def test_user_cancel_during_shutdown_still_fails_cancelled(self, worker_db, caplog):
        """A cancel the user asked for keeps today's outcome when shutdown cuts the job."""
        q = Queue(worker_db, default_lease_duration=30, default_max_retries=3)
        await q.init_db()
        wrapped = task(q)(held_task)
        job = await wrapped.enqueue()

        w = make_worker(q, shutdown_timeout=0.2)
        worker_task = await self._start(w)
        w._running = False  # draining from here on
        assert await q.cancel_job(job) is True
        with caplog.at_level("WARNING", logger="qler.worker"):
            await asyncio.wait_for(worker_task, timeout=5.0)

        row = await self._row(job)
        assert row.status == JobStatus.FAILED.value
        assert row.last_failure_kind == FailureKind.CANCELLED.value
        assert row.retry_count == 0
        attempts = await self._attempts(job)
        assert [(a.status, a.failure_kind) for a in attempts] == [
            (AttemptStatus.FAILED.value, FailureKind.CANCELLED.value),
        ]
        assert (
            "released 0 to be claimed at once, 0 keep their lease for lease recovery, "
            "failed 1 as cancelled"
        ) in caplog.text

    async def test_job_finishing_just_after_timeout_is_recorded_once(self, worker_db, monkeypatch):
        """A job writing its outcome when the timeout passes finishes the write, once."""
        q = Queue(worker_db, default_lease_duration=30, default_max_retries=3)
        await q.init_db()
        wrapped = task(q)(succeeding_task)
        job = await wrapped.enqueue(1)

        entered = asyncio.Event()
        gate = asyncio.Event()
        timeout_passed = asyncio.Event()
        calls = []
        real_complete = q.complete_job
        real_wait = asyncio.wait

        async def gated_complete(*args, **kwargs):
            calls.append(args[0].ulid)
            entered.set()
            await gate.wait()
            return await real_complete(*args, **kwargs)

        async def wait_then_signal(*args, **kwargs):
            res = await real_wait(*args, **kwargs)
            if kwargs.get("timeout") == 0.2:
                timeout_passed.set()
            return res

        monkeypatch.setattr(q, "complete_job", gated_complete)
        monkeypatch.setattr(asyncio, "wait", wait_then_signal)

        w = make_worker(q, shutdown_timeout=0.2)
        worker_task = asyncio.create_task(w.run())
        await asyncio.wait_for(entered.wait(), timeout=5.0)
        w._running = False
        await asyncio.wait_for(timeout_passed.wait(), timeout=5.0)
        gate.set()
        await asyncio.wait_for(worker_task, timeout=5.0)

        row = await self._row(job)
        assert row.status == JobStatus.COMPLETED.value
        assert row.result == {"sum": 2}
        assert calls == [job.ulid]
        attempts = await self._attempts(job)
        assert [a.status for a in attempts] == [AttemptStatus.COMPLETED.value]

    async def test_task_swallowing_the_cancel_completes_once(self, worker_db):
        """A task that catches the shutdown's cancel and returns is completed, once."""
        q = Queue(worker_db, default_lease_duration=30, default_max_retries=3)
        await q.init_db()
        wrapped = task(q)(swallowing_task)
        job = await wrapped.enqueue()

        w = make_worker(q, shutdown_timeout=0.2)
        await self._shut_down(w, await self._start(w))

        row = await self._row(job)
        assert row.status == JobStatus.COMPLETED.value
        assert row.result == "swallowed"
        attempts = await self._attempts(job)
        assert [a.status for a in attempts] == [AttemptStatus.COMPLETED.value]
        assert w._left_for_recovery == []


# ---------------------------------------------------------------------------
# TestShutdownRelease
# ---------------------------------------------------------------------------


class TestShutdownRelease:
    """A job the shutdown cut is claimable at once only when its work has really ended."""

    @pytest.fixture(autouse=True)
    def _reset(self):
        global _held_started, _held_runs, _drain_release
        global _thread_entered, _thread_go, _thread_returned, _on_cut, _resume
        global _honour_started, _ignore_started, _ignore_release
        _held_started = asyncio.Event()
        _held_runs = 0
        _drain_release = asyncio.Event()
        _thread_entered = threading.Event()
        _thread_go = threading.Event()
        _thread_returned = threading.Event()
        _on_cut = asyncio.Event()
        _resume = asyncio.Event()
        _honour_started = asyncio.Event()
        _ignore_started = asyncio.Event()
        _ignore_release = asyncio.Event()
        yield
        _thread_go.set()  # never leave a test's thread blocked

    async def _row(self, job):
        return await Job.query().filter(F("ulid") == job.ulid).first()

    async def _attempt_rows(self, job):
        attempts = await JobAttempt.query().filter(F("job_ulid") == job.ulid).all()
        return [
            (a.status, a.failure_kind, a.error, a.worker_id)
            for a in sorted(attempts, key=lambda a: a.attempt_number)
        ]

    async def _end_lease_now(self, q, job):
        """Move the lease end into the past, as the clock would after the lease length."""
        await q.jobs.filter(F("ulid") == job.ulid).update_one(lease_expires_at=now_epoch() - 1)

    async def test_released_job_is_claimed_before_its_lease_would_end(self, worker_db):
        """An async task that honours the cut: a second worker runs it at once."""
        q = Queue(worker_db, default_lease_duration=30, default_max_retries=3)
        await q.init_db()
        job = await task(q)(held_task).enqueue()

        w1 = make_worker(q, shutdown_timeout=0.2)
        w1_task = asyncio.create_task(w1.run())
        await asyncio.wait_for(_held_started.wait(), timeout=5.0)
        lease_end = (await self._row(job)).lease_expires_at
        w1._running = False
        await asyncio.wait_for(w1_task, timeout=5.0)

        w2 = make_worker(q)
        result = await run_worker_until_done(w2, job)
        assert now_epoch() < lease_end
        assert result.status == JobStatus.COMPLETED.value
        assert result.result == "second run"
        assert result.retry_count == 0
        assert _held_runs == 2
        assert await self._attempt_rows(job) == [
            (
                AttemptStatus.LEASE_EXPIRED.value,
                FailureKind.LEASE_EXPIRED.value,
                "Lease expired",
                w1.worker_id,
            ),
            (AttemptStatus.COMPLETED.value, None, None, w2.worker_id),
        ]

    async def test_release_equals_recovery_after_the_lease_ends(self, worker_db):
        """Two copies of one job with no retries left: released and recovered end alike."""
        q = Queue(worker_db, default_lease_duration=30, default_max_retries=0)
        await q.init_db()
        wrapped = task(q)(held_task)
        # The copy for recovery is claimed by a worker that is gone, and never run.
        recovered = await wrapped.enqueue()
        claimed = await q.claim_job("gone-worker", ["default"])
        assert claimed.ulid == recovered.ulid
        released = await wrapped.enqueue()

        w = make_worker(q, shutdown_timeout=0.2)
        w_task = asyncio.create_task(w.run())
        await asyncio.wait_for(_held_started.wait(), timeout=5.0)
        w._running = False
        await asyncio.wait_for(w_task, timeout=5.0)

        await self._end_lease_now(q, recovered)
        assert await q.recover_expired_leases() == 1

        def state(row):
            return (
                row.status,
                row.retry_count,
                row.worker_id,
                row.lease_expires_at,
                row.progress,
                row.progress_message,
                row.last_failure_kind,
                row.last_error,
            )

        a, b = await self._row(released), await self._row(recovered)
        assert state(a) == (JobStatus.PENDING.value, 0, "", None, None, "", None, None)
        assert state(a) == state(b)
        rows_a = [r[:3] for r in await self._attempt_rows(released)]
        rows_b = [r[:3] for r in await self._attempt_rows(recovered)]
        assert rows_a == [
            (AttemptStatus.LEASE_EXPIRED.value, FailureKind.LEASE_EXPIRED.value, "Lease expired")
        ]
        assert rows_a == rows_b

    async def test_task_ignoring_the_cut_keeps_its_lease(self, worker_db):
        """An async task that swallows the cancel and runs on: not claimable."""
        import qler.worker as worker_module

        q = Queue(worker_db, default_lease_duration=30, default_max_retries=3)
        await q.init_db()
        job = await task(q)(ignoring_task).enqueue()

        w = make_worker(q, shutdown_timeout=0.2)
        w_task = asyncio.create_task(w.run())
        await asyncio.wait_for(_ignore_started.wait(), timeout=5.0)
        stuck = set(w._active_tasks)
        before = await self._row(job)
        try:
            with pytest.MonkeyPatch.context() as mp:
                mp.setattr(worker_module, "SHUTDOWN_CUT_GRACE", 0.3)
                w._running = False
                await asyncio.wait_for(asyncio.shield(w_task), timeout=5.0)
            after = await self._row(job)
            other = await q.claim_job("other-worker", ["default"])
        finally:
            _ignore_release.set()
            await asyncio.gather(*stuck, return_exceptions=True)

        assert other is None
        assert (after.status, after.worker_id, after.lease_expires_at) == (
            JobStatus.RUNNING.value,
            w.worker_id,
            before.lease_expires_at,
        )

    async def test_sync_task_still_in_its_thread_keeps_its_lease(
        self, worker_db, monkeypatch, caplog
    ):
        """A cancelled to_thread await ends while the thread runs on: the job keeps its lease."""
        import qler.worker as worker_module

        monkeypatch.setattr(worker_module, "SHUTDOWN_CUT_GRACE", 0.3)
        q = Queue(worker_db, default_lease_duration=30, default_max_retries=3)
        await q.init_db()
        job = await task(q, sync=True)(blocking_sync_task).enqueue()

        w = make_worker(q, shutdown_timeout=0.2)
        w_task = asyncio.create_task(w.run())
        try:
            assert await asyncio.to_thread(_thread_entered.wait, 5.0) is True
            before = await self._row(job)
            with caplog.at_level("WARNING", logger="qler.worker"):
                w._running = False
                await asyncio.wait_for(w_task, timeout=5.0)
            thread_returned_at_exit = _thread_returned.is_set()
            after = await self._row(job)
            other = await q.claim_job("other-worker", ["default"])
        finally:
            # End the thread here: the loop's teardown would wait for it.
            _thread_go.set()
            await asyncio.to_thread(_thread_returned.wait, 5.0)

        assert thread_returned_at_exit is False
        assert other is None
        assert (after.status, after.worker_id, after.lease_expires_at) == (
            JobStatus.RUNNING.value,
            w.worker_id,
            before.lease_expires_at,
        )
        assert (
            "released 0 to be claimed at once, 1 keep their lease for lease recovery" in caplog.text
        )
        kept = [
            r.getMessage() for r in caplog.records if "job(s) keep their lease" in r.getMessage()
        ]
        assert len(kept) == 1
        assert kept[0].endswith(job.ulid)

    async def test_sync_task_whose_thread_ends_in_the_grace_is_released(self, worker_db):
        """The thread returns after the cut, inside the grace: the job is claimable."""
        q = Queue(worker_db, default_lease_duration=30, default_max_retries=3)
        await q.init_db()
        job = await task(q, sync=True)(blocking_sync_task).enqueue()

        w = make_worker(q, shutdown_timeout=0.2)
        w._cut_by_shutdown = _SignalOnAdd(_thread_go.set)
        w_task = asyncio.create_task(w.run())
        assert await asyncio.to_thread(_thread_entered.wait, 5.0) is True
        w._running = False
        await asyncio.wait_for(w_task, timeout=5.0)

        row = await self._row(job)
        assert _thread_returned.is_set() is True
        assert (row.status, row.worker_id, row.lease_expires_at, row.retry_count) == (
            JobStatus.PENDING.value,
            "",
            None,
            0,
        )

    async def test_job_recovered_before_the_release_is_left_alone(self, worker_db, caplog):
        """Lease recovery and another worker's claim came first: the guarded write changes nothing."""
        q = Queue(worker_db, default_lease_duration=30, default_max_retries=3)
        await q.init_db()
        job = await task(q)(pausing_on_cut_task).enqueue()

        w = make_worker(q, shutdown_timeout=0.2)
        w_task = asyncio.create_task(w.run())
        await asyncio.wait_for(_held_started.wait(), timeout=5.0)
        w._running = False
        await asyncio.wait_for(_on_cut.wait(), timeout=5.0)
        await self._end_lease_now(q, job)
        assert await q.recover_expired_leases() == 1
        other = await q.claim_job("other-worker", ["default"])
        assert other.ulid == job.ulid
        _resume.set()
        with caplog.at_level("WARNING", logger="qler.worker"):
            await asyncio.wait_for(w_task, timeout=5.0)

        row = await self._row(job)
        assert (row.status, row.worker_id) == (JobStatus.RUNNING.value, "other-worker")
        assert [(r[0], r[3]) for r in await self._attempt_rows(job)] == [
            (AttemptStatus.LEASE_EXPIRED.value, w.worker_id),
            (AttemptStatus.RUNNING.value, "other-worker"),
        ]
        assert (
            "released 0 to be claimed at once, 0 keep their lease for lease recovery" in caplog.text
        )

    async def test_failed_release_is_logged_and_left_for_recovery(
        self, worker_db, monkeypatch, caplog
    ):
        """The release write raises: one error naming the job; recovery takes it later."""
        q = Queue(worker_db, default_lease_duration=30, default_max_retries=3)
        await q.init_db()
        job = await task(q)(held_task).enqueue()

        async def locked(*args, **kwargs):
            raise sqlite3.OperationalError("database is locked")

        monkeypatch.setattr(q, "_release_lease", locked)
        w = make_worker(q, shutdown_timeout=0.2)
        w_task = asyncio.create_task(w.run())
        await asyncio.wait_for(_held_started.wait(), timeout=5.0)
        with caplog.at_level("WARNING", logger="qler.worker"):
            w._running = False
            await asyncio.wait_for(w_task, timeout=5.0)

        errors = [
            r.getMessage()
            for r in caplog.records
            if r.name == "qler.worker" and r.levelno == logging.ERROR
        ]
        assert errors == [
            f"Could not release job {job.ulid} after the shutdown cut; "
            "left it running for lease recovery"
        ]
        row = await self._row(job)
        assert (row.status, row.worker_id) == (JobStatus.RUNNING.value, w.worker_id)
        await self._end_lease_now(q, job)
        assert await q.recover_expired_leases() == 1
        assert (await self._row(job)).status == JobStatus.PENDING.value

    async def test_hanging_release_does_not_hold_the_shutdown(self, worker_db, monkeypatch, caplog):
        """A release that never returns is given up at the drain's bound."""
        import qler.worker as worker_module

        monkeypatch.setattr(worker_module, "SHUTDOWN_CUT_GRACE", 0.5)
        q = Queue(worker_db, default_lease_duration=30, default_max_retries=3)
        await q.init_db()
        job = await task(q)(held_task).enqueue()

        async def hangs(*args, **kwargs):
            await asyncio.Event().wait()

        monkeypatch.setattr(q, "_release_lease", hangs)
        w = make_worker(q, shutdown_timeout=0.2)
        w_task = asyncio.create_task(w.run())
        await asyncio.wait_for(_held_started.wait(), timeout=5.0)
        loop = asyncio.get_running_loop()
        with caplog.at_level("WARNING", logger="qler.worker"):
            started = loop.time()
            w._running = False
            await asyncio.wait_for(w_task, timeout=5.0)
            elapsed = loop.time() - started

        # timeout 0.2 + grace 0.5, with room for a busy machine
        assert elapsed < 0.2 + 0.5 + 1.5
        errors = [r.getMessage() for r in caplog.records if r.levelno == logging.ERROR]
        assert len(errors) == 1
        assert job.ulid in errors[0]
        row = await self._row(job)
        assert (row.status, row.worker_id) == (JobStatus.RUNNING.value, w.worker_id)

    async def test_two_jobs_cut_one_released_one_kept(self, worker_db, monkeypatch, caplog):
        """One task honours the cut, one ignores it: the log counts and names them."""
        import qler.worker as worker_module

        monkeypatch.setattr(worker_module, "SHUTDOWN_CUT_GRACE", 0.3)
        q = Queue(worker_db, default_lease_duration=30, default_max_retries=3)
        await q.init_db()
        honoured = await task(q)(honouring_task).enqueue()
        ignored = await task(q)(ignoring_task).enqueue()

        w = make_worker(q, shutdown_timeout=0.2, concurrency=2)
        w_task = asyncio.create_task(w.run())
        await asyncio.wait_for(_honour_started.wait(), timeout=5.0)
        await asyncio.wait_for(_ignore_started.wait(), timeout=5.0)
        stuck = set(w._active_tasks)
        try:
            with caplog.at_level("WARNING", logger="qler.worker"):
                w._running = False
                await asyncio.wait_for(asyncio.shield(w_task), timeout=5.0)
            row_h, row_i = await self._row(honoured), await self._row(ignored)
        finally:
            _ignore_release.set()
            await asyncio.gather(*stuck, return_exceptions=True)

        assert (row_h.status, row_h.worker_id) == (JobStatus.PENDING.value, "")
        assert (row_i.status, row_i.worker_id) == (JobStatus.RUNNING.value, w.worker_id)
        assert (
            "with 2 job(s) unfinished: released 1 to be claimed at once, "
            "1 keep their lease for lease recovery, failed 0 as cancelled"
        ) in caplog.text
        kept = [
            r.getMessage() for r in caplog.records if "job(s) keep their lease" in r.getMessage()
        ]
        assert len(kept) == 1
        assert kept[0].endswith(f": {ignored.ulid}")

    async def test_a_worker_run_again_counts_only_what_its_own_shutdown_cut(
        self, worker_db, caplog
    ):
        """A worker object run a second time: the first run's cut job is not this run's."""
        global _held_runs, _held_started
        q = Queue(worker_db, default_lease_duration=30, default_max_retries=3)
        await q.init_db()
        wrapped = task(q)(held_task)
        first = await wrapped.enqueue()

        w = make_worker(q, shutdown_timeout=0.2)
        run = asyncio.create_task(w.run())
        await asyncio.wait_for(_held_started.wait(), timeout=5.0)
        w._running = False
        await asyncio.wait_for(run, timeout=5.0)
        # Another worker finishes the released job, so the second run never holds it.
        await run_worker_until_done(make_worker(q), first)

        _held_runs = 0
        _held_started = asyncio.Event()
        second = await wrapped.enqueue()
        run = asyncio.create_task(w.run())
        await asyncio.wait_for(_held_started.wait(), timeout=5.0)
        with caplog.at_level("WARNING", logger="qler.worker"):
            w._running = False
            await asyncio.wait_for(run, timeout=5.0)

        row = await self._row(second)
        assert (row.status, row.worker_id) == (JobStatus.PENDING.value, "")
        assert (
            "with 1 job(s) unfinished: released 1 to be claimed at once, "
            "0 keep their lease for lease recovery, failed 0 as cancelled"
        ) in caplog.text


# ---------------------------------------------------------------------------
# TestMalformedPayload
# ---------------------------------------------------------------------------


class TestMalformedPayload:
    """Verify worker handles payloads with valid JSON but wrong structure."""

    async def test_non_dict_payload_fails_as_payload_invalid(self, worker_db):
        """Valid JSON but non-dict payload (e.g., '123') → PAYLOAD_INVALID."""
        q = Queue(worker_db, default_lease_duration=5, default_max_retries=0)
        await q.init_db()
        # Register a task so it's found (we want to hit the payload parse path, not TASK_NOT_FOUND)
        wrapped = task(q)(succeeding_task)

        # Manually insert a job with a non-dict payload that passes the poison pill
        # check (json.loads("123") succeeds) but fails .get() in _execute_job
        now = now_epoch()
        job = Job(
            ulid=generate_ulid(),
            status=JobStatus.PENDING.value,
            queue_name="default",
            task=wrapped.task_path,
            payload_json="123",
            max_retries=0,
            retry_delay=60,
            lease_duration=5,
            created_at=now,
            updated_at=now,
            eta=now,
        )
        await job.save()

        w = make_worker(q)
        with pytest.raises(JobFailedError) as exc_info:
            await run_worker_until_done(w, job)
        assert exc_info.value.failure_kind == FailureKind.PAYLOAD_INVALID.value

        await job.refresh()
        assert job.status == "failed"
        assert job.last_failure_kind == FailureKind.PAYLOAD_INVALID.value

    async def test_null_payload_fails_as_payload_invalid(self, worker_db):
        """JSON null payload → PAYLOAD_INVALID."""
        q = Queue(worker_db, default_lease_duration=5, default_max_retries=0)
        await q.init_db()
        wrapped = task(q)(succeeding_task)

        now = now_epoch()
        job = Job(
            ulid=generate_ulid(),
            status=JobStatus.PENDING.value,
            queue_name="default",
            task=wrapped.task_path,
            payload_json="null",
            max_retries=0,
            retry_delay=60,
            lease_duration=5,
            created_at=now,
            updated_at=now,
            eta=now,
        )
        await job.save()

        w = make_worker(q)
        with pytest.raises(JobFailedError) as exc_info:
            await run_worker_until_done(w, job)
        assert exc_info.value.failure_kind == FailureKind.PAYLOAD_INVALID.value

        await job.refresh()
        assert job.status == "failed"
        assert job.last_failure_kind == FailureKind.PAYLOAD_INVALID.value


# ---------------------------------------------------------------------------
# TestRetryExhaustion
# ---------------------------------------------------------------------------


class TestRetryExhaustion:
    """Verify the full retry exhaustion cycle through the worker."""

    async def test_retry_then_permanent_failure(self, worker_db):
        """max_retries=1: attempt 1 fails → retry → attempt 2 fails → permanent FAILED."""
        q = Queue(
            worker_db,
            default_lease_duration=5,
            default_max_retries=1,
            default_retry_delay=1,
        )
        await q.init_db()
        wrapped = task(q)(failing_task)
        job = await wrapped.enqueue()

        w = make_worker(q)
        # job.wait() will raise JobFailedError once retries are exhausted
        with pytest.raises(JobFailedError):
            await run_worker_until_done(w, job, timeout=10.0)

        await job.refresh()
        assert job.status == "failed"
        assert job.retry_count == 1
        assert job.attempts == 2
        assert job.last_failure_kind == FailureKind.EXCEPTION.value
        assert job.last_error == "boom"

        # Verify both attempt records
        attempts = await JobAttempt.query().filter(F("job_ulid") == job.ulid).order_by("ulid").all()
        assert len(attempts) == 2
        assert attempts[0].status == AttemptStatus.FAILED.value
        assert attempts[0].attempt_number == 1
        assert attempts[1].status == AttemptStatus.FAILED.value
        assert attempts[1].attempt_number == 2


# ---------------------------------------------------------------------------
# TestHealthEndpoint
# ---------------------------------------------------------------------------


async def _http_get(host: str, port: int, path: str) -> tuple[int, dict]:
    """Make an HTTP GET request and return (status_code, parsed_json_body)."""
    reader, writer = await asyncio.open_connection(host, port)
    try:
        writer.write(f"GET {path} HTTP/1.0\r\nHost: {host}\r\n\r\n".encode())
        await writer.drain()
        data = await reader.read()
        text = data.decode("utf-8")
        status_line = text.split("\r\n", 1)[0]
        status_code = int(status_line.split(" ", 2)[1])
        body = text.split("\r\n\r\n", 1)[1]
        return status_code, json.loads(body)
    finally:
        writer.close()
        await writer.wait_closed()


async def _unix_http_get(socket_path: str, path: str) -> tuple[int, dict]:
    """Make an HTTP GET request over a Unix socket."""
    reader, writer = await asyncio.open_unix_connection(socket_path)
    try:
        writer.write(f"GET {path} HTTP/1.0\r\nHost: localhost\r\n\r\n".encode())
        await writer.drain()
        data = await reader.read()
        text = data.decode("utf-8")
        status_line = text.split("\r\n", 1)[0]
        status_code = int(status_line.split(" ", 2)[1])
        body = text.split("\r\n\r\n", 1)[1]
        return status_code, json.loads(body)
    finally:
        writer.close()
        await writer.wait_closed()


class TestHealthEndpoint:
    """Health endpoint: TCP and Unix socket, JSON response, lifecycle."""

    def test_constructor_rejects_both_port_and_socket(self, worker_queue):
        with pytest.raises(ConfigurationError, match="mutually exclusive"):
            Worker(worker_queue, health_port=9100, health_socket="/tmp/h.sock")

    def test_constructor_rejects_invalid_port_zero(self, worker_queue):
        with pytest.raises(ConfigurationError, match="health_port must be between 1 and 65535"):
            Worker(worker_queue, health_port=0)

    def test_constructor_rejects_invalid_port_too_high(self, worker_queue):
        with pytest.raises(ConfigurationError, match="health_port must be between 1 and 65535"):
            Worker(worker_queue, health_port=70000)

    def test_status_property_before_run(self, worker_queue):
        """Before run(), _running is False → status is 'draining'."""
        w = make_worker(worker_queue)
        assert w.status == "draining"

    def test_started_at_set_on_init(self, worker_queue):
        before = now_epoch()
        w = make_worker(worker_queue)
        after = now_epoch()
        assert before <= w._started_at <= after

    async def test_health_not_started_without_flag(self, worker_queue):
        """No health server starts without explicit port/socket."""
        w = make_worker(worker_queue)
        worker_task = asyncio.create_task(w.run())
        await asyncio.sleep(0.05)
        try:
            # No health background task should exist (only lease renewal + recovery)
            health_tasks = [t for t in w._background_tasks if "health" in (t.get_name() or "")]
            # The health task won't have "health" in name by default, but
            # the total count should be exactly 2 (lease renewal + recovery)
            assert len(w._background_tasks) == 2
        finally:
            w._running = False
            await asyncio.sleep(0.05)
            worker_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await worker_task

    async def test_health_tcp_responds(self, worker_queue):
        """TCP health endpoint returns 200 with correct JSON fields."""
        port = 19100
        before = now_epoch()
        w = make_worker(worker_queue, health_port=port)
        after = now_epoch()
        worker_task = asyncio.create_task(w.run())
        await asyncio.sleep(0.15)  # Let server start

        try:
            status_code, data = await _http_get("127.0.0.1", port, "/health")
            assert status_code == 200
            assert data["status"] == "healthy"
            assert data["worker_id"] == w.worker_id
            assert data["concurrency"] == w.concurrency
            assert data["queues"] == w.queues
            assert data["active_jobs"] == 0
            assert data["started_at"] == w._started_at
            assert before <= data["started_at"] <= after
            assert 0 <= data["uptime_seconds"] <= (now_epoch() - before) + 1
        finally:
            w._running = False
            await asyncio.sleep(0.1)
            worker_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await worker_task

    @pytest.mark.skipif(sys.platform == "win32", reason="Unix sockets not available on Windows")
    async def test_health_unix_socket_responds(self, worker_queue, tmp_path):
        """Unix socket health endpoint returns 200 with correct JSON."""
        sock_path = str(tmp_path / "health.sock")
        w = make_worker(worker_queue, health_socket=sock_path)
        worker_task = asyncio.create_task(w.run())
        await asyncio.sleep(0.15)

        try:
            status_code, data = await _unix_http_get(sock_path, "/health")
            assert status_code == 200
            assert data["status"] == "healthy"
            assert data["worker_id"] == w.worker_id
            assert data["concurrency"] == w.concurrency
            assert data["queues"] == w.queues
        finally:
            w._running = False
            await asyncio.sleep(0.1)
            worker_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await worker_task

    @pytest.mark.skipif(sys.platform == "win32", reason="Unix sockets not available on Windows")
    async def test_health_unix_socket_cleanup(self, worker_queue, tmp_path):
        """Socket file is removed after worker stops."""
        sock_path = str(tmp_path / "health.sock")
        w = make_worker(worker_queue, health_socket=sock_path)
        worker_task = asyncio.create_task(w.run())
        await asyncio.sleep(0.15)

        import pathlib

        assert pathlib.Path(sock_path).exists()

        w._running = False
        await asyncio.sleep(0.2)
        worker_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await worker_task

        # Give a moment for cleanup
        await asyncio.sleep(0.1)
        assert not pathlib.Path(sock_path).exists()

    async def test_health_404_on_other_path(self, worker_queue):
        """Non-/health path returns 404."""
        port = 19101
        w = make_worker(worker_queue, health_port=port)
        worker_task = asyncio.create_task(w.run())
        await asyncio.sleep(0.15)

        try:
            status_code, data = await _http_get("127.0.0.1", port, "/other")
            assert status_code == 404
            assert data["error"] == "not found"
        finally:
            w._running = False
            await asyncio.sleep(0.1)
            worker_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await worker_task

    async def test_health_draining_status(self, worker_queue):
        """After _running = False, health response status becomes 'draining'.

        Runs the health server loop directly (not via worker.run()) so
        _running can be toggled without triggering the full shutdown sequence.
        """
        port = 19102
        w = make_worker(worker_queue, health_port=port)
        w._running = True  # Simulate started state
        health_task = asyncio.create_task(w._health_server_loop())
        await asyncio.sleep(0.15)

        try:
            status_code, data = await _http_get("127.0.0.1", port, "/health")
            assert data["status"] == "healthy"

            w._running = False

            status_code, data = await _http_get("127.0.0.1", port, "/health")
            assert data["status"] == "draining"
        finally:
            health_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await health_task

    async def test_health_response_active_jobs(self, worker_queue):
        """active_jobs count reflects running work."""
        port = 19103
        wrapped = task(worker_queue)(slow_task)
        job = await wrapped.enqueue(duration=2.0)

        w = make_worker(worker_queue, health_port=port)
        worker_task = asyncio.create_task(w.run())
        await asyncio.sleep(0.15)

        try:
            # Wait for job to be claimed
            for _ in range(50):
                await asyncio.sleep(0.02)
                if w._active_jobs:
                    break
            assert len(w._active_jobs) == 1

            status_code, data = await _http_get("127.0.0.1", port, "/health")
            assert status_code == 200
            assert data["active_jobs"] == 1
        finally:
            w._running = False
            await asyncio.sleep(0.1)
            worker_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await worker_task


# ---------------------------------------------------------------------------
# TestTimeout
# ---------------------------------------------------------------------------


class TestTimeout:
    """Job timeout enforcement via asyncio.wait_for."""

    async def test_timeout_triggers_failure(self, worker_queue):
        """Task exceeding timeout is failed with TIMEOUT failure kind."""
        wrapped = task(worker_queue, timeout=1)(timeout_slow_task)
        job = await wrapped.enqueue()

        w = make_worker(worker_queue)
        worker_task = asyncio.create_task(w.run())
        try:
            for _ in range(60):
                await asyncio.sleep(0.1)
                await job.refresh()
                if job.status != JobStatus.RUNNING.value:
                    break
        finally:
            w._running = False
            worker_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await worker_task

        await job.refresh()
        assert job.status == JobStatus.FAILED.value
        assert job.last_failure_kind == FailureKind.TIMEOUT.value
        assert "timed out" in job.last_error

    async def test_timeout_is_retryable(self, worker_queue):
        """Timed-out job is retried when max_retries allows it."""
        wrapped = task(worker_queue, timeout=1, max_retries=2)(timeout_slow_task)
        job = await wrapped.enqueue()

        w = make_worker(worker_queue)
        worker_task = asyncio.create_task(w.run())
        try:
            for _ in range(60):
                await asyncio.sleep(0.1)
                await job.refresh()
                if job.status != JobStatus.RUNNING.value:
                    break
        finally:
            w._running = False
            worker_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await worker_task

        await job.refresh()
        # Should be pending (requeued for retry), not permanently failed
        assert job.status == JobStatus.PENDING.value
        assert job.retry_count == 1
        assert job.last_failure_kind == FailureKind.TIMEOUT.value

    async def test_no_timeout_no_limit(self, worker_queue):
        """Task without timeout runs without time limit (existing behavior)."""
        wrapped = task(worker_queue)(timeout_fast_task)
        job = await wrapped.enqueue()

        w = make_worker(worker_queue)
        await run_worker_until_done(w, job)

        await job.refresh()
        assert job.status == JobStatus.COMPLETED.value
        assert job.timeout is None

    async def test_no_timeout_slow_task_completes(self, worker_queue):
        """Slow task without timeout completes normally (no accidental global timeout)."""
        wrapped = task(worker_queue)(slow_task)
        job = await wrapped.enqueue(0.5)

        w = make_worker(worker_queue)
        await run_worker_until_done(w, job)

        await job.refresh()
        assert job.status == JobStatus.COMPLETED.value
        assert job.result == "done"

    async def test_per_job_timeout_override(self, worker_queue):
        """Per-job _timeout overrides task default."""
        wrapped = task(worker_queue, timeout=60)(timeout_slow_task)
        # Override with short timeout
        job = await wrapped.enqueue(_timeout=1)

        assert job.timeout == 1

        w = make_worker(worker_queue)
        worker_task = asyncio.create_task(w.run())
        try:
            for _ in range(60):
                await asyncio.sleep(0.1)
                await job.refresh()
                if job.status != JobStatus.RUNNING.value:
                    break
        finally:
            w._running = False
            worker_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await worker_task

        await job.refresh()
        assert job.status == JobStatus.FAILED.value
        assert job.last_failure_kind == FailureKind.TIMEOUT.value
        assert "timed out after 1s" in job.last_error

    async def test_fast_task_within_timeout(self, worker_queue):
        """Task completing before timeout succeeds normally."""
        wrapped = task(worker_queue, timeout=5)(timeout_fast_task)
        job = await wrapped.enqueue()

        w = make_worker(worker_queue)
        await run_worker_until_done(w, job)

        await job.refresh()
        assert job.status == JobStatus.COMPLETED.value
        assert job.result == "fast"

    async def test_timeout_attempt_recorded(self, worker_queue):
        """Timed-out job creates a failed attempt with error details."""
        wrapped = task(worker_queue, timeout=1)(timeout_slow_task)
        job = await wrapped.enqueue()

        w = make_worker(worker_queue)
        worker_task = asyncio.create_task(w.run())
        try:
            for _ in range(60):
                await asyncio.sleep(0.1)
                await job.refresh()
                if job.status != JobStatus.RUNNING.value:
                    break
        finally:
            w._running = False
            worker_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await worker_task

        await job.refresh()
        attempts = await JobAttempt.query().filter(F("job_ulid") == job.ulid).all()
        assert len(attempts) == 1
        assert attempts[0].status == AttemptStatus.FAILED.value
        assert "timed out" in attempts[0].error

    async def test_sync_task_timeout(self, worker_queue):
        """Sync task (via to_thread) is also subject to timeout."""
        wrapped = task(worker_queue, sync=True, timeout=1)(timeout_sync_task)
        job = await wrapped.enqueue()

        w = make_worker(worker_queue)
        worker_task = asyncio.create_task(w.run())
        try:
            for _ in range(60):
                await asyncio.sleep(0.1)
                await job.refresh()
                if job.status != JobStatus.RUNNING.value:
                    break
        finally:
            w._running = False
            worker_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await worker_task

        await job.refresh()
        assert job.status == JobStatus.FAILED.value
        assert job.last_failure_kind == FailureKind.TIMEOUT.value
        assert "timed out" in job.last_error
        attempts = await JobAttempt.query().filter(F("job_ulid") == job.ulid).all()
        assert len(attempts) == 1
        assert attempts[0].status == AttemptStatus.FAILED.value
        assert attempts[0].failure_kind == FailureKind.TIMEOUT.value
        assert "timed out" in attempts[0].error


class TestShutdownBounds:
    """Renewal lasts through the drain, and the drain ends in bounded time."""

    @pytest.fixture(autouse=True)
    def _reset(self):
        global _held_started, _held_runs, _drain_release, _lease_at_finish
        _held_started = asyncio.Event()
        _held_runs = 0
        _drain_release = asyncio.Event()
        _lease_at_finish = []
        yield

    async def _row(self, job):
        return await Job.query().filter(F("ulid") == job.ulid).first()

    async def test_lease_is_renewed_through_a_long_drain(self, worker_db):
        """A drain longer than the job's lease: the lease stays ahead, one completion."""
        q = Queue(worker_db, default_lease_duration=2, default_max_retries=3)
        await q.init_db()
        wrapped = task(q)(draining_task)
        job = await wrapped.enqueue()

        w1 = make_worker(q, shutdown_timeout=10.0)
        w1_task = asyncio.create_task(w1.run())
        await asyncio.wait_for(_held_started.wait(), timeout=5.0)
        w2 = make_worker(q, lease_recovery_interval=0.1)
        w2_task = asyncio.create_task(w2.run())
        try:
            w1._running = False  # the drain starts here
            claim_lease = (await self._row(job)).lease_expires_at
            # Hold the drain until the claim lease and one lease length after it
            # are over (the clock has no event to wait on).
            while now_epoch() <= claim_lease + 2:  # noqa: ASYNC110 - waits on the wall clock, no event exists
                await asyncio.sleep(0.05)
            _drain_release.set()
            await asyncio.wait_for(w1_task, timeout=10.0)
        finally:
            _drain_release.set()
            w2._running = False
            await asyncio.wait_for(w2_task, timeout=10.0)
            if not w1_task.done():
                w1_task.cancel()

        assert len(_lease_at_finish) == 1
        lease, clock = _lease_at_finish[0]
        assert lease > clock
        assert _held_runs == 1
        row = await self._row(job)
        assert row.status == JobStatus.COMPLETED.value
        assert row.result == "first run"
        attempts = await JobAttempt.query().filter(F("job_ulid") == job.ulid).all()
        assert [(a.status, a.worker_id) for a in attempts] == [
            (AttemptStatus.COMPLETED.value, w1.worker_id),
        ]

    async def test_task_ignoring_the_cut_does_not_hold_shutdown(
        self, worker_db, monkeypatch, caplog
    ):
        """A task that swallows the cancel and keeps waiting is left after the grace."""
        import qler.worker as worker_module

        monkeypatch.setattr(worker_module, "SHUTDOWN_CUT_GRACE", 0.5, raising=False)
        q = Queue(worker_db, default_lease_duration=30, default_max_retries=3)
        await q.init_db()
        wrapped = task(q)(stubborn_task)
        job = await wrapped.enqueue()

        w = make_worker(q, shutdown_timeout=0.2)
        worker_task = asyncio.create_task(w.run())
        await asyncio.wait_for(_held_started.wait(), timeout=5.0)
        stuck = set(w._active_tasks)
        try:
            with caplog.at_level("WARNING", logger="qler.worker"):
                started = asyncio.get_running_loop().time()
                w._running = False
                await asyncio.wait_for(asyncio.shield(worker_task), timeout=5.0)
                elapsed = asyncio.get_running_loop().time() - started
            row = await self._row(job)
        finally:
            for t in stuck:
                t.cancel()
            await asyncio.gather(*stuck, return_exceptions=True)

        # timeout 0.2 + grace 0.5, with room for a busy machine
        assert elapsed < 0.2 + 0.5 + 1.5
        assert row.status == JobStatus.RUNNING.value
        assert row.worker_id == w.worker_id
        stuck_logs = [
            r.getMessage() for r in caplog.records if "job(s) keep their lease" in r.getMessage()
        ]
        assert len(stuck_logs) == 1
        assert job.ulid in stuck_logs[0]


class TestFinishFailureIsLogged:
    """A finish write that raises in the worker is logged; the worker carries on."""

    @pytest.fixture
    def tasks(self, worker_queue):
        return (
            task(worker_queue)(succeeding_task),
            task(worker_queue)(failing_task),
        )

    async def _run(
        self, worker_queue, monkeypatch, caplog, which, first_task, second_task, first_args=()
    ):
        """Make the first finish of kind `which` raise once; return (first, second, records)."""
        first_finish_raised = asyncio.Event()
        second_completed = asyncio.Event()
        real_complete = Queue.complete_job
        real_fail = Queue.fail_job
        calls = {"n": 0}

        async def complete_job(self, job, *a, **kw):
            if which == "complete":
                calls["n"] += 1
                if calls["n"] == 1:
                    first_finish_raised.set()
                    raise sqlite3.OperationalError("database is locked")
            await real_complete(self, job, *a, **kw)
            if job.ulid == second.ulid:
                second_completed.set()

        async def fail_job(self, job, *a, **kw):
            if which == "fail":
                calls["n"] += 1
                if calls["n"] == 1:
                    first_finish_raised.set()
                    raise sqlite3.OperationalError("database is locked")
            await real_fail(self, job, *a, **kw)

        monkeypatch.setattr(Queue, "complete_job", complete_job)
        monkeypatch.setattr(Queue, "fail_job", fail_job)

        first = await first_task.enqueue(*first_args)
        second = None

        w = make_worker(worker_queue)
        with caplog.at_level(logging.ERROR, logger="qler.worker"):
            worker_task = asyncio.create_task(w.run())
            try:
                await asyncio.wait_for(first_finish_raised.wait(), timeout=5.0)
                second = await second_task.enqueue(1)
                await asyncio.wait_for(second_completed.wait(), timeout=5.0)
            finally:
                w._running = False
                await asyncio.wait_for(worker_task, timeout=10.0)
        return w, first, second

    async def test_complete_that_raises_is_logged_and_worker_goes_on(
        self, worker_queue, monkeypatch, caplog, tasks
    ):
        succeeding, _ = tasks
        w, first, second = await self._run(
            worker_queue, monkeypatch, caplog, "complete", succeeding, succeeding, (1,)
        )

        errors = [
            r for r in caplog.records if r.name == "qler.worker" and r.levelno == logging.ERROR
        ]
        assert len(errors) == 1
        message = errors[0].getMessage()
        assert first.ulid in message
        assert succeeding.task_path in message
        assert "complete" in message
        assert "lease recovery" in message
        assert errors[0].exc_info[0] is sqlite3.OperationalError

        stored = await Job.query().filter(F("ulid") == first.ulid).first()
        assert stored.status == JobStatus.RUNNING.value
        assert stored.worker_id == w.worker_id
        assert stored.lease_expires_at is not None
        other = await Job.query().filter(F("ulid") == second.ulid).first()
        assert other.status == JobStatus.COMPLETED.value

    async def test_fail_that_raises_is_logged_and_worker_goes_on(
        self, worker_queue, monkeypatch, caplog, tasks
    ):
        succeeding, failing = tasks
        w, first, second = await self._run(
            worker_queue, monkeypatch, caplog, "fail", failing, succeeding
        )

        errors = [
            r
            for r in caplog.records
            if r.name == "qler.worker"
            and r.levelno == logging.ERROR
            and "lease recovery" in r.getMessage()
        ]
        assert len(errors) == 1
        message = errors[0].getMessage()
        assert first.ulid in message
        assert failing.task_path in message
        assert "fail" in message
        assert errors[0].exc_info[0] is sqlite3.OperationalError

        stored = await Job.query().filter(F("ulid") == first.ulid).first()
        assert stored.status == JobStatus.RUNNING.value
        assert stored.worker_id == w.worker_id
        assert stored.lease_expires_at is not None
        other = await Job.query().filter(F("ulid") == second.ulid).first()
        assert other.status == JobStatus.COMPLETED.value


async def test_cancel_check_reads_its_own_queue_after_another_was_initialised_last(
    tmp_path,
):
    """The worker's cancel check goes through the queue it holds."""
    qa = Queue(str(tmp_path / "a.db"))
    await qa.init_db()
    await qa.enqueue("m.f")
    job = await qa.claim_job("w1", ["default"])
    assert await job.request_cancel() is True
    qb = Queue(str(tmp_path / "b.db"))
    await qb.init_db()  # binds the class-wide handle to B last
    try:
        w = make_worker(qa)
        assert await w._cancel_was_requested(job) is True
    finally:
        await qa.close()
        await qb.close()
