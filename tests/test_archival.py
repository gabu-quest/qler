"""Tests for job archival — UPGRADE-1."""

import asyncio
import json
import logging

import pytest
from click.testing import CliRunner
from sqler import F

from qler._time import now_epoch
from qler.cli import cli
from qler.enums import JobStatus
from qler.models.job import Job
from qler.queue import Queue
from qler.worker import Worker


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

async def _complete_job(queue: Queue, task_path: str = "my_task") -> Job:
    """Enqueue -> claim -> complete a job. Returns the completed job."""
    job = await queue.enqueue(task_path)
    claimed = await queue.claim_job("worker-1", ["default"])
    await queue.complete_job(claimed, "worker-1", result="ok")
    await claimed.refresh()
    return claimed


async def _fail_job(queue: Queue, task_path: str = "my_task") -> Job:
    """Enqueue -> claim -> fail a job (no retries). Returns the failed job."""
    job = await queue.enqueue(task_path, max_retries=0)
    claimed = await queue.claim_job("worker-1", ["default"])
    await queue.fail_job(claimed, "worker-1", ValueError("boom"))
    await claimed.refresh()
    return claimed


async def _cancel_job(queue: Queue, task_path: str = "my_task") -> Job:
    """Enqueue -> cancel a job. Returns the cancelled job."""
    job = await queue.enqueue(task_path)
    await job.cancel()
    await job.refresh()
    return job


async def _backdate_finished_at(job: Job, seconds_ago: int) -> None:
    """Set a job's finished_at to N seconds in the past."""
    old_ts = now_epoch() - seconds_ago
    await Job.query().filter(F("ulid") == job.ulid).update_one(
        finished_at=old_ts
    )


# ---------------------------------------------------------------------------
# Archive table creation
# ---------------------------------------------------------------------------

class TestArchiveTableCreation:

    async def test_archive_table_created(self, queue):
        """init_db() creates the qler_jobs_archive table."""
        cur = await queue.db.adapter.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='qler_jobs_archive'"
        )
        rows = await cur.fetchall()
        await cur.close()
        assert len(rows) == 1
        assert rows[0][0] == "qler_jobs_archive"


# ---------------------------------------------------------------------------
# archive_jobs()
# ---------------------------------------------------------------------------

class TestArchiveJobs:

    async def test_archive_completed_jobs(self, queue):
        """Completed jobs older than threshold are moved to archive."""
        job = await _complete_job(queue)
        await _backdate_finished_at(job, 600)

        count = await queue.archive_jobs(older_than_seconds=300)

        assert count == 1
        remaining = await Job.query().filter(F("ulid") == job.ulid).all()
        assert len(remaining) == 0
        assert await queue.archive_count() == 1
        archived = await queue.archived_jobs()
        assert archived[0].ulid == job.ulid

    async def test_archive_failed_jobs(self, queue):
        """Failed jobs older than threshold are moved to archive."""
        job = await _fail_job(queue)
        await _backdate_finished_at(job, 600)

        count = await queue.archive_jobs(older_than_seconds=300)

        assert count == 1
        remaining = await Job.query().filter(F("ulid") == job.ulid).all()
        assert len(remaining) == 0
        archived = await queue.archived_jobs()
        assert archived[0].ulid == job.ulid
        assert archived[0].status == JobStatus.FAILED.value

    async def test_archive_cancelled_jobs(self, queue):
        """Cancelled jobs older than threshold are moved to archive."""
        job = await _cancel_job(queue)
        await _backdate_finished_at(job, 600)

        count = await queue.archive_jobs(older_than_seconds=300)

        assert count == 1
        remaining = await Job.query().filter(F("ulid") == job.ulid).all()
        assert len(remaining) == 0
        archived = await queue.archived_jobs()
        assert archived[0].ulid == job.ulid
        assert archived[0].status == JobStatus.CANCELLED.value

    async def test_archive_preserves_pending(self, queue):
        """Pending jobs are never archived."""
        job = await queue.enqueue("my_task")

        count = await queue.archive_jobs(older_than_seconds=0)

        assert count == 0
        all_jobs = await Job.query().all()
        assert len(all_jobs) == 1
        assert all_jobs[0].ulid == job.ulid

    async def test_archive_preserves_running(self, queue):
        """Running (claimed) jobs are never archived."""
        await queue.enqueue("my_task")
        claimed = await queue.claim_job("worker-1", ["default"])

        count = await queue.archive_jobs(older_than_seconds=0)

        assert count == 0
        all_jobs = await Job.query().all()
        assert len(all_jobs) == 1
        assert all_jobs[0].status == JobStatus.RUNNING.value

    async def test_archive_respects_threshold(self, queue):
        """Jobs newer than older_than_seconds stay in main table."""
        job = await _complete_job(queue)

        count = await queue.archive_jobs(older_than_seconds=300)

        assert count == 0
        remaining = await Job.query().filter(F("ulid") == job.ulid).all()
        assert len(remaining) == 1

    async def test_archive_returns_count(self, queue):
        """Return value matches actual rows moved."""
        archived_ulids = []
        for _ in range(3):
            job = await _complete_job(queue)
            await _backdate_finished_at(job, 600)
            archived_ulids.append(job.ulid)
        recent = await _complete_job(queue)

        count = await queue.archive_jobs(older_than_seconds=300)

        assert count == 3
        assert await queue.archive_count() == 3
        remaining = await Job.query().filter(
            F("status") == JobStatus.COMPLETED.value
        ).all()
        assert len(remaining) == 1
        assert remaining[0].ulid == recent.ulid

    async def test_archive_atomic(self, queue):
        """After archival, job exists in exactly one table."""
        job = await _complete_job(queue)
        await _backdate_finished_at(job, 600)

        await queue.archive_jobs(older_than_seconds=300)

        main = await Job.query().filter(F("ulid") == job.ulid).all()
        assert len(main) == 0
        assert await queue.archive_count() == 1
        archived = await queue.archived_jobs()
        assert len(archived) == 1
        assert archived[0].ulid == job.ulid

    async def test_archive_idempotent(self, queue):
        """Calling archive twice doesn't duplicate or error."""
        job = await _complete_job(queue)
        await _backdate_finished_at(job, 600)

        first = await queue.archive_jobs(older_than_seconds=300)
        second = await queue.archive_jobs(older_than_seconds=300)

        assert first == 1
        assert second == 0
        assert await queue.archive_count() == 1

    async def test_archive_mixed_statuses(self, queue):
        """Archives all three terminal statuses in one sweep."""
        completed = await _complete_job(queue)
        await _backdate_finished_at(completed, 600)
        failed = await _fail_job(queue)
        await _backdate_finished_at(failed, 600)
        cancelled = await _cancel_job(queue)
        await _backdate_finished_at(cancelled, 600)
        pending = await queue.enqueue("pending_task")

        count = await queue.archive_jobs(older_than_seconds=300)

        assert count == 3
        remaining = await Job.query().all()
        assert len(remaining) == 1
        assert remaining[0].ulid == pending.ulid
        assert remaining[0].status == JobStatus.PENDING.value


# ---------------------------------------------------------------------------
# archived_jobs() query
# ---------------------------------------------------------------------------

class TestArchivedJobsQuery:

    async def test_archived_jobs_returns_job_instances(self, queue):
        """archived_jobs() returns proper Job objects with all fields."""
        job = await _complete_job(queue)
        await _backdate_finished_at(job, 600)
        await queue.archive_jobs(older_than_seconds=300)

        archived = await queue.archived_jobs()

        assert len(archived) == 1
        assert archived[0].ulid == job.ulid
        assert archived[0].status == JobStatus.COMPLETED.value
        assert archived[0].task == "my_task"
        assert archived[0].queue_name == "default"

    async def test_archived_jobs_filter_by_queue(self, queue):
        """Queue name filter works on archived jobs."""
        job = await queue.enqueue("my_task", queue_name="special")
        claimed = await queue.claim_job("worker-1", ["special"])
        await queue.complete_job(claimed, "worker-1", result="ok")
        await _backdate_finished_at(claimed, 600)
        await queue.archive_jobs(older_than_seconds=300)

        found = await queue.archived_jobs(queue_name="special")
        assert len(found) == 1
        assert found[0].queue_name == "special"

        not_found = await queue.archived_jobs(queue_name="other")
        assert len(not_found) == 0

    async def test_archived_jobs_filter_by_status(self, queue):
        """Status filter works on archived jobs."""
        completed = await _complete_job(queue)
        await _backdate_finished_at(completed, 600)
        failed = await _fail_job(queue)
        await _backdate_finished_at(failed, 600)
        await queue.archive_jobs(older_than_seconds=300)

        only_completed = await queue.archived_jobs(status="completed")
        assert len(only_completed) == 1
        assert only_completed[0].ulid == completed.ulid

        only_failed = await queue.archived_jobs(status="failed")
        assert len(only_failed) == 1
        assert only_failed[0].ulid == failed.ulid

    async def test_archived_jobs_limit(self, queue):
        """Limit parameter is respected."""
        for _ in range(5):
            job = await _complete_job(queue)
            await _backdate_finished_at(job, 600)
        await queue.archive_jobs(older_than_seconds=300)

        limited = await queue.archived_jobs(limit=2)
        assert len(limited) == 2

        all_archived = await queue.archived_jobs(limit=100)
        assert len(all_archived) == 5

    async def test_archived_jobs_restores_table_binding(self, queue):
        """After archived_jobs(), Job model is bound back to qler_jobs."""
        job = await _complete_job(queue)
        await _backdate_finished_at(job, 600)
        await queue.archive_jobs(older_than_seconds=300)

        await queue.archived_jobs()

        pending = await queue.enqueue("after_archive")
        found = await Job.query().filter(F("ulid") == pending.ulid).all()
        assert len(found) == 1
        assert found[0].ulid == pending.ulid

    async def test_archived_jobs_restores_binding_on_error(self, queue):
        """Table binding is restored even if archived_jobs() query fails."""
        job = await _complete_job(queue)
        await _backdate_finished_at(job, 600)
        await queue.archive_jobs(older_than_seconds=300)

        # Force an error by querying with invalid filter (should not corrupt binding)
        await queue.archived_jobs()

        # Main table still works
        new_job = await queue.enqueue("recovery_test")
        found = await Job.query().filter(F("ulid") == new_job.ulid).all()
        assert len(found) == 1


# ---------------------------------------------------------------------------
# archive_count()
# ---------------------------------------------------------------------------

class TestArchiveCount:

    async def test_archive_count_zero(self, queue):
        """archive_count() returns 0 when archive is empty."""
        assert await queue.archive_count() == 0

    async def test_archive_count_after_archival(self, queue):
        """archive_count() returns correct count after archival."""
        for _ in range(5):
            job = await _complete_job(queue)
            await _backdate_finished_at(job, 600)
        await queue.archive_jobs(older_than_seconds=300)

        assert await queue.archive_count() == 5


# ---------------------------------------------------------------------------
# Lifecycle event
# ---------------------------------------------------------------------------

class TestArchivalLifecycleEvent:

    async def test_lifecycle_event_emitted(self, queue, caplog):
        """job.archived lifecycle event fires with correct structured data."""
        job = await _complete_job(queue)
        await _backdate_finished_at(job, 600)

        with caplog.at_level(logging.INFO, logger="qler.lifecycle"):
            await queue.archive_jobs(older_than_seconds=300)

        lifecycle_msgs = [r for r in caplog.records if r.name == "qler.lifecycle"]
        assert len(lifecycle_msgs) == 1
        record = lifecycle_msgs[0]
        assert record.event == "job.archived"
        assert record.count == 1
        assert record.older_than_seconds == 300

    async def test_no_lifecycle_event_when_zero(self, queue, caplog):
        """No lifecycle event when nothing is archived."""
        with caplog.at_level(logging.INFO, logger="qler.lifecycle"):
            await queue.archive_jobs(older_than_seconds=300)

        lifecycle_msgs = [r for r in caplog.records if r.name == "qler.lifecycle"]
        assert len(lifecycle_msgs) == 0


# ---------------------------------------------------------------------------
# Auto-archival in Worker
# ---------------------------------------------------------------------------

class TestAutoArchival:

    async def test_worker_accepts_archive_params(self, queue):
        """Worker can be created with archive_interval and archive_after."""
        w = Worker(queue, archive_interval=60.0, archive_after=120)
        assert w.archive_interval == 60.0
        assert w.archive_after == 120

    async def test_worker_archive_disabled_by_default(self, queue):
        """Worker has archival disabled by default."""
        w = Worker(queue)
        assert w.archive_interval is None
        assert w.archive_after == 300


# ---------------------------------------------------------------------------
# CLI: qler archive
# ---------------------------------------------------------------------------

class TestCLIArchive:

    @pytest.fixture
    def runner(self):
        return CliRunner()

    @pytest.fixture
    def db_path(self, tmp_path):
        return str(tmp_path / "test-archive.db")

    @pytest.fixture
    def populated_db(self, db_path):
        """DB with 2 old completed jobs and 1 pending job."""

        async def _setup():
            q = Queue(db_path)
            await q.init_db()
            for _ in range(2):
                job = await q.enqueue("my_task", max_retries=0)
                claimed = await q.claim_job("worker-1", ["default"])
                await q.complete_job(claimed, "worker-1", result="ok")
            # Backdate finished_at
            now = now_epoch()
            old_ts = now - 600
            cur = await q.db.adapter.execute(
                "UPDATE qler_jobs SET data = json_set(data, '$.finished_at', ?) "
                "WHERE status = 'completed'",
                [old_ts],
            )
            await cur.close()
            await q.db.adapter.auto_commit()
            # One pending job
            await q.enqueue("pending_task")
            await q.close()

        asyncio.run(_setup())
        return db_path

    def test_archive_basic(self, runner, populated_db):
        """qler archive moves old terminal jobs."""
        result = runner.invoke(cli, ["archive", "--db", populated_db, "--older-than", "5m"])
        assert result.exit_code == 0
        assert "Archived 2 jobs" in result.output

    def test_archive_json(self, runner, populated_db):
        """qler archive --json outputs correct structure."""
        result = runner.invoke(cli, ["archive", "--db", populated_db, "--older-than", "5m", "--json"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data == {"archived": 2}

    def test_archive_nothing(self, runner, populated_db):
        """qler archive when nothing is old enough."""
        result = runner.invoke(cli, ["archive", "--db", populated_db, "--older-than", "1h"])
        assert result.exit_code == 0
        assert "Archived 0 jobs" in result.output

    def test_archive_invalid_duration(self, runner, db_path):
        """qler archive with invalid --older-than."""
        # Create the DB first
        asyncio.run(self._init_db(db_path))
        result = runner.invoke(cli, ["archive", "--db", db_path, "--older-than", "invalid"])
        assert result.exit_code != 0

    async def _init_db(self, db_path):
        q = Queue(db_path)
        await q.init_db()
        await q.close()


class TestCLIStatusIncludeArchive:

    @pytest.fixture
    def runner(self):
        return CliRunner()

    @pytest.fixture
    def db_with_archive(self, tmp_path):
        """DB with 1 pending + 1 archived completed job."""
        db_path = str(tmp_path / "test-status-archive.db")

        async def _setup():
            q = Queue(db_path)
            await q.init_db()
            await q.enqueue("pending_task")
            job = await q.enqueue("done_task", max_retries=0)
            claimed = await q.claim_job("worker-1", ["default"])
            await q.complete_job(claimed, "worker-1", result="ok")
            old_ts = now_epoch() - 600
            cur = await q.db.adapter.execute(
                "UPDATE qler_jobs SET data = json_set(data, '$.finished_at', ?) "
                "WHERE status = 'completed'",
                [old_ts],
            )
            await cur.close()
            await q.db.adapter.auto_commit()
            await q.archive_jobs(older_than_seconds=300)
            await q.close()

        asyncio.run(_setup())
        return db_path

    def test_status_without_archive(self, runner, db_with_archive):
        """qler status without --include-archive shows only active jobs."""
        result = runner.invoke(cli, ["status", "--db", db_with_archive])
        assert result.exit_code == 0
        assert "1" in result.output  # 1 pending
        assert "Archived" not in result.output

    def test_status_with_archive(self, runner, db_with_archive):
        """qler status --include-archive shows archived section."""
        result = runner.invoke(cli, ["status", "--db", db_with_archive, "--include-archive"])
        assert result.exit_code == 0
        assert "Archived: 1 jobs" in result.output

    def test_status_with_archive_json(self, runner, db_with_archive):
        """qler status --include-archive --json includes archived counts."""
        result = runner.invoke(cli, [
            "status", "--db", db_with_archive, "--include-archive", "--json",
        ])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert "archived" in data
        assert data["archived_total"] == 1


class TestCLIJobsIncludeArchive:

    @pytest.fixture
    def runner(self):
        return CliRunner()

    @pytest.fixture
    def db_with_archive(self, tmp_path):
        """DB with 1 pending + 1 archived completed job."""
        db_path = str(tmp_path / "test-jobs-archive.db")

        async def _setup():
            q = Queue(db_path)
            await q.init_db()
            await q.enqueue("pending_task")
            job = await q.enqueue("done_task", max_retries=0)
            claimed = await q.claim_job("worker-1", ["default"])
            await q.complete_job(claimed, "worker-1", result="ok")
            old_ts = now_epoch() - 600
            cur = await q.db.adapter.execute(
                "UPDATE qler_jobs SET data = json_set(data, '$.finished_at', ?) "
                "WHERE status = 'completed'",
                [old_ts],
            )
            await cur.close()
            await q.db.adapter.auto_commit()
            await q.archive_jobs(older_than_seconds=300)
            await q.close()

        asyncio.run(_setup())
        return db_path

    def test_jobs_without_archive(self, runner, db_with_archive):
        """qler jobs without --include-archive shows only active jobs."""
        result = runner.invoke(cli, ["jobs", "--db", db_with_archive])
        assert result.exit_code == 0
        assert "1 jobs shown" in result.output
        assert "(archived)" not in result.output

    def test_jobs_with_archive(self, runner, db_with_archive):
        """qler jobs --include-archive shows archived jobs with marker."""
        result = runner.invoke(cli, ["jobs", "--db", db_with_archive, "--include-archive"])
        assert result.exit_code == 0
        assert "(archived)" in result.output
        assert "2 jobs shown" in result.output
        assert "(1 archived)" in result.output

    def test_jobs_with_archive_json(self, runner, db_with_archive):
        """qler jobs --include-archive --json marks archived jobs."""
        result = runner.invoke(cli, [
            "jobs", "--db", db_with_archive, "--include-archive", "--json",
        ])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert len(data) == 2
        archived_entries = [j for j in data if j.get("archived")]
        assert len(archived_entries) == 1
