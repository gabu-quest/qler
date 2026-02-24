"""Tests for M20 — Unique Jobs."""

import pytest
from sqler import F

from qler.enums import JobStatus
from qler.exceptions import ConfigurationError
from qler.models.job import Job
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
        count = await Job.query().filter(F("unique_key") == tw.task_path).count()
        assert count == 1

    async def test_unique_returns_existing_running(self, queue):
        tw = TaskWrapper(_unique_noop, queue, unique=True)
        job1 = await tw.enqueue()
        await Job.query().filter(F("ulid") == job1.ulid).update_one(
            status=JobStatus.RUNNING.value
        )
        job2 = await tw.enqueue()
        assert job1.ulid == job2.ulid
        assert job2.status == JobStatus.RUNNING.value
        count = await Job.query().filter(F("unique_key") == tw.task_path).count()
        assert count == 1

    async def test_unique_allows_after_completed(self, queue):
        tw = TaskWrapper(_unique_noop, queue, unique=True)
        job1 = await tw.enqueue()
        await Job.query().filter(F("ulid") == job1.ulid).update_one(
            status=JobStatus.COMPLETED.value
        )
        job2 = await tw.enqueue()
        assert job1.ulid != job2.ulid
        assert job2.status == JobStatus.PENDING.value

    async def test_unique_allows_after_failed(self, queue):
        tw = TaskWrapper(_unique_noop, queue, unique=True)
        job1 = await tw.enqueue()
        await Job.query().filter(F("ulid") == job1.ulid).update_one(
            status=JobStatus.FAILED.value
        )
        job2 = await tw.enqueue()
        assert job1.ulid != job2.ulid
        assert job2.status == JobStatus.PENDING.value

    async def test_unique_allows_after_cancelled(self, queue):
        tw = TaskWrapper(_unique_noop, queue, unique=True)
        job1 = await tw.enqueue()
        await Job.query().filter(F("ulid") == job1.ulid).update_one(
            status=JobStatus.CANCELLED.value
        )
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
            _keyed_noop, queue,
            unique_key=lambda order_id: f"sync:{order_id}",
        )
        job1 = await tw.enqueue("order-1")
        job2 = await tw.enqueue("order-2")
        assert job1.ulid != job2.ulid
        assert job1.unique_key == "sync:order-1"
        assert job2.unique_key == "sync:order-2"

    async def test_unique_key_same_key_returns_existing(self, queue):
        tw = TaskWrapper(
            _keyed_noop, queue,
            unique_key=lambda order_id: f"sync:{order_id}",
        )
        job1 = await tw.enqueue("order-1")
        job2 = await tw.enqueue("order-1")
        assert job1.ulid == job2.ulid
        count = await Job.query().filter(F("unique_key") == "sync:order-1").count()
        assert count == 1

    async def test_unique_key_allows_after_terminal(self, queue):
        """unique_key jobs can be re-enqueued after reaching terminal state."""
        tw = TaskWrapper(
            _keyed_noop, queue,
            unique_key=lambda order_id: f"sync:{order_id}",
        )
        job1 = await tw.enqueue("order-1")
        await Job.query().filter(F("ulid") == job1.ulid).update_one(
            status=JobStatus.COMPLETED.value
        )
        job2 = await tw.enqueue("order-1")
        assert job1.ulid != job2.ulid
        assert job2.status == JobStatus.PENDING.value
        assert job2.unique_key == "sync:order-1"

    async def test_unique_key_validates_callable(self, queue):
        with pytest.raises(ConfigurationError, match="unique_key must be callable"):
            task(queue, unique_key="not-callable")(_unique_noop)

    async def test_unique_key_validates_return_type(self, queue):
        tw = TaskWrapper(
            _keyed_noop, queue,
            unique_key=lambda order_id: 42,
        )
        with pytest.raises(TypeError, match="unique_key function must return str"):
            await tw.enqueue("order-1")

    async def test_unique_key_validates_return_type_in_batch(self, queue):
        tw = TaskWrapper(
            _keyed_noop, queue,
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
            _keyed_noop, queue,
            unique_key=lambda order_id: f"sync:{order_id}",
        )
        # Enqueue with both idempotency key and unique key
        job1 = await tw.enqueue("order-1", _idempotency_key="idem-1")
        # Complete the job so uniqueness wouldn't block
        await Job.query().filter(F("ulid") == job1.ulid).update_one(
            status=JobStatus.COMPLETED.value
        )
        # Re-enqueue with same idempotency key — should return existing (completed)
        # because idempotency checks all non-cancelled states
        job2 = await tw.enqueue("order-1", _idempotency_key="idem-1")
        assert job1.ulid == job2.ulid

    async def test_batch_respects_uniqueness_with_key_fn(self, queue):
        """enqueue_many with unique_key=fn checks uniqueness per-job."""
        tw = TaskWrapper(
            _keyed_noop, queue,
            unique_key=lambda order_id: f"sync:{order_id}",
        )
        # Create an existing job
        job1 = await tw.enqueue("order-1")
        # Batch enqueue: order-1 should return existing, order-2 should be new
        results = await tw.enqueue_many([
            {"args": ("order-1",)},
            {"args": ("order-2",)},
        ])
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
        count = await Job.query().filter(F("unique_key") == tw.task_path).count()
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
        reloaded = await Job.query().filter(F("ulid") == job.ulid).first()
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
