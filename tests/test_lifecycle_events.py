"""Tests for structured lifecycle events (M24)."""

from __future__ import annotations

import asyncio
import logging

import pytest
import pytest_asyncio
from sqler import AsyncSQLerDB, F

from qler._time import now_epoch
from qler.enums import FailureKind
from qler.lifecycle import emit, logger as lifecycle_logger
from qler.models.job import Job
from qler.queue import Queue
from qler.task import task
from qler.worker import Worker


# ---------------------------------------------------------------------------
# Module-level task functions (required by @task decorator)
# ---------------------------------------------------------------------------


async def enqueue_task():
    pass


async def batch_task(x: int):
    pass


async def claim_task():
    pass


async def complete_task():
    pass


async def fail_task():
    pass


async def retry_task():
    pass


async def retry_only_task():
    pass


async def exhaust_task():
    pass


async def lease_task():
    pass


async def executed_task():
    return 42


async def failing_exec_task():
    raise ValueError("kaboom")


async def corr_task():
    pass


async def no_metrics_task():
    pass


async def batch_idemp_task(x: int):
    pass


# ---------------------------------------------------------------------------
# Capturing handler fixture
# ---------------------------------------------------------------------------


class CapturingHandler(logging.Handler):
    """Collects log records for assertion."""

    def __init__(self) -> None:
        super().__init__()
        self.records: list[logging.LogRecord] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.records.append(record)

    def clear(self) -> None:
        self.records.clear()

    def by_event(self, event: str) -> list[logging.LogRecord]:
        return [r for r in self.records if getattr(r, "event", None) == event]


@pytest.fixture
def capture():
    handler = CapturingHandler()
    lifecycle_logger.addHandler(handler)
    lifecycle_logger.setLevel(logging.DEBUG)
    yield handler
    lifecycle_logger.removeHandler(handler)


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


# ---------------------------------------------------------------------------
# emit() helper tests
# ---------------------------------------------------------------------------


class TestEmitHelper:
    def test_emit_helper_fields(self, capture: CapturingHandler):
        emit("job.enqueued", job_id="abc", queue="default", task="my.task", correlation_id="corr-1")
        assert len(capture.records) == 1
        rec = capture.records[0]
        assert rec.event == "job.enqueued"  # type: ignore[attr-defined]
        assert rec.job_id == "abc"  # type: ignore[attr-defined]
        assert rec.queue == "default"  # type: ignore[attr-defined]
        assert rec.task == "my.task"  # type: ignore[attr-defined]
        assert rec.correlation_id == "corr-1"  # type: ignore[attr-defined]

    def test_emit_helper_no_correlation_id(self, capture: CapturingHandler):
        emit("job.completed", job_id="abc", queue="default", task="my.task")
        rec = capture.records[0]
        # correlation_id should not be in extra when empty string
        assert not hasattr(rec, "correlation_id")

    def test_emit_helper_extra_kwargs(self, capture: CapturingHandler):
        emit("job.claimed", job_id="abc", queue="q", task="t", worker_id="w1", attempt=3)
        rec = capture.records[0]
        assert rec.worker_id == "w1"  # type: ignore[attr-defined]
        assert rec.attempt == 3  # type: ignore[attr-defined]

    def test_emit_uses_info_level(self, capture: CapturingHandler):
        emit("job.enqueued", job_id="x", queue="q", task="t")
        assert capture.records[0].levelno == logging.INFO


# ---------------------------------------------------------------------------
# Queue lifecycle event tests
# ---------------------------------------------------------------------------

_MOD = "test_lifecycle_events"


class TestEnqueueEvents:
    @pytest.mark.asyncio
    async def test_enqueue_emits_event(self, queue: Queue, capture: CapturingHandler):
        t = task(queue)(enqueue_task)
        job = await t.enqueue()

        events = capture.by_event("job.enqueued")
        assert len(events) == 1
        rec = events[0]
        assert rec.job_id == job.ulid  # type: ignore[attr-defined]
        assert rec.queue == "default"  # type: ignore[attr-defined]
        assert rec.task == f"{_MOD}.enqueue_task"  # type: ignore[attr-defined]
        assert rec.priority == 0  # type: ignore[attr-defined]

    @pytest.mark.asyncio
    async def test_enqueue_many_emits_per_job(self, queue: Queue, capture: CapturingHandler):
        t = task(queue)(batch_task)

        jobs = await t.enqueue_many([(1,), (2,), (3,)])

        events = capture.by_event("job.enqueued")
        assert len(events) == 3
        for rec in events:
            assert rec.batch is True  # type: ignore[attr-defined]
            assert rec.queue == "default"  # type: ignore[attr-defined]
            assert rec.task == f"{_MOD}.batch_task"  # type: ignore[attr-defined]
        emitted_ids = {r.job_id for r in events}  # type: ignore[attr-defined]
        assert emitted_ids == {j.ulid for j in jobs}

    @pytest.mark.asyncio
    async def test_enqueue_many_idempotency_skips_event(self, queue: Queue, capture: CapturingHandler):
        """Idempotency-matched jobs in batch do not emit job.enqueued."""
        t = task(queue)(batch_idemp_task)

        # Pre-create a job with known idempotency key
        await t.enqueue(1, _idempotency_key="key-1")
        capture.clear()

        # Batch: key-1 already exists, key-2 is new
        jobs = await queue.enqueue_many([
            {"task_path": t.task_path, "args": (1,), "idempotency_key": "key-1"},
            {"task_path": t.task_path, "args": (2,), "idempotency_key": "key-2"},
        ])

        events = capture.by_event("job.enqueued")
        # Only key-2 should emit (key-1 is an idempotency match)
        assert len(events) == 1
        assert events[0].job_id == jobs[1].ulid  # type: ignore[attr-defined]


class TestClaimEvents:
    @pytest.mark.asyncio
    async def test_claim_emits_event(self, queue: Queue, capture: CapturingHandler):
        t = task(queue)(claim_task)
        job = await t.enqueue()
        capture.clear()

        claimed = await queue.claim_job("worker-1", ["default"])

        events = capture.by_event("job.claimed")
        assert len(events) == 1
        rec = events[0]
        assert rec.job_id == job.ulid  # type: ignore[attr-defined]
        assert rec.queue == "default"  # type: ignore[attr-defined]
        assert rec.task == f"{_MOD}.claim_task"  # type: ignore[attr-defined]
        assert rec.worker_id == "worker-1"  # type: ignore[attr-defined]
        assert rec.attempt == 1  # type: ignore[attr-defined]


class TestCompleteEvents:
    @pytest.mark.asyncio
    async def test_complete_emits_event(self, queue: Queue, capture: CapturingHandler):
        t = task(queue)(complete_task)
        job = await t.enqueue()
        claimed = await queue.claim_job("worker-1", ["default"])
        capture.clear()

        await queue.complete_job(claimed, "worker-1")

        events = capture.by_event("job.completed")
        assert len(events) == 1
        assert events[0].job_id == job.ulid  # type: ignore[attr-defined]
        assert events[0].queue == "default"  # type: ignore[attr-defined]
        assert events[0].task == f"{_MOD}.complete_task"  # type: ignore[attr-defined]


class TestFailEvents:
    @pytest.mark.asyncio
    async def test_fail_terminal_emits_failed(self, queue: Queue, capture: CapturingHandler):
        t = task(queue)(fail_task)
        await t.enqueue()
        claimed = await queue.claim_job("worker-1", ["default"])
        capture.clear()

        await queue.fail_job(claimed, "worker-1", RuntimeError("boom"), failure_kind=FailureKind.EXCEPTION)

        failed_events = capture.by_event("job.failed")
        assert len(failed_events) == 1
        rec = failed_events[0]
        assert rec.queue == "default"  # type: ignore[attr-defined]
        assert rec.task == f"{_MOD}.fail_task"  # type: ignore[attr-defined]
        assert rec.failure_kind == "exception"  # type: ignore[attr-defined]
        assert rec.error == "boom"  # type: ignore[attr-defined]
        assert len(capture.by_event("job.retried")) == 0  # terminal — no retry

    @pytest.mark.asyncio
    async def test_fail_retryable_emits_retried(self, queue: Queue, capture: CapturingHandler):
        t = task(queue, max_retries=3)(retry_task)
        await t.enqueue()
        claimed = await queue.claim_job("worker-1", ["default"])
        capture.clear()

        await queue.fail_job(claimed, "worker-1", RuntimeError("oops"), failure_kind=FailureKind.EXCEPTION)

        retried_events = capture.by_event("job.retried")
        assert len(retried_events) == 1
        rec = retried_events[0]
        assert rec.queue == "default"  # type: ignore[attr-defined]
        assert rec.task == f"{_MOD}.retry_task"  # type: ignore[attr-defined]
        assert rec.failure_kind == "exception"  # type: ignore[attr-defined]
        assert rec.retry_count == 1  # type: ignore[attr-defined]

    @pytest.mark.asyncio
    async def test_fail_retried_not_failed(self, queue: Queue, capture: CapturingHandler):
        """Retryable failure emits only 'job.retried', not 'job.failed'."""
        t = task(queue, max_retries=3)(retry_only_task)
        await t.enqueue()
        claimed = await queue.claim_job("worker-1", ["default"])
        capture.clear()

        await queue.fail_job(claimed, "worker-1", RuntimeError("oops"), failure_kind=FailureKind.EXCEPTION)

        assert len(capture.by_event("job.retried")) == 1
        assert len(capture.by_event("job.failed")) == 0

    @pytest.mark.asyncio
    async def test_fail_exhausted_retries_emits_failed(self, queue: Queue, capture: CapturingHandler):
        """Final failure after retry exhaustion emits 'job.failed', not 'job.retried'."""
        t = task(queue, max_retries=1, retry_delay=1)(exhaust_task)
        await t.enqueue()

        # First failure → retried
        claimed = await queue.claim_job("worker-1", ["default"])
        await queue.fail_job(claimed, "worker-1", RuntimeError("x"), failure_kind=FailureKind.EXCEPTION)

        # Reset ETA so we can reclaim
        await Job.query().filter(F("ulid") == claimed.ulid).update_one(eta=now_epoch())

        # Second failure → exhausted → failed
        claimed = await queue.claim_job("worker-1", ["default"])
        capture.clear()
        await queue.fail_job(claimed, "worker-1", RuntimeError("x"), failure_kind=FailureKind.EXCEPTION)

        assert len(capture.by_event("job.failed")) == 1
        assert len(capture.by_event("job.retried")) == 0


class TestLeaseRecoveryEvents:
    @pytest.mark.asyncio
    async def test_lease_recovery_emits_event(self, queue: Queue, capture: CapturingHandler):
        t = task(queue)(lease_task)
        job = await t.enqueue()

        claimed = await queue.claim_job("worker-1", ["default"])
        now = now_epoch()
        await Job.query().filter(F("ulid") == claimed.ulid).update_one(
            lease_expires_at=now - 100,
        )
        capture.clear()

        recovered = await queue.recover_expired_leases()

        assert recovered == 1
        events = capture.by_event("job.lease_recovered")
        assert len(events) == 1
        assert events[0].job_id == job.ulid  # type: ignore[attr-defined]
        assert events[0].queue == "default"  # type: ignore[attr-defined]
        assert events[0].task == f"{_MOD}.lease_task"  # type: ignore[attr-defined]


# ---------------------------------------------------------------------------
# Worker lifecycle event tests
# ---------------------------------------------------------------------------

_WORKER_TIMEOUT = 5.0


class TestWorkerEvents:
    @pytest.mark.asyncio
    async def test_worker_emits_executed(self, queue: Queue, capture: CapturingHandler):
        t = task(queue)(executed_task)
        job = await t.enqueue()

        worker = Worker(queue, queues=["default"], concurrency=1, poll_interval=0.05)

        async def stop_after_one():
            deadline = asyncio.get_event_loop().time() + _WORKER_TIMEOUT
            while not capture.by_event("job.executed"):
                if asyncio.get_event_loop().time() > deadline:
                    pytest.fail("job.executed event never fired within timeout")
                await asyncio.sleep(0.02)
            worker._running = False

        await asyncio.gather(worker.run(), stop_after_one())

        events = capture.by_event("job.executed")
        assert len(events) == 1
        rec = events[0]
        assert rec.job_id == job.ulid  # type: ignore[attr-defined]
        assert rec.task == f"{_MOD}.executed_task"  # type: ignore[attr-defined]
        assert 0 < rec.duration < 1.0  # type: ignore[attr-defined]

    @pytest.mark.asyncio
    async def test_worker_failed_emits_executed(self, queue: Queue, capture: CapturingHandler):
        """job.executed fires even when the task fails."""
        t = task(queue)(failing_exec_task)
        job = await t.enqueue()

        worker = Worker(queue, queues=["default"], concurrency=1, poll_interval=0.05)

        async def stop_after_one():
            deadline = asyncio.get_event_loop().time() + _WORKER_TIMEOUT
            while not capture.by_event("job.executed"):
                if asyncio.get_event_loop().time() > deadline:
                    pytest.fail("job.executed event never fired within timeout")
                await asyncio.sleep(0.02)
            worker._running = False

        await asyncio.gather(worker.run(), stop_after_one())

        executed = capture.by_event("job.executed")
        assert len(executed) == 1
        assert executed[0].job_id == job.ulid  # type: ignore[attr-defined]
        assert 0 < executed[0].duration < 1.0  # type: ignore[attr-defined]

        # Also has a job.failed event
        assert len(capture.by_event("job.failed")) == 1


# ---------------------------------------------------------------------------
# Cross-cutting concerns
# ---------------------------------------------------------------------------


class TestCorrelationPropagation:
    @pytest.mark.asyncio
    async def test_correlation_id_propagated(self, queue: Queue, capture: CapturingHandler):
        t = task(queue)(corr_task)
        await t.enqueue(_correlation_id="my-corr-id")
        claimed = await queue.claim_job("worker-1", ["default"])
        await queue.complete_job(claimed, "worker-1")

        corr_records = [r for r in capture.records if hasattr(r, "correlation_id")]
        assert len(corr_records) == 3  # enqueued + claimed + completed
        for rec in corr_records:
            assert rec.correlation_id == "my-corr-id"  # type: ignore[attr-defined]


class TestFiresWithoutMetrics:
    @pytest.mark.asyncio
    async def test_fires_without_metrics(self, db, capture: CapturingHandler):
        """Lifecycle events fire even when metrics=False (default)."""
        q = Queue(db)
        await q.init_db()
        assert q._metrics is None

        t = task(q)(no_metrics_task)
        await t.enqueue()

        assert len(capture.by_event("job.enqueued")) == 1
