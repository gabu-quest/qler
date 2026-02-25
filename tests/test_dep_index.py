"""Tests for the reverse dependency index (qler_job_deps table)."""

import pytest
import pytest_asyncio

from qler.queue import Queue


pytestmark = pytest.mark.asyncio


class TestDepTable:
    """Verify qler_job_deps table and index are created by init_db()."""

    async def test_dep_table_created(self, queue: Queue):
        """qler_job_deps table exists after init_db()."""
        cur = await queue._db.adapter.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='qler_job_deps'"
        )
        row = await cur.fetchone()
        await cur.close()
        assert row is not None
        assert row[0] == "qler_job_deps"

    async def test_dep_index_created(self, queue: Queue):
        """idx_qler_job_deps_parent index exists after init_db()."""
        cur = await queue._db.adapter.execute(
            "SELECT name FROM sqlite_master WHERE type='index' AND name='idx_qler_job_deps_parent'"
        )
        row = await cur.fetchone()
        await cur.close()
        assert row is not None
        assert row[0] == "idx_qler_job_deps_parent"


class TestReverseMapPopulation:
    """Verify enqueue/enqueue_many populate the reverse map."""

    async def test_reverse_map_populated_on_enqueue(self, queue: Queue):
        """enqueue() with depends_on creates (parent, child) rows."""
        parent = await queue.enqueue("my.task", queue_name="q")
        child = await queue.enqueue(
            "my.task", queue_name="q", depends_on=[parent.ulid]
        )

        cur = await queue._db.adapter.execute(
            "SELECT parent_ulid, child_ulid FROM qler_job_deps ORDER BY child_ulid"
        )
        rows = await cur.fetchall()
        await cur.close()

        assert len(rows) == 1
        assert rows[0][0] == parent.ulid
        assert rows[0][1] == child.ulid

    async def test_reverse_map_multiple_parents(self, queue: Queue):
        """A child depending on two parents creates two rows."""
        p1 = await queue.enqueue("my.task", queue_name="q")
        p2 = await queue.enqueue("my.task", queue_name="q")
        child = await queue.enqueue(
            "my.task", queue_name="q", depends_on=[p1.ulid, p2.ulid]
        )

        cur = await queue._db.adapter.execute(
            "SELECT parent_ulid FROM qler_job_deps WHERE child_ulid = ? ORDER BY parent_ulid",
            [child.ulid],
        )
        rows = await cur.fetchall()
        await cur.close()

        assert len(rows) == 2
        parent_ulids = {r[0] for r in rows}
        assert parent_ulids == {p1.ulid, p2.ulid}

    async def test_reverse_map_populated_on_enqueue_many(self, queue: Queue):
        """enqueue_many() with deps creates reverse map rows."""
        parent = await queue.enqueue("my.task", queue_name="q")
        jobs = await queue.enqueue_many([
            {"task_path": "my.task", "queue_name": "q", "depends_on": [parent.ulid]},
            {"task_path": "my.task", "queue_name": "q", "depends_on": [parent.ulid]},
        ])

        cur = await queue._db.adapter.execute(
            "SELECT child_ulid FROM qler_job_deps WHERE parent_ulid = ? ORDER BY child_ulid",
            [parent.ulid],
        )
        rows = await cur.fetchall()
        await cur.close()

        assert len(rows) == 2
        child_ulids = {r[0] for r in rows}
        assert child_ulids == {jobs[0].ulid, jobs[1].ulid}

    async def test_reverse_map_no_rows_without_deps(self, queue: Queue):
        """enqueue() without depends_on creates no reverse map rows."""
        await queue.enqueue("my.task", queue_name="q")

        cur = await queue._db.adapter.execute(
            "SELECT COUNT(*) FROM qler_job_deps"
        )
        row = await cur.fetchone()
        await cur.close()

        assert row[0] == 0


class TestArchivalCleansUpReverseMap:
    """Verify archive_jobs() removes dep entries for archived jobs."""

    async def test_archive_cleans_up_reverse_map(self, queue: Queue):
        """archive_jobs() removes dep entries for archived jobs."""
        parent = await queue.enqueue("my.task", queue_name="q")
        child = await queue.enqueue(
            "my.task", queue_name="q", depends_on=[parent.ulid]
        )

        # Complete the parent so it becomes archivable
        from sqler import F
        from qler.enums import JobStatus
        from qler._time import now_epoch
        from qler.models.job import Job

        now = now_epoch()
        await Job.query().filter(F("ulid") == parent.ulid).update_one(
            status=JobStatus.COMPLETED.value,
            finished_at=now - 600,  # older than default 300s threshold
            updated_at=now,
        )

        # Confirm dep row exists before archival
        cur = await queue._db.adapter.execute(
            "SELECT COUNT(*) FROM qler_job_deps"
        )
        row = await cur.fetchone()
        await cur.close()
        assert row[0] == 1

        # Archive
        archived = await queue.archive_jobs(older_than_seconds=300)
        assert archived == 1

        # Dep row should be cleaned up (parent was archived)
        cur = await queue._db.adapter.execute(
            "SELECT COUNT(*) FROM qler_job_deps"
        )
        row = await cur.fetchone()
        await cur.close()
        assert row[0] == 0
