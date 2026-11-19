"""Tests for the reverse dependency index (qler_job_dependencies table)."""

import pytest
from sqler import F

from qler.models.dependency import JobDependency
from qler.queue import Queue

pytestmark = pytest.mark.asyncio


class TestDepTable:
    """Verify qler_job_dependencies table and index are created by init_db()."""

    async def test_dep_table_created(self, queue: Queue):
        """qler_job_dependencies table exists after init_db(); the old table is not made."""
        rows = await queue.db.execute_sql(
            "SELECT name FROM sqlite_master WHERE type='table' "
            "AND name IN ('qler_job_dependencies', 'qler_job_deps')"
        )
        assert rows == [{"name": "qler_job_dependencies"}]

    async def test_dep_index_created(self, queue: Queue):
        """The UNIQUE index over (parent_ulid, child_ulid) exists after init_db()."""
        indexes = {i["name"]: i for i in await queue.db.list_indexes("qler_job_dependencies")}
        assert "idx_qler_job_dependencies_edge" in indexes
        assert indexes["idx_qler_job_dependencies_edge"]["unique"] is True


class TestReverseMapPopulation:
    """Verify enqueue/enqueue_many populate the reverse map."""

    async def test_reverse_map_populated_on_enqueue(self, queue: Queue):
        """enqueue() with depends_on creates (parent, child) rows."""
        parent = await queue.enqueue("my.task", queue_name="q")
        child = await queue.enqueue("my.task", queue_name="q", depends_on=[parent.ulid])

        edges = await JobDependency.using(queue.db).all()

        assert len(edges) == 1
        assert edges[0].parent_ulid == parent.ulid
        assert edges[0].child_ulid == child.ulid

    async def test_reverse_map_multiple_parents(self, queue: Queue):
        """A child depending on two parents creates two rows."""
        p1 = await queue.enqueue("my.task", queue_name="q")
        p2 = await queue.enqueue("my.task", queue_name="q")
        child = await queue.enqueue("my.task", queue_name="q", depends_on=[p1.ulid, p2.ulid])

        parent_ulids = (
            await JobDependency.using(queue.db)
            .filter(F("child_ulid") == child.ulid)
            .distinct_values("parent_ulid")
        )

        assert len(parent_ulids) == 2
        assert set(parent_ulids) == {p1.ulid, p2.ulid}

    async def test_reverse_map_populated_on_enqueue_many(self, queue: Queue):
        """enqueue_many() with deps creates reverse map rows."""
        parent = await queue.enqueue("my.task", queue_name="q")
        jobs = await queue.enqueue_many(
            [
                {"task_path": "my.task", "queue_name": "q", "depends_on": [parent.ulid]},
                {"task_path": "my.task", "queue_name": "q", "depends_on": [parent.ulid]},
            ]
        )

        child_ulids = (
            await JobDependency.using(queue.db)
            .filter(F("parent_ulid") == parent.ulid)
            .distinct_values("child_ulid")
        )

        assert len(child_ulids) == 2
        assert set(child_ulids) == {jobs[0].ulid, jobs[1].ulid}

    async def test_reverse_map_no_rows_without_deps(self, queue: Queue):
        """enqueue() without depends_on creates no reverse map rows."""
        await queue.enqueue("my.task", queue_name="q")

        assert await JobDependency.using(queue.db).count() == 0


class TestArchivalCleansUpReverseMap:
    """Verify archive_jobs() removes dep entries for archived jobs."""

    async def test_archive_cleans_up_reverse_map(self, queue: Queue):
        """archive_jobs() removes dep entries for archived jobs."""
        parent = await queue.enqueue("my.task", queue_name="q")
        child = await queue.enqueue("my.task", queue_name="q", depends_on=[parent.ulid])

        # Complete the parent so it becomes archivable
        from sqler import F

        from qler._time import now_epoch
        from qler.enums import JobStatus

        now = now_epoch()
        await queue.jobs.filter(F("ulid") == parent.ulid).update_one(
            status=JobStatus.COMPLETED.value,
            finished_at=now - 600,  # older than default 300s threshold
            updated_at=now,
        )

        # Confirm dep row exists before archival
        assert await JobDependency.using(queue.db).count() == 1

        # Archive
        archived = await queue.archive_jobs(older_than_seconds=300)
        assert archived == 1

        # Dep row should be cleaned up (parent was archived)
        assert await JobDependency.using(queue.db).count() == 0
