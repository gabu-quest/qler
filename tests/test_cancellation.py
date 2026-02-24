"""Tests for cooperative cancellation — request_cancel, is_cancellation_requested, CLI --running."""

import asyncio
import json

import pytest
import pytest_asyncio
from click.testing import CliRunner
from sqler import AsyncSQLerDB, F

from qler._context import _current_job, is_cancellation_requested
from qler._time import now_epoch
from qler.cli import cli
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


async def _transition_to_running(job: Job) -> Job:
    """Helper: atomically transition a PENDING job to RUNNING."""
    updated = await Job.query().filter(
        (F("ulid") == job.ulid) & (F("status") == JobStatus.PENDING.value)
    ).update_one(status=JobStatus.RUNNING.value, worker_id="test-worker")
    assert updated is not None
    job._sync_from(updated)
    return job


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

        await _transition_to_running(job)

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

    async def test_request_cancel_failed(self, cancel_queue):
        """FAILED job → request_cancel() returns False."""
        wrapper = cancel_queue._tasks[f"{__name__}.slow_task"]
        job = await wrapper.enqueue()
        await _transition_to_running(job)

        # Transition to FAILED
        now = now_epoch()
        updated = await Job.query().filter(
            (F("ulid") == job.ulid) & (F("status") == JobStatus.RUNNING.value)
        ).update_one(status=JobStatus.FAILED.value, finished_at=now)
        job._sync_from(updated)

        ok = await job.request_cancel()
        assert ok is False

    async def test_request_cancel_cancelled(self, cancel_queue):
        """CANCELLED job → request_cancel() returns False."""
        wrapper = cancel_queue._tasks[f"{__name__}.slow_task"]
        job = await wrapper.enqueue()
        ok = await job.cancel()
        assert ok is True
        assert job.status == JobStatus.CANCELLED.value

        ok = await job.request_cancel()
        assert ok is False


class TestCancelDelegatesToRequestCancel:
    """Verify cancel() delegates to request_cancel() for RUNNING jobs."""

    async def test_cancel_delegates_to_request_cancel(self, cancel_queue):
        """cancel() on RUNNING job calls request_cancel(), returns True."""
        wrapper = cancel_queue._tasks[f"{__name__}.slow_task"]
        job = await wrapper.enqueue()
        await _transition_to_running(job)

        ok = await job.cancel()
        assert ok is True
        assert job.cancel_requested is True
        # Status stays RUNNING (cooperative — task decides when to stop)
        assert job.status == JobStatus.RUNNING.value

    async def test_cancel_pending_then_running(self, cancel_queue):
        """cancel() on PENDING → CANCELLED; cancel() on RUNNING → cooperative."""
        wrapper = cancel_queue._tasks[f"{__name__}.slow_task"]

        # PENDING → immediate cancel
        pending_job = await wrapper.enqueue()
        ok = await pending_job.cancel()
        assert ok is True
        await pending_job.refresh()
        assert pending_job.status == JobStatus.CANCELLED.value

        # RUNNING → cooperative cancel
        running_job = await wrapper.enqueue()
        await _transition_to_running(running_job)
        ok = await running_job.cancel()
        assert ok is True
        assert running_job.cancel_requested is True
        assert running_job.status == JobStatus.RUNNING.value


# ---------------------------------------------------------------------------
# Tests: is_cancellation_requested()
# ---------------------------------------------------------------------------


class TestIsCancellationRequested:
    """Verify the context helper queries the DB correctly."""

    async def test_is_cancellation_requested_true(self, cancel_queue):
        """After flag is set, is_cancellation_requested() returns True."""
        wrapper = cancel_queue._tasks[f"{__name__}.slow_task"]
        job = await wrapper.enqueue()
        await _transition_to_running(job)

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
        await _transition_to_running(job)

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

    async def test_is_cancellation_requested_job_deleted(self, cancel_queue):
        """Job deleted from DB mid-execution → returns False (defensive guard)."""
        wrapper = cancel_queue._tasks[f"{__name__}.slow_task"]
        job = await wrapper.enqueue()
        await _transition_to_running(job)

        token = _current_job.set(job)
        try:
            # Delete the job from DB
            adapter = cancel_queue.db.adapter
            cur = await adapter.execute(
                "DELETE FROM qler_jobs WHERE ulid = ?", [job.ulid]
            )
            await cur.close()
            await adapter.auto_commit()

            result = await is_cancellation_requested()
            assert result is False
        finally:
            _current_job.reset(token)

    async def test_is_cancellation_requested_worker_id_mismatch(self, cancel_queue):
        """If job was reclaimed by another worker, returns False."""
        wrapper = cancel_queue._tasks[f"{__name__}.slow_task"]
        job = await wrapper.enqueue()
        await _transition_to_running(job)

        # Set cancel_requested AND change worker_id (simulating reclaim)
        await Job.query().filter(F("ulid") == job.ulid).update_one(
            cancel_requested=True,
            worker_id="other-worker",
        )

        token = _current_job.set(job)
        try:
            # Our worker_id is "test-worker" but DB says "other-worker"
            result = await is_cancellation_requested()
            assert result is False
        finally:
            _current_job.reset(token)


# ---------------------------------------------------------------------------
# Tests: task with cancellation check
# ---------------------------------------------------------------------------


class TestTaskCancellationCheck:
    """Verify a task can check and respond to cancellation."""

    async def test_task_runs_to_completion_when_not_cancelled(self, cancel_queue):
        """Task completes normally when cancel_requested is not set."""
        imm_q = Queue(cancel_queue.db, immediate=True)
        await imm_q.init_db()
        task(imm_q)(cancellable_task)
        wrapper = imm_q._tasks[f"{__name__}.cancellable_task"]

        job = await wrapper.enqueue()
        assert job.status == JobStatus.COMPLETED.value
        assert job.result == "finished"

    async def test_task_reads_cancel_flag_when_preflagged(self, cancel_queue):
        """Task checks is_cancellation_requested() and returns early when flag is pre-set."""
        imm_q = Queue(cancel_queue.db, immediate=True)
        await imm_q.init_db()
        task(imm_q)(cancellable_task)
        wrapper = imm_q._tasks[f"{__name__}.cancellable_task"]

        # Enqueue without immediate execution (use non-immediate queue)
        non_imm_wrapper = cancel_queue._tasks[f"{__name__}.cancellable_task"]
        job = await non_imm_wrapper.enqueue()
        assert job.status == JobStatus.PENDING.value

        # Pre-set cancel_requested on the PENDING job
        await Job.query().filter(F("ulid") == job.ulid).update_one(
            cancel_requested=True
        )

        # Execute via immediate mode
        result_job = await imm_q._execute_immediate(job)
        assert result_job.status == JobStatus.COMPLETED.value
        assert result_job.result == "cancelled early"


# ---------------------------------------------------------------------------
# Tests: CLI cancel --running (real CliRunner)
# ---------------------------------------------------------------------------


class TestCliCancelRunning:
    """Verify CLI cancel command with --running flag using real CliRunner."""

    @pytest.fixture
    def runner(self):
        return CliRunner()

    @pytest.fixture
    def db_path(self, tmp_path):
        return str(tmp_path / "cancel-test.db")

    def test_cli_cancel_running_by_ulid(self, runner, db_path):
        """qler cancel <ulid> --running on a RUNNING job → cancellation requested."""

        async def _setup():
            q = Queue(db_path)
            await q.init_db()
            j = await q.enqueue("myapp.tasks.long_running")
            # Transition to RUNNING
            now = now_epoch()
            await Job.query().filter(
                (F("ulid") == j.ulid) & (F("status") == JobStatus.PENDING.value)
            ).update_one(
                status=JobStatus.RUNNING.value,
                worker_id="worker-1",
                updated_at=now,
            )
            await q.close()
            return j.ulid

        ulid = asyncio.run(_setup())
        result = runner.invoke(cli, ["cancel", ulid, "--db", db_path, "--running", "--json"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["cancellation_requested"] == 1
        assert data["requested_ulids"] == [ulid]
        assert data["cancelled"] == 0

    def test_cli_cancel_running_without_flag_rejects(self, runner, db_path):
        """qler cancel <ulid> on RUNNING without --running → error."""

        async def _setup():
            q = Queue(db_path)
            await q.init_db()
            j = await q.enqueue("myapp.tasks.long_running")
            await Job.query().filter(
                (F("ulid") == j.ulid) & (F("status") == JobStatus.PENDING.value)
            ).update_one(status=JobStatus.RUNNING.value, worker_id="w-1")
            await q.close()
            return j.ulid

        ulid = asyncio.run(_setup())
        result = runner.invoke(cli, ["cancel", ulid, "--db", db_path])
        assert result.exit_code == 1
        assert "running" in result.output.lower()
        assert "--running" in result.output

    def test_cli_cancel_all_with_running(self, runner, db_path):
        """qler cancel --all --running cancels PENDING and requests RUNNING."""

        async def _setup():
            q = Queue(db_path)
            await q.init_db()
            # 2 PENDING
            await q.enqueue("myapp.tasks.p1")
            await q.enqueue("myapp.tasks.p2")
            # 1 RUNNING
            j = await q.enqueue("myapp.tasks.r1")
            await Job.query().filter(
                (F("ulid") == j.ulid) & (F("status") == JobStatus.PENDING.value)
            ).update_one(status=JobStatus.RUNNING.value, worker_id="w-1")
            await q.close()

        asyncio.run(_setup())
        result = runner.invoke(cli, [
            "cancel", "--all", "--running", "--db", db_path, "--json"
        ])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["cancelled"] == 2
        assert data["cancellation_requested"] == 1

    def test_cli_cancel_all_without_running_skips_running(self, runner, db_path):
        """qler cancel --all (without --running) only cancels PENDING."""

        async def _setup():
            q = Queue(db_path)
            await q.init_db()
            await q.enqueue("myapp.tasks.p1")
            j = await q.enqueue("myapp.tasks.r1")
            await Job.query().filter(
                (F("ulid") == j.ulid) & (F("status") == JobStatus.PENDING.value)
            ).update_one(status=JobStatus.RUNNING.value, worker_id="w-1")
            await q.close()

        asyncio.run(_setup())
        result = runner.invoke(cli, [
            "cancel", "--all", "--db", db_path, "--json"
        ])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["cancelled"] == 1
        assert data["cancellation_requested"] == 0

    def test_cli_cancel_nonexistent_ulid(self, runner, db_path):
        """qler cancel <nonexistent> → error."""
        runner.invoke(cli, ["init", "--db", db_path])
        result = runner.invoke(cli, ["cancel", "nonexistent-ulid", "--db", db_path])
        assert result.exit_code == 1
        assert "not found" in result.output.lower()
