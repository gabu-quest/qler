"""Tests for M20 — Unique Jobs."""

import asyncio
import logging

import pytest
from sqler import F

from qler.enums import JobStatus
from qler.exceptions import ConfigurationError, UniqueKeyConflictError
from qler.models.job import Job
from qler.queue import Queue
from qler.task import TaskWrapper, task

# ---- helpers: module-level async functions for @task ----


async def _unique_noop():
    pass


async def _unique_noop2():
    pass


async def _keyed_noop(order_id: str):
    pass


# ======================================================================
# TestUniqueJobs — basic unique=True behavior
# ======================================================================


class TestUniqueJobs:
    """Test unique=True prevents duplicate active jobs."""

    async def test_unique_returns_existing_pending(self, queue):
        tw = TaskWrapper(_unique_noop, queue, unique=True)
        job1 = await tw.enqueue()
        job2 = await tw.enqueue()
        job3 = await tw.enqueue()
        assert job1.ulid == job2.ulid == job3.ulid
        assert job1.status == JobStatus.PENDING.value
        # DB-level proof: only one row exists
        count = await queue.jobs.filter(F("unique_key") == tw.task_path).count()
        assert count == 1

    async def test_unique_returns_existing_running(self, queue):
        tw = TaskWrapper(_unique_noop, queue, unique=True)
        job1 = await tw.enqueue()
        await queue.jobs.filter(F("ulid") == job1.ulid).update_one(status=JobStatus.RUNNING.value)
        job2 = await tw.enqueue()
        assert job1.ulid == job2.ulid
        assert job2.status == JobStatus.RUNNING.value
        count = await queue.jobs.filter(F("unique_key") == tw.task_path).count()
        assert count == 1

    async def test_unique_allows_after_completed(self, queue):
        tw = TaskWrapper(_unique_noop, queue, unique=True)
        job1 = await tw.enqueue()
        await queue.jobs.filter(F("ulid") == job1.ulid).update_one(status=JobStatus.COMPLETED.value)
        job2 = await tw.enqueue()
        assert job1.ulid != job2.ulid
        assert job2.status == JobStatus.PENDING.value

    async def test_unique_allows_after_failed(self, queue):
        tw = TaskWrapper(_unique_noop, queue, unique=True)
        job1 = await tw.enqueue()
        await queue.jobs.filter(F("ulid") == job1.ulid).update_one(status=JobStatus.FAILED.value)
        job2 = await tw.enqueue()
        assert job1.ulid != job2.ulid
        assert job2.status == JobStatus.PENDING.value

    async def test_unique_allows_after_cancelled(self, queue):
        tw = TaskWrapper(_unique_noop, queue, unique=True)
        job1 = await tw.enqueue()
        await queue.jobs.filter(F("ulid") == job1.ulid).update_one(status=JobStatus.CANCELLED.value)
        job2 = await tw.enqueue()
        assert job1.ulid != job2.ulid
        assert job2.status == JobStatus.PENDING.value

    async def test_unique_different_tasks_are_independent(self, queue):
        """Different task paths produce different unique_keys, so they don't conflict."""
        tw1 = TaskWrapper(_unique_noop, queue, unique=True)
        tw2 = TaskWrapper(_unique_noop2, queue, unique=True)
        job1 = await tw1.enqueue()
        job2 = await tw2.enqueue()
        assert job1.ulid != job2.ulid
        assert job1.unique_key != job2.unique_key


# ======================================================================
# TestUniqueKeyFunction — unique_key=fn behavior
# ======================================================================


class TestUniqueKeyFunction:
    """Test unique_key=fn provides scoped uniqueness."""

    async def test_unique_key_scoped_by_key(self, queue):
        tw = TaskWrapper(
            _keyed_noop,
            queue,
            unique_key=lambda order_id: f"sync:{order_id}",
        )
        job1 = await tw.enqueue("order-1")
        job2 = await tw.enqueue("order-2")
        assert job1.ulid != job2.ulid
        assert job1.unique_key == "sync:order-1"
        assert job2.unique_key == "sync:order-2"

    async def test_unique_key_same_key_returns_existing(self, queue):
        tw = TaskWrapper(
            _keyed_noop,
            queue,
            unique_key=lambda order_id: f"sync:{order_id}",
        )
        job1 = await tw.enqueue("order-1")
        job2 = await tw.enqueue("order-1")
        assert job1.ulid == job2.ulid
        count = await queue.jobs.filter(F("unique_key") == "sync:order-1").count()
        assert count == 1

    async def test_unique_key_allows_after_terminal(self, queue):
        """unique_key jobs can be re-enqueued after reaching terminal state."""
        tw = TaskWrapper(
            _keyed_noop,
            queue,
            unique_key=lambda order_id: f"sync:{order_id}",
        )
        job1 = await tw.enqueue("order-1")
        await queue.jobs.filter(F("ulid") == job1.ulid).update_one(status=JobStatus.COMPLETED.value)
        job2 = await tw.enqueue("order-1")
        assert job1.ulid != job2.ulid
        assert job2.status == JobStatus.PENDING.value
        assert job2.unique_key == "sync:order-1"

    async def test_unique_key_validates_callable(self, queue):
        with pytest.raises(ConfigurationError, match="unique_key must be callable"):
            task(queue, unique_key="not-callable")(_unique_noop)

    async def test_unique_key_validates_return_type(self, queue):
        tw = TaskWrapper(
            _keyed_noop,
            queue,
            unique_key=lambda order_id: 42,
        )
        with pytest.raises(TypeError, match="unique_key function must return str"):
            await tw.enqueue("order-1")

    async def test_unique_key_validates_return_type_in_batch(self, queue):
        tw = TaskWrapper(
            _keyed_noop,
            queue,
            unique_key=lambda order_id: 42,
        )
        with pytest.raises(TypeError, match="unique_key function must return str"):
            await tw.enqueue_many([{"args": ("order-1",)}])


# ======================================================================
# TestUniqueWithOtherFeatures — interaction with idempotency & batch
# ======================================================================


class TestUniqueWithOtherFeatures:
    """Test uniqueness interacts correctly with idempotency and batch enqueue."""

    async def test_unique_with_idempotency_precedence(self, queue):
        """Idempotency check runs first and takes precedence."""
        tw = TaskWrapper(
            _keyed_noop,
            queue,
            unique_key=lambda order_id: f"sync:{order_id}",
        )
        # Enqueue with both idempotency key and unique key
        job1 = await tw.enqueue("order-1", _idempotency_key="idem-1")
        # Complete the job so uniqueness wouldn't block
        await queue.jobs.filter(F("ulid") == job1.ulid).update_one(status=JobStatus.COMPLETED.value)
        # Re-enqueue with same idempotency key — should return existing (completed)
        # because idempotency checks all non-cancelled states
        job2 = await tw.enqueue("order-1", _idempotency_key="idem-1")
        assert job1.ulid == job2.ulid

    async def test_batch_respects_uniqueness_with_key_fn(self, queue):
        """enqueue_many with unique_key=fn checks uniqueness per-job."""
        tw = TaskWrapper(
            _keyed_noop,
            queue,
            unique_key=lambda order_id: f"sync:{order_id}",
        )
        # Create an existing job
        job1 = await tw.enqueue("order-1")
        # Batch enqueue: order-1 should return existing, order-2 should be new
        results = await tw.enqueue_many(
            [
                {"args": ("order-1",)},
                {"args": ("order-2",)},
            ]
        )
        assert len(results) == 2
        assert results[0].ulid == job1.ulid  # existing
        assert results[1].ulid != job1.ulid  # new
        assert results[1].unique_key == "sync:order-2"

    async def test_batch_respects_uniqueness_with_unique_true(self, queue):
        """enqueue_many with unique=True deduplicates all items to the same job."""
        tw = TaskWrapper(_unique_noop, queue, unique=True)
        job1 = await tw.enqueue()
        results = await tw.enqueue_many([{}, {}])
        assert len(results) == 2
        assert results[0].ulid == job1.ulid
        assert results[1].ulid == job1.ulid
        count = await queue.jobs.filter(F("unique_key") == tw.task_path).count()
        assert count == 1


# ======================================================================
# TestUniqueConfig — configuration validation
# ======================================================================


class TestUniqueConfig:
    """Test unique configuration validation."""

    async def test_unique_and_unique_key_mutually_exclusive(self, queue):
        with pytest.raises(
            ConfigurationError,
            match="unique and unique_key are mutually exclusive",
        ):
            task(queue, unique=True, unique_key=lambda: "key")(_unique_noop)

    async def test_unique_key_stored_on_job(self, queue):
        """Verify unique_key is persisted on the Job model."""
        tw = TaskWrapper(_unique_noop, queue, unique=True)
        job = await tw.enqueue()
        assert job.unique_key == tw.task_path

        # Reload from DB
        reloaded = await queue.jobs.filter(F("ulid") == job.ulid).first()
        assert reloaded is not None
        assert reloaded.ulid == job.ulid
        assert reloaded.unique_key == tw.task_path
        assert reloaded.status == JobStatus.PENDING.value

    async def test_per_call_unique_key_override(self, queue):
        """_unique_key param on enqueue() overrides decorator config."""
        tw = TaskWrapper(_unique_noop, queue, unique=True)
        job1 = await tw.enqueue(_unique_key="custom-key-1")
        job2 = await tw.enqueue(_unique_key="custom-key-2")
        assert job1.ulid != job2.ulid
        assert job1.unique_key == "custom-key-1"
        assert job2.unique_key == "custom-key-2"

        # Same key returns existing
        job3 = await tw.enqueue(_unique_key="custom-key-1")
        assert job3.ulid == job1.ulid

    async def test_non_unique_tasks_allow_duplicates(self, queue):
        """Tasks without unique=True freely enqueue duplicates."""
        tw = TaskWrapper(_unique_noop, queue)
        job1 = await tw.enqueue()
        job2 = await tw.enqueue()
        assert job1.ulid != job2.ulid
        assert job1.unique_key is None
        assert job2.unique_key is None


# ======================================================================
# TestUniqueKeyMakesOneJob — the key holds when enqueues run at once
# ======================================================================


async def _unique_rows(queue: Queue, key: str) -> int:
    return await queue.jobs.filter(F("unique_key") == key).count()


async def _failed_job_with_key(q, key: str) -> Job:
    """A job with ``key`` that a worker claimed and failed for good."""
    job = await q.enqueue("t", unique_key=key)
    claimed = await q.claim_job("w1", ["default"])
    assert claimed is not None
    assert claimed.ulid == job.ulid
    await q.fail_job(claimed, "w1", RuntimeError("boom"))
    await job.refresh()
    assert job.status == JobStatus.FAILED.value
    return job


class TestUniqueKeyMakesOneJob:
    """A unique key makes one active job, also when enqueues run at the same time."""

    async def test_eight_concurrent_enqueues_make_one_job(self, disk_queue):
        jobs = await asyncio.gather(*[disk_queue.enqueue("t", unique_key="u1") for _ in range(8)])
        assert len({j.ulid for j in jobs}) == 1
        assert await _unique_rows(disk_queue, "u1") == 1

    async def test_two_queues_on_one_file_make_one_job(self, tmp_path):
        path = str(tmp_path / "two.db")
        q1, q2 = Queue(path), Queue(path)
        await q1.init_db()
        await q2.init_db()
        try:
            jobs = await asyncio.gather(*[q.enqueue("t", unique_key="u2") for q in (q1, q2) * 4])
            assert len({j.ulid for j in jobs}) == 1
            assert await Job.using(q1.db, "qler_jobs").filter(F("unique_key") == "u2").count() == 1
        finally:
            await q1.close()
            await q2.close()

    async def test_two_enqueue_many_with_overlapping_keys(self, disk_queue):
        first = [{"task_path": "t", "unique_key": f"m{i}"} for i in range(0, 10)]
        second = [{"task_path": "t", "unique_key": f"m{i}"} for i in range(5, 15)]
        a, b = await asyncio.gather(disk_queue.enqueue_many(first), disk_queue.enqueue_many(second))
        for i in range(15):
            assert await _unique_rows(disk_queue, f"m{i}") == 1
        assert [j.ulid for j in a[5:]] == [j.ulid for j in b[:5]]

    async def test_enqueue_and_enqueue_many_at_once(self, disk_queue):
        one, many = await asyncio.gather(
            disk_queue.enqueue("t", unique_key="mix"),
            disk_queue.enqueue_many([{"task_path": "t", "unique_key": "mix"}]),
        )
        assert await _unique_rows(disk_queue, "mix") == 1
        assert many[0].ulid == one.ulid

    async def test_completed_key_can_be_used_again_with_index(self, disk_queue):
        job1 = await disk_queue.enqueue("t", unique_key="again")
        claimed = await disk_queue.claim_job("w1", ["default"])
        assert claimed is not None
        assert claimed.ulid == job1.ulid
        await disk_queue.complete_job(claimed, "w1", result=None)
        job2 = await disk_queue.enqueue("t", unique_key="again")
        assert job2.ulid != job1.ulid
        assert await _unique_rows(disk_queue, "again") == 2
        names = {i["name"] for i in await disk_queue.db.list_indexes("qler_jobs")}
        assert "idx_qler_jobs_unique_uq" in names

    async def test_start_with_duplicate_keys(self, tmp_path, caplog):
        path = str(tmp_path / "dups.db")
        old = Queue(path)
        await old.init_db()
        # An older qler had no unique index: drop it and write two active jobs with one key.
        await old.db.drop_index("idx_qler_jobs_unique_uq")
        first = await old.enqueue("t", unique_key="dup-u")
        second = Job(
            ulid="01ZZZZZZZZZZZZZZZZZZZZZZZZ",
            task="t",
            unique_key="dup-u",
            created_at=first.created_at,
            updated_at=first.created_at,
        )
        await second.save(db=old.db)
        await old.close()

        caplog.set_level(logging.ERROR, logger="qler.queue")
        q = Queue(path)
        await q.init_db()
        try:
            errors = [r for r in caplog.records if r.levelno == logging.ERROR]
            assert len(errors) == 1
            assert "unique" in errors[0].getMessage()
            assert "dup-u" in errors[0].getMessage()
            names = {i["name"] for i in await q.db.list_indexes("qler_jobs")}
            assert "idx_qler_jobs_unique_uq" not in names
            assert "idx_qler_jobs_unique" in names

            again = await q.enqueue("t", unique_key="dup-u")
            assert again.ulid == first.ulid
            assert await _unique_rows(q, "dup-u") == 2

            assert await q.cancel_job(second) is True
        finally:
            await q.close()

        q = Queue(path)
        await q.init_db()
        try:
            indexes = {i["name"]: i for i in await q.db.list_indexes("qler_jobs")}
            # sqler's "unique" flag reads UNIQUE anywhere in the text: check the head.
            assert indexes["idx_qler_jobs_unique_uq"]["sql"].startswith("CREATE UNIQUE INDEX ")
            assert "idx_qler_jobs_unique" not in indexes
        finally:
            await q.close()

    async def test_error_lists_twenty_keys_and_the_rest(self, tmp_path, caplog):
        path = str(tmp_path / "many.db")
        old = Queue(path)
        await old.init_db()
        await old.db.drop_index("idx_qler_jobs_unique_uq")
        for i in range(23):
            for n in range(2):
                await Job(ulid=f"01H{i:02d}{n}" + "0" * 20, task="t", unique_key=f"k{i:02d}").save(
                    db=old.db
                )
        await old.close()

        caplog.set_level(logging.ERROR, logger="qler.queue")
        q = Queue(path)
        await q.init_db()
        await q.close()
        errors = [r for r in caplog.records if r.levelno == logging.ERROR]
        assert len(errors) == 1
        message = errors[0].getMessage()
        assert "23 unique keys" in message
        assert ", ".join(f"k{i:02d}" for i in range(20)) in message
        assert "k20" not in message
        assert "and 3 more" in message

    async def test_retry_into_an_active_key_raises(self, disk_queue):
        failed = await _failed_job_with_key(disk_queue, "busy")
        active = await disk_queue.enqueue("t", unique_key="busy")
        assert active.ulid != failed.ulid
        with pytest.raises(UniqueKeyConflictError, match="busy") as info:
            await failed.retry()
        assert info.value.unique_key == "busy"
        assert info.value.active_ulid == active.ulid
        assert active.ulid in info.value.message
        row = await disk_queue.jobs.filter(F("ulid") == failed.ulid).first()
        assert row is not None
        assert row.status == JobStatus.FAILED.value

    async def test_replay_into_an_active_key_raises(self, disk_queue):
        failed = await _failed_job_with_key(disk_queue, "busy2")
        active = await disk_queue.enqueue("t", unique_key="busy2")
        with pytest.raises(UniqueKeyConflictError, match="busy2") as info:
            await disk_queue.replay_job(failed)
        assert info.value.unique_key == "busy2"
        assert info.value.active_ulid == active.ulid
        row = await disk_queue.jobs.filter(F("ulid") == failed.ulid).first()
        assert row is not None
        assert row.status == JobStatus.FAILED.value


# ======================================================================
# TestUniqueKeyInOneBatch — one batch, one unique key, one job
# ======================================================================


class TestUniqueKeyInOneBatch:
    """Two specs of one enqueue_many call with one unique key are one job."""

    async def test_two_specs_one_key(self, queue):
        a, b = await queue.enqueue_many(
            [
                {"task_path": "t", "unique_key": "k1"},
                {"task_path": "t", "unique_key": "k1"},
            ]
        )
        assert a.ulid == b.ulid
        assert await _unique_rows(queue, "k1") == 1

    async def test_keys_a_b_a(self, queue):
        res = await queue.enqueue_many(
            [
                {"task_path": "t", "unique_key": "a"},
                {"task_path": "t", "unique_key": "b"},
                {"task_path": "t", "unique_key": "a"},
            ]
        )
        assert len(res) == 3
        assert res[0].ulid == res[2].ulid
        assert res[0].ulid != res[1].ulid
        assert await _unique_rows(queue, "a") == 1
        assert await _unique_rows(queue, "b") == 1
        assert await queue.jobs.count() == 2

    async def test_task_wrapper_unique_true(self, queue):
        tw = TaskWrapper(_unique_noop, queue, unique=True)
        res = await tw.enqueue_many([{}, {}])
        assert len(res) == 2
        assert res[0].ulid == res[1].ulid
        assert await _unique_rows(queue, tw.task_path) == 1

    async def test_both_keys_repeated(self, queue):
        spec = {"task_path": "t", "idempotency_key": "i1", "unique_key": "u1"}
        a, b = await queue.enqueue_many([dict(spec), dict(spec)])
        assert a.ulid == b.ulid
        assert await _unique_rows(queue, "u1") == 1
        assert await queue.jobs.count() == 1


class TestKeyIndexText:
    """A database made by this code is recognised by the text of its key indexes."""

    async def test_unique_index_where_text_is_unchanged(self, tmp_path):
        q = Queue(str(tmp_path / "text.db"))
        await q.init_db()
        try:
            indexes = {i["name"]: i["sql"] for i in await q.db.list_indexes("qler_jobs")}
        finally:
            await q.close()
        assert indexes["idx_qler_jobs_idempotency_uq"].endswith(
            " WHERE json_extract(data, '$.idempotency_key') IS NOT NULL AND status != 'cancelled'"
        )
        assert indexes["idx_qler_jobs_unique_uq"].endswith(
            " WHERE json_extract(data, '$.unique_key') IS NOT NULL "
            "AND status IN ('pending', 'running')"
        )
