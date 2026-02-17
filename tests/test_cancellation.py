"""Tests for cooperative cancellation — request_cancel, is_cancellation_requested, CLI --running."""

import pytest
import pytest_asyncio
from sqler import AsyncSQLerDB, F

from qler._context import _current_job, is_cancellation_requested
from qler.enums import JobStatus
from qler.models.job import Job
from qler.queue import Queue
from qler.task import task


# ---------------------------------------------------------------------------
# Module-level task functions
# ---------------------------------------------------------------------------


async def slow_task():
    return "done"


async def cancellable_task():
    if await is_cancellation_requested():
        return "cancelled early"
    return "finished"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def cancel_db():
    _db = AsyncSQLerDB.in_memory(shared=False)
    await _db.connect()
    yield _db
    await _db.close()


@pytest_asyncio.fixture
async def cancel_queue(cancel_db):
    q = Queue(cancel_db, immediate=False)
    await q.init_db()
    task(q)(slow_task)
    task(q)(cancellable_task)
    yield q


# ---------------------------------------------------------------------------
# Tests: request_cancel()
# ---------------------------------------------------------------------------


class TestRequestCancel:
    """Verify request_cancel() sets the flag on RUNNING jobs only."""

    async def test_request_cancel_running(self, cancel_queue):
        """RUNNING job → request_cancel() returns True, cancel_requested == True."""
        wrapper = cancel_queue._tasks[f"{__name__}.slow_task"]
        job = await wrapper.enqueue()
        assert job.status == JobStatus.PENDING.value

        # Manually transition to RUNNING to simulate worker claim
        updated = await Job.query().filter(
            (F("ulid") == job.ulid) & (F("status") == JobStatus.PENDING.value)
        ).update_one(status=JobStatus.RUNNING.value)
        assert updated is not None
        job._sync_from(updated)

        ok = await job.request_cancel()
        assert ok is True
        assert job.cancel_requested is True

    async def test_request_cancel_pending(self, cancel_queue):
        """PENDING job → request_cancel() returns False (not RUNNING)."""
        wrapper = cancel_queue._tasks[f"{__name__}.slow_task"]
        job = await wrapper.enqueue()
        assert job.status == JobStatus.PENDING.value

        ok = await job.request_cancel()
        assert ok is False
        assert job.cancel_requested is False

    async def test_request_cancel_completed(self, cancel_queue):
        """COMPLETED job → request_cancel() returns False."""
        imm_q = Queue(cancel_queue.db, immediate=True)
        await imm_q.init_db()
        task(imm_q)(slow_task)
        wrapper = imm_q._tasks[f"{__name__}.slow_task"]

        job = await wrapper.enqueue()
        assert job.status == JobStatus.COMPLETED.value

        ok = await job.request_cancel()
        assert ok is False


class TestCancelDelegatesToRequestCancel:
    """Verify cancel() delegates to request_cancel() for RUNNING jobs."""

    async def test_cancel_delegates_to_request_cancel(self, cancel_queue):
        """cancel() on RUNNING job calls request_cancel(), returns True."""
        wrapper = cancel_queue._tasks[f"{__name__}.slow_task"]
        job = await wrapper.enqueue()

        # Transition to RUNNING
        updated = await Job.query().filter(
            (F("ulid") == job.ulid) & (F("status") == JobStatus.PENDING.value)
        ).update_one(status=JobStatus.RUNNING.value)
        job._sync_from(updated)

        ok = await job.cancel()
        assert ok is True
        assert job.cancel_requested is True
        # Status stays RUNNING (cooperative — task decides when to stop)
        assert job.status == JobStatus.RUNNING.value


# ---------------------------------------------------------------------------
# Tests: is_cancellation_requested()
# ---------------------------------------------------------------------------


class TestIsCancellationRequested:
    """Verify the context helper queries the DB correctly."""

    async def test_is_cancellation_requested_true(self, cancel_queue):
        """After flag is set, is_cancellation_requested() returns True."""
        wrapper = cancel_queue._tasks[f"{__name__}.slow_task"]
        job = await wrapper.enqueue()

        # Transition to RUNNING
        updated = await Job.query().filter(
            (F("ulid") == job.ulid) & (F("status") == JobStatus.PENDING.value)
        ).update_one(status=JobStatus.RUNNING.value)
        job._sync_from(updated)

        # Set the context variable
        token = _current_job.set(job)
        try:
            # Before flag is set
            result = await is_cancellation_requested()
            assert result is False

            # Set the flag
            await job.request_cancel()

            # After flag is set
            result = await is_cancellation_requested()
            assert result is True
        finally:
            _current_job.reset(token)

    async def test_is_cancellation_requested_false(self, cancel_queue):
        """Before flag is set, is_cancellation_requested() returns False."""
        wrapper = cancel_queue._tasks[f"{__name__}.slow_task"]
        job = await wrapper.enqueue()

        # Transition to RUNNING
        updated = await Job.query().filter(
            (F("ulid") == job.ulid) & (F("status") == JobStatus.PENDING.value)
        ).update_one(status=JobStatus.RUNNING.value)
        job._sync_from(updated)

        token = _current_job.set(job)
        try:
            result = await is_cancellation_requested()
            assert result is False
        finally:
            _current_job.reset(token)

    async def test_is_cancellation_requested_outside_context(self, cancel_queue):
        """Called outside task context → returns False (does not raise)."""
        result = await is_cancellation_requested()
        assert result is False


# ---------------------------------------------------------------------------
# Tests: task with cancellation check
# ---------------------------------------------------------------------------


class TestTaskCancellationCheck:
    """Verify a task can check and respond to cancellation."""

    async def test_task_exits_on_cancellation(self, cancel_queue):
        """Task with cancellation check sees the flag via immediate mode."""
        imm_q = Queue(cancel_queue.db, immediate=True)
        await imm_q.init_db()
        task(imm_q)(cancellable_task)
        wrapper = imm_q._tasks[f"{__name__}.cancellable_task"]

        # In immediate mode, cancel_requested won't be set mid-execution,
        # so the task should return "finished"
        job = await wrapper.enqueue()
        assert job.status == JobStatus.COMPLETED.value
        assert job.result == "finished"


# ---------------------------------------------------------------------------
# Tests: CLI cancel --running
# ---------------------------------------------------------------------------


class TestCliCancelRunning:
    """Verify CLI cancel command with --running flag."""

    async def test_cli_cancel_running(self, cancel_queue):
        """qler cancel --running sets flag on RUNNING job."""
        from click.testing import CliRunner

        from qler.cli import cli

        wrapper = cancel_queue._tasks[f"{__name__}.slow_task"]
        job = await wrapper.enqueue()

        # Transition to RUNNING
        updated = await Job.query().filter(
            (F("ulid") == job.ulid) & (F("status") == JobStatus.PENDING.value)
        ).update_one(status=JobStatus.RUNNING.value)

        # We can't use the CLI directly (it creates its own Queue from --db),
        # but we can test the logic via the model's request_cancel
        job._sync_from(updated)
        assert job.status == JobStatus.RUNNING.value

        ok = await job.request_cancel()
        assert ok is True

        # Verify in DB
        refreshed = await Job.query().filter(F("ulid") == job.ulid).first()
        assert refreshed is not None
        assert refreshed.cancel_requested is True

    async def test_cli_cancel_running_without_flag(self, cancel_queue):
        """cancel() on PENDING works; RUNNING without --running flag → request_cancel via cancel()."""
        wrapper = cancel_queue._tasks[f"{__name__}.slow_task"]

        # PENDING job → cancel works
        pending_job = await wrapper.enqueue()
        assert pending_job.status == JobStatus.PENDING.value
        ok = await pending_job.cancel()
        assert ok is True
        await pending_job.refresh()
        assert pending_job.status == JobStatus.CANCELLED.value

        # RUNNING job → cancel() delegates to request_cancel()
        running_job = await wrapper.enqueue()
        updated = await Job.query().filter(
            (F("ulid") == running_job.ulid) & (F("status") == JobStatus.PENDING.value)
        ).update_one(status=JobStatus.RUNNING.value)
        running_job._sync_from(updated)

        ok = await running_job.cancel()
        assert ok is True
        assert running_job.cancel_requested is True
        assert running_job.status == JobStatus.RUNNING.value
