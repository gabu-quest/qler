"""Tests for batch claiming — Queue.claim_jobs() and Worker batch dispatch."""

import asyncio
import re

import pytest
import pytest_asyncio
from sqler import AsyncSQLerDB, F

from qler._time import now_epoch
from qler.enums import AttemptStatus, JobStatus
from qler.exceptions import ConfigurationError
from qler.models.attempt import JobAttempt
from qler.models.job import Job
from qler.queue import Queue
from qler.task import task
from qler.worker import Worker

_ULID_PATTERN = re.compile(r"^[0-9A-Z]{26}$")


# ---------------------------------------------------------------------------
# Module-level task functions
# ---------------------------------------------------------------------------

_batch_log: list[str] = []


async def batch_task(name):
    _batch_log.append(name)
    return name


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

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
# TestClaimJobs
# ---------------------------------------------------------------------------

class TestClaimJobs:
    """Verify Queue.claim_jobs batch claiming."""

    async def test_respects_n(self, queue):
        for i in range(5):
            await queue.enqueue(f"t{i}", queue_name="default")
        jobs = await queue.claim_jobs("w1", ["default"], n=3)
        assert len(jobs) == 3
        # 2 remaining pending
        pending = await Job.query().filter(
            F("status") == JobStatus.PENDING.value
        ).count()
        assert pending == 2

    async def test_empty_queue_returns_empty(self, queue):
        jobs = await queue.claim_jobs("w1", ["default"], n=5)
        assert jobs == []

    async def test_priority_ordering(self, queue):
        await queue.enqueue("low", priority=1)
        await queue.enqueue("high", priority=10)
        await queue.enqueue("medium", priority=5)

        jobs = await queue.claim_jobs("w1", ["default"], n=2)
        assert len(jobs) == 2
        tasks = [j.task for j in jobs]
        assert tasks[0] == "high"
        assert tasks[1] == "medium"

    async def test_creates_attempt_records(self, queue):
        await queue.enqueue("t1", queue_name="default")
        await queue.enqueue("t2", queue_name="default")
        jobs = await queue.claim_jobs("w1", ["default"], n=2)
        assert len(jobs) == 2

        for job in jobs:
            attempts = await JobAttempt.query().filter(
                F("job_ulid") == job.ulid
            ).all()
            assert len(attempts) == 1
            assert attempts[0].status == AttemptStatus.RUNNING.value
            assert attempts[0].worker_id == "w1"
            assert attempts[0].attempt_number == 1

    async def test_sets_last_attempt_id(self, queue):
        await queue.enqueue("t1", queue_name="default")
        jobs = await queue.claim_jobs("w1", ["default"], n=1)
        assert len(jobs) == 1
        # Refresh to get last_attempt_id from the per-job update
        await jobs[0].refresh()
        assert _ULID_PATTERN.match(jobs[0].last_attempt_id), (
            f"Expected ULID, got {jobs[0].last_attempt_id!r}"
        )

    async def test_partial_availability(self, queue):
        """n=5 but only 3 pending -> returns 3."""
        for i in range(3):
            await queue.enqueue(f"t{i}", queue_name="default")
        jobs = await queue.claim_jobs("w1", ["default"], n=5)
        assert len(jobs) == 3

    async def test_all_claimed_are_running(self, queue):
        for i in range(3):
            await queue.enqueue(f"t{i}", queue_name="default")
        jobs = await queue.claim_jobs("w1", ["default"], n=3)
        assert len(jobs) == 3
        for job in jobs:
            assert job.status == JobStatus.RUNNING.value
            assert job.worker_id == "w1"
            assert job.attempts == 1

    async def test_poison_pill_filtered(self, queue):
        """Jobs with unparseable payloads are excluded from results."""
        await queue.enqueue("valid_task", queue_name="default")

        from qler._time import generate_ulid
        bad_ulid = generate_ulid()
        bad_job = Job(
            ulid=bad_ulid,
            task="bad_task",
            queue_name="default",
            status=JobStatus.PENDING.value,
            payload_json="not valid json {{{",
            priority=100,  # higher priority so it gets claimed first
        )
        await bad_job.save()

        jobs = await queue.claim_jobs("w1", ["default"], n=2)
        assert len(jobs) == 1
        assert jobs[0].task == "valid_task"

        bad = await Job.query().filter(F("ulid") == bad_ulid).first()
        assert bad.status == JobStatus.FAILED.value

    async def test_rejects_empty_queues(self, queue):
        with pytest.raises(ConfigurationError, match="queues list must not be empty"):
            await queue.claim_jobs("w1", [], n=1)

    async def test_rejects_n_zero(self, queue):
        with pytest.raises(ConfigurationError, match="n must be >= 1"):
            await queue.claim_jobs("w1", ["default"], n=0)

    async def test_skips_future_eta(self, queue):
        await queue.enqueue("future", eta=now_epoch() + 3600)
        await queue.enqueue("now")
        jobs = await queue.claim_jobs("w1", ["default"], n=2)
        assert len(jobs) == 1
        assert jobs[0].task == "now"

    async def test_skips_jobs_with_pending_deps(self, queue):
        j1 = await queue.enqueue("parent")
        j2 = await queue.enqueue("child", depends_on=[j1.ulid])
        jobs = await queue.claim_jobs("w1", ["default"], n=2)
        assert len(jobs) == 1
        assert jobs[0].ulid == j1.ulid

    async def test_claims_across_multiple_queues(self, queue):
        """Jobs from different queues are claimed in a single batch."""
        await queue.enqueue("alpha_task", queue_name="alpha")
        await queue.enqueue("beta_task", queue_name="beta")
        jobs = await queue.claim_jobs("w1", ["alpha", "beta"], n=2)
        assert len(jobs) == 2
        queue_names = {j.queue_name for j in jobs}
        assert queue_names == {"alpha", "beta"}
        tasks = {j.task for j in jobs}
        assert tasks == {"alpha_task", "beta_task"}

    async def test_rate_limited_jobs_filtered(self, db):
        """Rate-limited jobs are requeued and excluded from results."""
        q = Queue(db, rate_limits={"default": "1/m"})
        await q.init_db()

        await q.enqueue("t1", queue_name="default")
        await q.enqueue("t2", queue_name="default")

        jobs = await q.claim_jobs("w1", ["default"], n=2)
        # First job passes rate limit, second gets rate-limited
        assert len(jobs) == 1
        assert jobs[0].task == "t1"

        # The second job should be back to pending (requeued)
        pending = await Job.query().filter(
            F("status") == JobStatus.PENDING.value
        ).count()
        assert pending == 1


# ---------------------------------------------------------------------------
# TestWorkerBatchDispatch
# ---------------------------------------------------------------------------

class TestWorkerBatchDispatch:
    """Verify Worker uses batch claiming to dispatch multiple jobs."""

    async def test_batch_claim_dispatches_multiple(self, db):
        q = Queue(db, default_lease_duration=5)
        await q.init_db()

        _batch_log.clear()
        wrapped = task(q)(batch_task)

        for i in range(3):
            await wrapped.enqueue(f"job_{i}")

        w = Worker(
            q,
            queues=["default"],
            concurrency=3,
            poll_interval=0.01,
            shutdown_timeout=2.0,
        )

        worker_task = asyncio.create_task(w.run())
        try:
            start = asyncio.get_event_loop().time()
            while len(_batch_log) < 3:
                elapsed = asyncio.get_event_loop().time() - start
                if elapsed > 5.0:
                    pytest.fail(
                        f"Worker dispatch timed out after {elapsed:.1f}s: "
                        f"only {len(_batch_log)}/3 jobs completed. "
                        f"Completed: {_batch_log}"
                    )
                await asyncio.sleep(0.02)

            assert len(_batch_log) == 3
            assert set(_batch_log) == {"job_0", "job_1", "job_2"}
        finally:
            w._running = False
            await asyncio.sleep(0.05)
            worker_task.cancel()
            try:
                await worker_task
            except asyncio.CancelledError:
                pass
