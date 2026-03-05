"""Tests for job progress — set_progress(), progress fields, reset on retry."""

import asyncio

import pytest
import pytest_asyncio
from qler._context import _current_job, set_progress
from qler._time import now_epoch
from qler.enums import JobStatus
from qler.models.job import Job
from qler.queue import Queue
from qler.task import task
from qler.worker import Worker
from sqler import AsyncSQLerDB, F

# ---------------------------------------------------------------------------
# Module-level task functions
# ---------------------------------------------------------------------------


async def progress_task():
    """Task that reports progress at known checkpoints."""
    await set_progress(0, "starting")
    await set_progress(50, "halfway")
    await set_progress(100, "done")
    return "finished"


async def progress_fail_task():
    """Task that reports progress then fails."""
    await set_progress(25, "in progress")
    raise RuntimeError("boom after progress")


async def bare_progress_task():
    """Task that sets progress without a message."""
    await set_progress(42)
    return "ok"


async def boundary_progress_task():
    """Task that tests 0 and 100 boundary values."""
    await set_progress(0, "zero")
    await set_progress(100, "hundred")
    return "ok"


async def imm_progress_task():
    """Task for immediate mode progress test."""
    await set_progress(66, "immediate")
    return "imm_ok"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def progress_db():
    _db = AsyncSQLerDB.in_memory(shared=False)
    await _db.connect()
    yield _db
    await _db.close()


@pytest_asyncio.fixture
async def progress_queue(progress_db):
    q = Queue(progress_db)
    await q.init_db()
    yield q


def _make_worker(queue, **kwargs):
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
# Tests: set_progress() context function
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestSetProgress:
    """set_progress() writes to DB and updates local instance."""

    async def test_raises_outside_context(self):
        with pytest.raises(RuntimeError, match="outside of a task execution context"):
            await set_progress(50)

    async def test_rejects_negative_percent(self, progress_queue):
        job = Job(ulid="TEST01", task="x", worker_id="w1")
        token = _current_job.set(job)
        try:
            with pytest.raises(ValueError, match="0–100"):
                await set_progress(-1)
        finally:
            _current_job.reset(token)

    async def test_rejects_over_100(self, progress_queue):
        job = Job(ulid="TEST02", task="x", worker_id="w1")
        token = _current_job.set(job)
        try:
            with pytest.raises(ValueError, match="0–100"):
                await set_progress(101)
        finally:
            _current_job.reset(token)

    async def test_rejects_float(self, progress_queue):
        job = Job(ulid="TEST03", task="x", worker_id="w1")
        token = _current_job.set(job)
        try:
            with pytest.raises(ValueError, match="0–100"):
                await set_progress(50.5)
        finally:
            _current_job.reset(token)

    async def test_updates_db_and_local(self, progress_queue):
        """set_progress writes to DB and updates the local job instance."""
        wrapped = task(progress_queue)(progress_task)
        job = await wrapped.enqueue()

        w = _make_worker(progress_queue)
        result = await _run_worker_until_job_done(w, job)

        assert result.status == JobStatus.COMPLETED.value
        assert result.result == "finished"
        assert result.progress == 100
        assert result.progress_message == "done"

    async def test_progress_persisted_to_db(self, progress_queue):
        """Progress is visible via direct DB query after set_progress."""
        wrapped = task(progress_queue)(progress_task)
        job = await wrapped.enqueue()

        w = _make_worker(progress_queue)
        await _run_worker_until_job_done(w, job)

        # Re-fetch from DB to confirm persistence
        db_job = await Job.query().filter(F("ulid") == job.ulid).first()
        assert db_job is not None
        assert db_job.progress == 100
        assert db_job.progress_message == "done"

    async def test_progress_default_message_empty(self, progress_queue):
        """set_progress with no message defaults to empty string."""
        wrapped = task(progress_queue)(bare_progress_task)
        job = await wrapped.enqueue()

        w = _make_worker(progress_queue)
        result = await _run_worker_until_job_done(w, job)

        assert result.progress == 42
        assert result.progress_message == ""

    async def test_progress_in_immediate_mode(self, progress_db):
        """set_progress works correctly in immediate mode."""
        q = Queue(progress_db, immediate=True)
        await q.init_db()

        wrapped = task(q)(imm_progress_task)
        job = await wrapped.enqueue()

        assert job.status == JobStatus.COMPLETED.value
        assert job.result == "imm_ok"
        assert job.progress == 66
        assert job.progress_message == "immediate"


# ---------------------------------------------------------------------------
# Tests: progress reset on retry
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestProgressReset:
    """Progress fields are cleared when a job is retried."""

    async def test_auto_retry_resets_progress(self, progress_db):
        """Automatic retry (fail with max_retries>0) clears progress."""
        q = Queue(progress_db, default_max_retries=1)
        await q.init_db()

        wrapped = task(q)(progress_fail_task)
        job = await wrapped.enqueue()

        w = _make_worker(q)
        worker_task = asyncio.create_task(w.run())
        try:
            # Wait for first attempt to fail and job to go back to pending
            for _ in range(200):
                await asyncio.sleep(0.02)
                await job.refresh()
                if job.retry_count >= 1:
                    break
            assert job.retry_count >= 1
            assert job.status == JobStatus.PENDING.value
            # Before reset: progress_fail_task sets progress=25, but
            # auto-retry should have cleared it
            assert job.progress is None
            assert job.progress_message == ""
        finally:
            w._running = False
            await asyncio.sleep(0.05)
            worker_task.cancel()
            try:
                await worker_task
            except asyncio.CancelledError:
                pass

    async def test_manual_retry_resets_progress(self, progress_queue):
        """job.retry() on a FAILED job clears progress fields."""
        wrapped = task(progress_queue)(progress_fail_task)
        job = await wrapped.enqueue()

        w = _make_worker(progress_queue)
        worker_task = asyncio.create_task(w.run())
        try:
            # Wait for job to fail terminally (no retries)
            for _ in range(200):
                await asyncio.sleep(0.02)
                await job.refresh()
                if job.status == JobStatus.FAILED.value:
                    break
            assert job.status == JobStatus.FAILED.value
            # Progress should still have the value set before failure
            assert job.progress == 25
            assert job.progress_message == "in progress"
        finally:
            w._running = False
            await asyncio.sleep(0.05)
            worker_task.cancel()
            try:
                await worker_task
            except asyncio.CancelledError:
                pass

        # Now manually retry — progress should be cleared
        ok = await job.retry()
        assert ok is True
        assert job.status == JobStatus.PENDING.value
        assert job.progress is None
        assert job.progress_message == ""

    async def test_dlq_replay_resets_progress(self, progress_db):
        """replay_job() from DLQ clears progress fields."""
        q = Queue(progress_db, dlq="dead_letters")
        await q.init_db()

        wrapped = task(q)(progress_fail_task)
        job = await wrapped.enqueue()

        w = _make_worker(q)
        worker_task = asyncio.create_task(w.run())
        try:
            # Wait for job to fail and move to DLQ
            for _ in range(200):
                await asyncio.sleep(0.02)
                await job.refresh()
                if job.status == JobStatus.FAILED.value:
                    break
            assert job.status == JobStatus.FAILED.value
            assert job.queue_name == "dead_letters"
            # Confirm progress was set before failure
            assert job.progress == 25
            assert job.progress_message == "in progress"
        finally:
            w._running = False
            await asyncio.sleep(0.05)
            worker_task.cancel()
            try:
                await worker_task
            except asyncio.CancelledError:
                pass

        # Replay from DLQ — progress should be cleared
        replayed = await q.replay_job(job)
        assert replayed is not None
        assert replayed.status == JobStatus.PENDING.value
        assert replayed.progress is None
        assert replayed.progress_message == ""

    async def test_lease_expiry_resets_progress(self, progress_db):
        """recover_expired_leases() clears progress on recovered jobs."""
        q = Queue(progress_db, default_lease_duration=1)
        await q.init_db()

        # Enqueue and manually claim a job, then set progress on it
        wrapped = task(q)(progress_task)
        job = await wrapped.enqueue()

        # Simulate a claimed job with progress that has an expired lease
        now = now_epoch()
        claimed = await Job.query().filter(
            (F("ulid") == job.ulid) & (F("status") == JobStatus.PENDING.value)
        ).update_one(
            status=JobStatus.RUNNING.value,
            worker_id="dead-worker",
            lease_expires_at=now - 10,  # Already expired
            progress=75,
            progress_message="three quarters",
            updated_at=now,
        )
        assert claimed is not None
        assert claimed.progress == 75
        assert claimed.progress_message == "three quarters"

        # Recover expired leases
        recovered = await q.recover_expired_leases()
        assert recovered == 1

        # Verify progress was reset
        await job.refresh()
        assert job.status == JobStatus.PENDING.value
        assert job.progress is None
        assert job.progress_message == ""


# ---------------------------------------------------------------------------
# Tests: Job model defaults
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestProgressDefaults:
    """Progress fields have correct defaults on new jobs."""

    async def test_new_job_has_no_progress(self, progress_queue):
        wrapped = task(progress_queue)(progress_task)
        job = await wrapped.enqueue()
        assert job.progress is None
        assert job.progress_message == ""

    async def test_progress_boundary_values(self, progress_queue):
        """0 and 100 are both valid progress values."""
        wrapped = task(progress_queue)(boundary_progress_task)
        job = await wrapped.enqueue()

        w = _make_worker(progress_queue)
        result = await _run_worker_until_job_done(w, job)
        assert result.progress == 100
        assert result.progress_message == "hundred"
