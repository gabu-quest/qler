"""Tests for immediate mode — Queue(immediate=True) executes jobs inline."""

import pytest
import pytest_asyncio
from qler.enums import AttemptStatus, FailureKind, JobStatus
from qler.exceptions import (
    PayloadNotSerializableError,
    PayloadTooLargeError,
    TaskNotRegisteredError,
)
from qler.models.attempt import JobAttempt
from qler.models.job import Job
from qler.queue import Queue
from qler.task import task
from sqler import AsyncSQLerDB, F

# ---------------------------------------------------------------------------
# Module-level task functions (required: @task rejects nested functions)
# ---------------------------------------------------------------------------

_call_count = 0


async def add_task(a, b):
    return a + b


async def make_dict_task(key, value):
    return {key: value}


async def make_list_task(*items):
    return list(items)


async def noop_task():
    pass


async def boom_task():
    raise ValueError("kaboom")


async def flaky_task():
    raise RuntimeError("transient")


async def bad_result_task():
    return object()  # Not JSON-serializable


def multiply_task(a, b):
    return a * b


async def identity_task(x):
    return x


async def greet_task(name):
    return f"hello {name}"


async def fail_oops_task():
    raise RuntimeError("oops")


import asyncio


async def imm_timeout_slow_task():
    await asyncio.sleep(10)
    return "unreachable"


async def imm_timeout_fast_task():
    await asyncio.sleep(0.01)
    return "quick"


async def traced_task():
    return "done"


async def counted_task():
    global _call_count
    _call_count += 1
    return _call_count


async def delayed_task():
    return "done"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def imm_db():
    """In-memory sqler database for immediate mode tests."""
    _db = AsyncSQLerDB.in_memory(shared=False)
    await _db.connect()
    yield _db
    await _db.close()


@pytest_asyncio.fixture
async def imm_queue(imm_db):
    """Initialized Queue with immediate=True and tasks registered."""
    q = Queue(imm_db, immediate=True)
    await q.init_db()

    # Register module-level tasks on this queue
    task(q)(add_task)
    task(q)(make_dict_task)
    task(q)(make_list_task)
    task(q)(noop_task)
    task(q)(boom_task)
    task(q, max_retries=2)(flaky_task)
    task(q)(bad_result_task)
    task(q, sync=True)(multiply_task)
    task(q)(identity_task)
    task(q)(greet_task)
    task(q)(fail_oops_task)
    task(q)(traced_task)
    task(q)(counted_task)
    task(q)(delayed_task)

    yield q


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestImmediateHappyPath:
    """Verify immediate mode executes and returns completed jobs."""

    async def test_happy_path(self, imm_queue):
        wrapper = imm_queue._tasks[f"{__name__}.add_task"]
        job = await wrapper.enqueue(2, 3)

        assert job.status == JobStatus.COMPLETED.value
        assert job.result == 5
        assert job.attempts == 1
        assert job.finished_at >= job.created_at

    async def test_result_property_dict(self, imm_queue):
        wrapper = imm_queue._tasks[f"{__name__}.make_dict_task"]
        job = await wrapper.enqueue("foo", 42)

        assert job.status == JobStatus.COMPLETED.value
        assert job.result == {"foo": 42}

    async def test_result_property_list(self, imm_queue):
        wrapper = imm_queue._tasks[f"{__name__}.make_list_task"]
        job = await wrapper.enqueue(1, 2, 3)

        assert job.status == JobStatus.COMPLETED.value
        assert job.result == [1, 2, 3]

    async def test_result_none(self, imm_queue):
        wrapper = imm_queue._tasks[f"{__name__}.noop_task"]
        job = await wrapper.enqueue()

        assert job.status == JobStatus.COMPLETED.value
        assert job.result is None

    async def test_immediate_ignores_delay(self, imm_queue):
        wrapper = imm_queue._tasks[f"{__name__}.delayed_task"]
        job = await wrapper.enqueue(_delay=3600)

        # Immediate mode executes regardless of delay
        assert job.status == JobStatus.COMPLETED.value
        assert job.result == "done"
        assert job.eta >= job.created_at


class TestImmediateFailure:
    """Verify immediate mode handles task failures correctly."""

    async def test_failure_no_retries(self, imm_queue):
        wrapper = imm_queue._tasks[f"{__name__}.boom_task"]
        job = await wrapper.enqueue()

        assert job.status == JobStatus.FAILED.value
        assert "kaboom" in job.last_error
        assert job.last_failure_kind == FailureKind.EXCEPTION.value
        assert job.finished_at >= job.created_at

    async def test_failure_with_retries(self, imm_queue):
        wrapper = imm_queue._tasks[f"{__name__}.flaky_task"]
        job = await wrapper.enqueue()

        # With retries available, fail_job puts it back to PENDING
        assert job.status == JobStatus.PENDING.value
        assert job.retry_count == 1
        assert "transient" in job.last_error

    async def test_non_serializable_result(self, imm_queue):
        wrapper = imm_queue._tasks[f"{__name__}.bad_result_task"]
        job = await wrapper.enqueue()

        # complete_job detects non-serializable result and calls fail_job
        assert job.status == JobStatus.FAILED.value
        assert job.last_failure_kind == FailureKind.EXCEPTION.value
        assert "is not JSON serializable" in job.last_error
        assert job.finished_at >= job.created_at


class TestImmediateSyncTask:
    """Verify sync=True tasks execute via to_thread in immediate mode."""

    async def test_sync_task(self, imm_queue):
        wrapper = imm_queue._tasks[f"{__name__}.multiply_task"]
        job = await wrapper.enqueue(6, 7)

        assert job.status == JobStatus.COMPLETED.value
        assert job.result == 42


class TestImmediateTaskResolution:
    """Verify immediate mode raises and cleans up when task is not registered."""

    async def test_task_not_registered(self, imm_queue):
        with pytest.raises(TaskNotRegisteredError, match="not registered"):
            await imm_queue.enqueue("some.nonexistent.task", args=(1, 2))

        # Job should be FAILED, not left as a ghost RUNNING record
        jobs = await Job.query().filter(
            F("task") == "some.nonexistent.task"
        ).all()
        assert len(jobs) == 1
        assert jobs[0].status == JobStatus.FAILED.value
        assert jobs[0].last_failure_kind == FailureKind.TASK_NOT_FOUND.value


class TestImmediatePayloadValidation:
    """Verify payload validation still works in immediate mode."""

    async def test_payload_not_serializable(self, imm_queue):
        wrapper = imm_queue._tasks[f"{__name__}.identity_task"]

        with pytest.raises(PayloadNotSerializableError, match="not JSON-serializable"):
            await wrapper.enqueue(object())

    async def test_payload_too_large(self, imm_db):
        q = Queue(imm_db, immediate=True, max_payload_size=100)
        await q.init_db()
        task(q)(identity_task)
        wrapper = q._tasks[f"{__name__}.identity_task"]

        big = "x" * 200
        with pytest.raises(PayloadTooLargeError, match="exceeds max"):
            await wrapper.enqueue(big)


class TestImmediateAttemptRecord:
    """Verify immediate mode creates proper attempt records."""

    async def test_attempt_record_created(self, imm_queue):
        wrapper = imm_queue._tasks[f"{__name__}.greet_task"]
        job = await wrapper.enqueue("world")

        attempts = await JobAttempt.query().filter(
            F("job_ulid") == job.ulid
        ).all()

        assert len(attempts) == 1
        attempt = attempts[0]
        assert attempt.status == AttemptStatus.COMPLETED.value
        assert attempt.worker_id == "__immediate__"
        assert attempt.attempt_number == 1
        assert attempt.started_at >= job.created_at
        assert attempt.finished_at >= attempt.started_at

    async def test_failed_attempt_record(self, imm_queue):
        wrapper = imm_queue._tasks[f"{__name__}.fail_oops_task"]
        job = await wrapper.enqueue()

        attempts = await JobAttempt.query().filter(
            F("job_ulid") == job.ulid
        ).all()

        assert len(attempts) == 1
        attempt = attempts[0]
        assert attempt.status == AttemptStatus.FAILED.value
        assert attempt.failure_kind == FailureKind.EXCEPTION.value
        assert "oops" in attempt.error
        assert attempt.traceback is not None
        assert "RuntimeError" in attempt.traceback
        assert attempt.finished_at >= attempt.started_at


class TestImmediateCorrelation:
    """Verify correlation IDs are preserved through immediate execution."""

    async def test_correlation_id_preserved(self, imm_queue):
        wrapper = imm_queue._tasks[f"{__name__}.traced_task"]
        job = await wrapper.enqueue(_correlation_id="req-abc-123")

        assert job.correlation_id == "req-abc-123"
        assert job.status == JobStatus.COMPLETED.value

        attempts = await JobAttempt.query().filter(
            F("job_ulid") == job.ulid
        ).all()
        assert len(attempts) == 1
        assert attempts[0].status == AttemptStatus.COMPLETED.value
        assert attempts[0].worker_id == "__immediate__"


class TestImmediateIdempotency:
    """Verify idempotency keys work correctly with immediate mode."""

    async def test_idempotency_in_immediate(self, imm_queue):
        global _call_count
        _call_count = 0

        wrapper = imm_queue._tasks[f"{__name__}.counted_task"]
        job1 = await wrapper.enqueue(_idempotency_key="once")
        job2 = await wrapper.enqueue(_idempotency_key="once")

        # Second call returns the existing job (not re-executed)
        assert job1.ulid == job2.ulid
        assert _call_count == 1
        assert job1.status == JobStatus.COMPLETED.value


class TestImmediateRetryExhaustion:
    """Verify retry boundary: retry_count == max_retries → FAILED."""

    async def test_retry_exhaustion(self, imm_db):
        """Enqueue max_retries=1 always-fail task. First exec → PENDING (retry).
        Second exec → FAILED (exhausted). Boundary: retry_count == max_retries."""
        q = Queue(imm_db, immediate=True)
        await q.init_db()
        task(q, max_retries=1)(boom_task)
        wrapper = q._tasks[f"{__name__}.boom_task"]

        # First execution: fails but retry available → PENDING
        job = await wrapper.enqueue()
        assert job.status == JobStatus.PENDING.value
        assert job.retry_count == 1
        assert job.max_retries == 1
        assert "kaboom" in job.last_error

        # Second execution: retries exhausted → FAILED
        job = await q._execute_immediate(job)
        assert job.status == JobStatus.FAILED.value
        assert job.retry_count == 1
        assert job.last_failure_kind == FailureKind.EXCEPTION.value
        assert job.finished_at >= job.created_at


class TestImmediateIdempotencyAfterCancellation:
    """Verify idempotency key is reusable after cancellation."""

    async def test_idempotency_after_cancellation(self, imm_queue):
        global _call_count
        _call_count = 0

        wrapper = imm_queue._tasks[f"{__name__}.counted_task"]

        # First enqueue → completes
        job1 = await wrapper.enqueue(_idempotency_key="cancel-test")
        assert job1.status == JobStatus.COMPLETED.value
        assert _call_count == 1

        # Idempotency dedup: same key returns existing (COMPLETED)
        job_dup = await wrapper.enqueue(_idempotency_key="cancel-test")
        assert job_dup.ulid == job1.ulid
        assert _call_count == 1

        # Now: enqueue a new job, cancel it while PENDING, re-enqueue same key
        q2 = Queue(imm_queue.db, immediate=False)
        await q2.init_db()
        task(q2)(counted_task)
        wrapper2 = q2._tasks[f"{__name__}.counted_task"]

        job_pending = await wrapper2.enqueue(_idempotency_key="cancel-reuse")
        assert job_pending.status == JobStatus.PENDING.value
        assert _call_count == 1  # not executed (non-immediate)

        # Cancel the pending job
        ok = await job_pending.cancel()
        assert ok is True

        # Re-enqueue same idempotency key → should create new job
        # (cancelled jobs are excluded from idempotency check)
        job_new = await wrapper2.enqueue(_idempotency_key="cancel-reuse")
        assert job_new.ulid != job_pending.ulid
        assert job_new.status == JobStatus.PENDING.value


@pytest_asyncio.fixture
async def imm_timeout_queue():
    db = AsyncSQLerDB.in_memory(shared=False)
    await db.connect()
    q = Queue(db, immediate=True)
    await q.init_db()
    yield q
    await db.close()


class TestImmediateTimeout:
    """Timeout enforcement in immediate mode."""

    async def test_immediate_timeout_triggers_failure(self, imm_timeout_queue):
        """Task exceeding timeout in immediate mode fails with TIMEOUT."""
        wrapped = task(imm_timeout_queue, timeout=1)(imm_timeout_slow_task)
        job = await wrapped.enqueue()
        assert job.status == JobStatus.FAILED.value
        assert job.last_failure_kind == FailureKind.TIMEOUT.value
        assert "timed out" in job.last_error

    async def test_immediate_fast_within_timeout(self, imm_timeout_queue):
        """Task completing before timeout succeeds in immediate mode."""
        wrapped = task(imm_timeout_queue, timeout=5)(imm_timeout_fast_task)
        job = await wrapped.enqueue()
        assert job.status == JobStatus.COMPLETED.value
        assert job.result == "quick"

    async def test_immediate_timeout_attempt_recorded(self, imm_timeout_queue):
        """Timed-out immediate job creates a failed attempt."""
        wrapped = task(imm_timeout_queue, timeout=1)(imm_timeout_slow_task)
        job = await wrapped.enqueue()

        attempts = await JobAttempt.query().filter(
            F("job_ulid") == job.ulid
        ).all()
        assert len(attempts) == 1
        assert attempts[0].status == AttemptStatus.FAILED.value
        assert "timed out" in attempts[0].error
        assert attempts[0].failure_kind == FailureKind.TIMEOUT.value
