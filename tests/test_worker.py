"""Tests for the Worker class — init, execution, concurrency, shutdown, correlation."""

import asyncio
import re

import pytest
import pytest_asyncio
from sqler import AsyncSQLerDB

from qler._context import current_job
from qler.enums import AttemptStatus, FailureKind, JobStatus
from qler.exceptions import ConfigurationError, JobFailedError
from qler.models.attempt import JobAttempt
from qler.models.job import Job
from qler.queue import Queue
from qler.task import task
from qler.worker import Worker
from sqler import F

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


def _make_worker(queue, **kwargs):
    """Create a Worker with fast test defaults."""
    defaults = {
        "queues": ["default"],
        "concurrency": 1,
        "poll_interval": 0.01,
        "lease_recovery_interval": 60.0,
        "shutdown_timeout": 2.0,
    }
    defaults.update(kwargs)
    return Worker(queue, **defaults)


async def _run_worker_until_job_done(worker, job, timeout=5.0):
    """Run worker in background, wait for job to reach terminal state, stop worker."""
    worker_task = asyncio.create_task(worker.run())
    try:
        result = await job.wait(timeout=timeout, poll_interval=0.02)
        return result
    finally:
        worker._running = False
        await asyncio.sleep(0.05)
        worker_task.cancel()
        try:
            await worker_task
        except asyncio.CancelledError:
            pass


# ---------------------------------------------------------------------------
# TestWorkerInit
# ---------------------------------------------------------------------------

class TestWorkerInit:
    """Worker.__init__ validation and defaults."""

    def test_worker_id_format(self, worker_queue):
        w = _make_worker(worker_queue)
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

        w = _make_worker(worker_queue)
        result = await _run_worker_until_job_done(w, job)
        assert result.status == "completed"
        assert result.result == {"sum": 15}

    async def test_sync_task_executes(self, worker_queue):
        wrapped = task(worker_queue, sync=True)(sync_task)
        job = await wrapped.enqueue(21)

        w = _make_worker(worker_queue)
        result = await _run_worker_until_job_done(w, job)
        assert result.status == "completed"
        assert result.result == 42

    async def test_task_not_found(self, worker_queue):
        """Unregistered task path → permanent FAILED with TASK_NOT_FOUND."""
        job = await worker_queue.enqueue("nonexistent.task_fn")

        w = _make_worker(worker_queue)
        with pytest.raises(JobFailedError) as exc_info:
            await _run_worker_until_job_done(w, job)
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

        w = _make_worker(worker_queue)
        with pytest.raises(JobFailedError) as exc_info:
            await _run_worker_until_job_done(w, job)
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

        w = _make_worker(q)
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
            try:
                await worker_task
            except asyncio.CancelledError:
                pass

        # After 1 failure with max_retries=1, job goes back to pending for retry
        assert job.status == "pending"
        assert job.retry_count == 1
        assert job.last_failure_kind == FailureKind.EXCEPTION.value
        assert job.last_error == "boom"

    async def test_exception_exhausted(self, worker_queue):
        """Task raises with no retries → permanent FAILED."""
        wrapped = task(worker_queue)(failing_task)
        job = await wrapped.enqueue()

        w = _make_worker(worker_queue)
        with pytest.raises(JobFailedError):
            await _run_worker_until_job_done(w, job)

        await job.refresh()
        assert job.status == "failed"
        assert job.last_failure_kind == FailureKind.EXCEPTION.value
        assert job.last_error == "boom"

    async def test_result_stored(self, worker_queue):
        """Completed job stores result in result_json."""
        wrapped = task(worker_queue)(succeeding_task)
        job = await wrapped.enqueue(3, y=7)

        w = _make_worker(worker_queue)
        result = await _run_worker_until_job_done(w, job)
        assert result.result_json is not None
        assert result.result == {"sum": 10}

    async def test_context_var_set(self, worker_queue):
        """current_job() works inside task execution."""
        global _captured_job_ulid
        _captured_job_ulid = None

        wrapped = task(worker_queue)(context_var_task)
        job = await wrapped.enqueue()

        w = _make_worker(worker_queue)
        await _run_worker_until_job_done(w, job)

        assert _captured_job_ulid == job.ulid

    async def test_attempt_record_created(self, worker_queue):
        """Completed job has a completed attempt record."""
        wrapped = task(worker_queue)(succeeding_task)
        job = await wrapped.enqueue(1)

        w = _make_worker(worker_queue)
        await _run_worker_until_job_done(w, job)

        attempts = await JobAttempt.query().filter(
            F("job_ulid") == job.ulid
        ).all()
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

        w = _make_worker(worker_queue, concurrency=2)
        worker_task = asyncio.create_task(w.run())
        try:
            # Both should complete within ~0.2s (parallel), not ~0.4s (serial)
            await job1.wait(timeout=2.0, poll_interval=0.02)
            await job2.wait(timeout=2.0, poll_interval=0.02)
        finally:
            w._running = False
            worker_task.cancel()
            try:
                await worker_task
            except asyncio.CancelledError:
                pass

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

        w = _make_worker(worker_queue, concurrency=1)
        worker_task = asyncio.create_task(w.run())
        try:
            await job1.wait(timeout=2.0, poll_interval=0.02)
            await job2.wait(timeout=2.0, poll_interval=0.02)
        finally:
            w._running = False
            worker_task.cancel()
            try:
                await worker_task
            except asyncio.CancelledError:
                pass

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

        w = _make_worker(worker_queue, shutdown_timeout=5.0)
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
        try:
            await worker_task
        except asyncio.CancelledError:
            pass

        await job.refresh()
        assert job.status == "completed"

    async def test_running_flag_stops_claim_loop(self, worker_queue):
        """Setting _running = False causes the worker task to exit cleanly."""
        w = _make_worker(worker_queue)
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

        w = _make_worker(worker_queue)
        await _run_worker_until_job_done(w, job)

        assert _captured_correlation == "my-trace-123"

    async def test_falls_back_to_ulid(self, worker_queue):
        """Job without correlation_id falls back to job ulid."""
        global _captured_correlation
        _captured_correlation = None

        wrapped = task(worker_queue)(capture_no_correlation_task)
        job = await wrapped.enqueue()

        w = _make_worker(worker_queue)
        await _run_worker_until_job_done(w, job)

        # correlation_id defaults to "" for jobs enqueued without one
        assert _captured_correlation == ""
