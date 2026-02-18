"""Tests for M9 — Job Dependencies/Chaining."""

import pytest
import pytest_asyncio
from sqler import AsyncSQLerDB, F

from qler._time import now_epoch
from qler.enums import FailureKind, JobStatus
from qler.exceptions import DependencyError, JobCancelledError, JobFailedError
from qler.models.job import Job
from qler.queue import Queue
from qler.task import task


# ---------------------------------------------------------------------------
# Module-level task functions (required: @task rejects nested functions)
# ---------------------------------------------------------------------------


async def noop_task():
    pass


async def add_task(a, b):
    return a + b


async def boom_task():
    raise ValueError("kaboom")


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def queue(db):
    """Initialized Queue bound to the in-memory database."""
    q = Queue(db)
    await q.init_db()
    yield q


@pytest_asyncio.fixture
async def imm_queue(db):
    """Queue in immediate mode for inline execution."""
    q = Queue(db, immediate=True)
    await q.init_db()
    task(q)(noop_task)
    task(q)(add_task)
    task(q)(boom_task)
    yield q


# ---------------------------------------------------------------------------
# TestEnqueueWithDependencies
# ---------------------------------------------------------------------------


class TestEnqueueWithDependencies:
    """Enqueue with depends_on parameter."""

    async def test_single_dependency(self, queue):
        """Job B depends on job A; B gets pending_dep_count=1."""
        a = await queue.enqueue("my.task", queue_name="default")
        b = await queue.enqueue("my.task", depends_on=[a.ulid])

        assert b.dependencies == [a.ulid]
        assert b.pending_dep_count == 1

    async def test_multiple_dependencies(self, queue):
        """Job C depends on A and B; pending_dep_count=2."""
        a = await queue.enqueue("my.task")
        b = await queue.enqueue("my.task")
        c = await queue.enqueue("my.task", depends_on=[a.ulid, b.ulid])

        assert c.dependencies == [a.ulid, b.ulid]
        assert c.pending_dep_count == 2

    async def test_completed_dependency_count_zero(self, queue):
        """If dep is already COMPLETED, pending_dep_count=0."""
        a = await queue.enqueue("my.task")
        # Manually complete A
        await Job.query().filter(F("ulid") == a.ulid).update_one(
            status=JobStatus.COMPLETED.value,
            finished_at=now_epoch(),
            updated_at=now_epoch(),
        )
        b = await queue.enqueue("my.task", depends_on=[a.ulid])

        assert b.dependencies == [a.ulid]
        assert b.pending_dep_count == 0

    async def test_mixed_statuses(self, queue):
        """One COMPLETED + one PENDING = pending_dep_count=1."""
        a = await queue.enqueue("my.task")
        b = await queue.enqueue("my.task")
        # Complete A
        await Job.query().filter(F("ulid") == a.ulid).update_one(
            status=JobStatus.COMPLETED.value,
            finished_at=now_epoch(),
            updated_at=now_epoch(),
        )
        c = await queue.enqueue("my.task", depends_on=[a.ulid, b.ulid])

        assert c.pending_dep_count == 1

    async def test_rejects_missing_dependency(self, queue):
        """Enqueue raises DependencyError for a nonexistent dep ULID."""
        with pytest.raises(DependencyError, match="not found"):
            await queue.enqueue("my.task", depends_on=["01NONEXISTENT00000000000000"])

    async def test_rejects_failed_dependency(self, queue):
        """Enqueue raises DependencyError if dep is FAILED."""
        a = await queue.enqueue("my.task")
        await Job.query().filter(F("ulid") == a.ulid).update_one(
            status=JobStatus.FAILED.value,
            finished_at=now_epoch(),
            updated_at=now_epoch(),
        )
        with pytest.raises(DependencyError, match="is failed"):
            await queue.enqueue("my.task", depends_on=[a.ulid])

    async def test_rejects_cancelled_dependency(self, queue):
        """Enqueue raises DependencyError if dep is CANCELLED."""
        a = await queue.enqueue("my.task")
        await Job.query().filter(F("ulid") == a.ulid).update_one(
            status=JobStatus.CANCELLED.value,
            finished_at=now_epoch(),
            updated_at=now_epoch(),
        )
        with pytest.raises(DependencyError, match="is cancelled"):
            await queue.enqueue("my.task", depends_on=[a.ulid])

    async def test_deduplicates_dependency_ulids(self, queue):
        """Duplicate ULIDs in depends_on are deduplicated."""
        a = await queue.enqueue("my.task")
        b = await queue.enqueue("my.task", depends_on=[a.ulid, a.ulid, a.ulid])

        assert b.dependencies == [a.ulid]
        assert b.pending_dep_count == 1

    async def test_no_deps_default(self, queue):
        """Without depends_on, dependencies=[] and pending_dep_count=0."""
        a = await queue.enqueue("my.task")

        assert a.dependencies == []
        assert a.pending_dep_count == 0


# ---------------------------------------------------------------------------
# TestClaimWithDependencies
# ---------------------------------------------------------------------------


class TestClaimWithDependencies:
    """claim_job() respects pending_dep_count."""

    async def test_skips_blocked_jobs(self, queue):
        """Jobs with pending_dep_count > 0 are not claimable."""
        a = await queue.enqueue("my.task")
        b = await queue.enqueue("my.task", depends_on=[a.ulid])

        claimed = await queue.claim_job("worker-1", ["default"])
        # Should claim A (unblocked), not B
        assert claimed is not None
        assert claimed.ulid == a.ulid

    async def test_claims_unblocked_job(self, queue):
        """Jobs with pending_dep_count=0 are claimable."""
        a = await queue.enqueue("my.task")
        # Complete A
        await Job.query().filter(F("ulid") == a.ulid).update_one(
            status=JobStatus.COMPLETED.value,
            finished_at=now_epoch(),
            updated_at=now_epoch(),
        )
        b = await queue.enqueue("my.task", depends_on=[a.ulid])

        assert b.pending_dep_count == 0
        claimed = await queue.claim_job("worker-1", ["default"])
        assert claimed is not None
        assert claimed.ulid == b.ulid

    async def test_prefers_claimable_over_blocked(self, queue):
        """When both blocked and unblocked jobs exist, claim the unblocked one."""
        a = await queue.enqueue("my.task")
        b = await queue.enqueue("my.task", depends_on=[a.ulid])
        c = await queue.enqueue("my.task")  # No deps, claimable

        # Claim first — should be A or C (both claimable), by priority/eta/ulid order
        claimed1 = await queue.claim_job("worker-1", ["default"])
        assert claimed1 is not None
        assert claimed1.ulid in {a.ulid, c.ulid}

        claimed2 = await queue.claim_job("worker-2", ["default"])
        assert claimed2 is not None
        assert claimed2.ulid in {a.ulid, c.ulid}
        assert claimed2.ulid != claimed1.ulid

        # B should still be blocked
        claimed3 = await queue.claim_job("worker-3", ["default"])
        assert claimed3 is None

    async def test_returns_none_when_all_blocked(self, queue):
        """Returns None when only blocked jobs are pending."""
        a = await queue.enqueue("my.task")
        b = await queue.enqueue("my.task", depends_on=[a.ulid])
        # Claim A to make it RUNNING
        await queue.claim_job("worker-1", ["default"])

        # Only B is pending, but it's blocked
        claimed = await queue.claim_job("worker-2", ["default"])
        assert claimed is None


# ---------------------------------------------------------------------------
# TestDependencyResolution
# ---------------------------------------------------------------------------


class TestDependencyResolution:
    """_resolve_dependencies decrements pending_dep_count on completion."""

    async def test_complete_decrements_count(self, queue):
        """Completing A decrements B's pending_dep_count."""
        a = await queue.enqueue("my.task")
        b = await queue.enqueue("my.task", depends_on=[a.ulid])
        assert b.pending_dep_count == 1

        # Claim and complete A
        claimed_a = await queue.claim_job("worker-1", ["default"])
        assert claimed_a.ulid == a.ulid
        await queue.complete_job(claimed_a, "worker-1")

        # Check B's pending_dep_count
        refreshed_b = await Job.query().filter(F("ulid") == b.ulid).first()
        assert refreshed_b.pending_dep_count == 0

    async def test_last_dep_makes_claimable(self, queue):
        """After all deps complete, the job becomes claimable."""
        a = await queue.enqueue("my.task")
        b = await queue.enqueue("my.task")
        c = await queue.enqueue("my.task", depends_on=[a.ulid, b.ulid])
        assert c.pending_dep_count == 2

        # Complete A
        claimed_a = await queue.claim_job("worker-1", ["default"])
        await queue.complete_job(claimed_a, "worker-1")

        refreshed_c = await Job.query().filter(F("ulid") == c.ulid).first()
        assert refreshed_c.pending_dep_count == 1

        # C still not claimable
        # Claim B (it's the next unblocked one)
        claimed_b = await queue.claim_job("worker-1", ["default"])
        assert claimed_b.ulid == b.ulid
        await queue.complete_job(claimed_b, "worker-1")

        refreshed_c = await Job.query().filter(F("ulid") == c.ulid).first()
        assert refreshed_c.pending_dep_count == 0

        # Now C is claimable
        claimed_c = await queue.claim_job("worker-1", ["default"])
        assert claimed_c is not None
        assert claimed_c.ulid == c.ulid

    async def test_chain_a_b_c(self, queue):
        """A→B→C chain: complete A, B becomes claimable, complete B, C becomes claimable."""
        a = await queue.enqueue("my.task")
        b = await queue.enqueue("my.task", depends_on=[a.ulid])
        c = await queue.enqueue("my.task", depends_on=[b.ulid])

        assert b.pending_dep_count == 1
        assert c.pending_dep_count == 1

        # Only A is claimable
        claimed = await queue.claim_job("worker-1", ["default"])
        assert claimed.ulid == a.ulid
        await queue.complete_job(claimed, "worker-1")

        # Now B is claimable
        claimed = await queue.claim_job("worker-1", ["default"])
        assert claimed.ulid == b.ulid
        await queue.complete_job(claimed, "worker-1")

        # Now C is claimable
        claimed = await queue.claim_job("worker-1", ["default"])
        assert claimed.ulid == c.ulid

    async def test_multiple_dependents(self, queue):
        """A completes, both B and C (which depend on A) get decremented."""
        a = await queue.enqueue("my.task")
        b = await queue.enqueue("my.task", depends_on=[a.ulid])
        c = await queue.enqueue("my.task", depends_on=[a.ulid])

        claimed_a = await queue.claim_job("worker-1", ["default"])
        await queue.complete_job(claimed_a, "worker-1")

        refreshed_b = await Job.query().filter(F("ulid") == b.ulid).first()
        refreshed_c = await Job.query().filter(F("ulid") == c.ulid).first()
        assert refreshed_b.pending_dep_count == 0
        assert refreshed_c.pending_dep_count == 0

    async def test_idempotent_resolution(self, queue):
        """Calling _resolve_dependencies twice doesn't double-decrement."""
        a = await queue.enqueue("my.task")
        b = await queue.enqueue("my.task", depends_on=[a.ulid])

        await queue._resolve_dependencies(a.ulid)
        refreshed = await Job.query().filter(F("ulid") == b.ulid).first()
        assert refreshed.pending_dep_count == 0

        # Second call — count is already 0, pending_dep_count > 0 guard prevents decrement
        await queue._resolve_dependencies(a.ulid)
        refreshed = await Job.query().filter(F("ulid") == b.ulid).first()
        assert refreshed.pending_dep_count == 0


# ---------------------------------------------------------------------------
# TestCascadeCancel
# ---------------------------------------------------------------------------


class TestCascadeCancel:
    """Cascade cancellation on failure."""

    async def test_fail_cascades_to_dependent(self, queue):
        """When A fails terminally, B is cascade-cancelled."""
        a = await queue.enqueue("my.task")
        b = await queue.enqueue("my.task", depends_on=[a.ulid])

        claimed_a = await queue.claim_job("worker-1", ["default"])
        await queue.fail_job(claimed_a, "worker-1", ValueError("boom"))

        refreshed_b = await Job.query().filter(F("ulid") == b.ulid).first()
        assert refreshed_b.status == JobStatus.CANCELLED.value
        assert "failed/cancelled" in refreshed_b.last_error

    async def test_recursive_cascade(self, queue):
        """A→B→C: fail A, both B and C are cascade-cancelled."""
        a = await queue.enqueue("my.task")
        b = await queue.enqueue("my.task", depends_on=[a.ulid])
        c = await queue.enqueue("my.task", depends_on=[b.ulid])

        claimed_a = await queue.claim_job("worker-1", ["default"])
        await queue.fail_job(claimed_a, "worker-1", ValueError("boom"))

        refreshed_b = await Job.query().filter(F("ulid") == b.ulid).first()
        refreshed_c = await Job.query().filter(F("ulid") == c.ulid).first()
        assert refreshed_b.status == JobStatus.CANCELLED.value
        assert refreshed_c.status == JobStatus.CANCELLED.value

    async def test_only_affects_pending(self, queue):
        """Cascade cancel only cancels PENDING jobs, not RUNNING or COMPLETED."""
        a = await queue.enqueue("my.task")
        b = await queue.enqueue("my.task")
        c = await queue.enqueue("my.task", depends_on=[a.ulid])

        # B is independent (no deps on A), leave it as PENDING
        # Complete B to make it COMPLETED
        await Job.query().filter(F("ulid") == b.ulid).update_one(
            status=JobStatus.COMPLETED.value,
            finished_at=now_epoch(),
            updated_at=now_epoch(),
        )

        # Fail A
        claimed_a = await queue.claim_job("worker-1", ["default"])
        await queue.fail_job(claimed_a, "worker-1", ValueError("boom"))

        # C should be cancelled (depends on A)
        refreshed_c = await Job.query().filter(F("ulid") == c.ulid).first()
        assert refreshed_c.status == JobStatus.CANCELLED.value

        # B should remain COMPLETED (not affected)
        refreshed_b = await Job.query().filter(F("ulid") == b.ulid).first()
        assert refreshed_b.status == JobStatus.COMPLETED.value

    async def test_sets_error_message(self, queue):
        """Cascade-cancelled jobs have a descriptive last_error."""
        a = await queue.enqueue("my.task")
        b = await queue.enqueue("my.task", depends_on=[a.ulid])

        claimed_a = await queue.claim_job("worker-1", ["default"])
        await queue.fail_job(claimed_a, "worker-1", ValueError("boom"))

        refreshed_b = await Job.query().filter(F("ulid") == b.ulid).first()
        assert a.ulid in refreshed_b.last_error

    async def test_cancel_via_queue_cascades(self, queue):
        """Queue.cancel_job() cascades to dependents."""
        a = await queue.enqueue("my.task")
        b = await queue.enqueue("my.task", depends_on=[a.ulid])

        ok = await queue.cancel_job(a)
        assert ok is True

        refreshed_a = await Job.query().filter(F("ulid") == a.ulid).first()
        refreshed_b = await Job.query().filter(F("ulid") == b.ulid).first()
        assert refreshed_a.status == JobStatus.CANCELLED.value
        assert refreshed_b.status == JobStatus.CANCELLED.value

    async def test_retry_does_not_cascade(self, queue):
        """A retryable failure (max_retries>0) does NOT cascade-cancel."""
        a = await queue.enqueue("my.task", max_retries=3)
        b = await queue.enqueue("my.task", depends_on=[a.ulid])

        claimed_a = await queue.claim_job("worker-1", ["default"])
        await queue.fail_job(claimed_a, "worker-1", ValueError("transient"))

        # A should be back to PENDING (retrying), B still PENDING (not cancelled)
        refreshed_a = await Job.query().filter(F("ulid") == a.ulid).first()
        refreshed_b = await Job.query().filter(F("ulid") == b.ulid).first()
        assert refreshed_a.status == JobStatus.PENDING.value
        assert refreshed_b.status == JobStatus.PENDING.value


# ---------------------------------------------------------------------------
# TestWaitForDependencies
# ---------------------------------------------------------------------------


class TestWaitForDependencies:
    """Job.wait_for_dependencies() polling."""

    async def test_no_deps_returns_immediately(self, queue):
        """wait_for_dependencies returns immediately when no deps."""
        a = await queue.enqueue("my.task")
        await a.wait_for_dependencies(timeout=1.0)
        # No error = success

    async def test_waits_for_completion(self, queue):
        """wait_for_dependencies returns when all deps complete."""
        a = await queue.enqueue("my.task")
        b = await queue.enqueue("my.task", depends_on=[a.ulid])

        # Complete A
        await Job.query().filter(F("ulid") == a.ulid).update_one(
            status=JobStatus.COMPLETED.value,
            finished_at=now_epoch(),
            updated_at=now_epoch(),
        )

        await b.wait_for_dependencies(timeout=2.0)
        # No error = success

    async def test_raises_on_failed_dep(self, queue):
        """wait_for_dependencies raises JobFailedError if a dep fails."""
        a = await queue.enqueue("my.task")
        b = await queue.enqueue("my.task", depends_on=[a.ulid])

        # Fail A
        await Job.query().filter(F("ulid") == a.ulid).update_one(
            status=JobStatus.FAILED.value,
            last_error="boom",
            finished_at=now_epoch(),
            updated_at=now_epoch(),
        )

        with pytest.raises(JobFailedError, match="failed"):
            await b.wait_for_dependencies(timeout=2.0)

    async def test_raises_on_cancelled_dep(self, queue):
        """wait_for_dependencies raises JobCancelledError if a dep is cancelled."""
        a = await queue.enqueue("my.task")
        b = await queue.enqueue("my.task", depends_on=[a.ulid])

        # Cancel A
        await Job.query().filter(F("ulid") == a.ulid).update_one(
            status=JobStatus.CANCELLED.value,
            finished_at=now_epoch(),
            updated_at=now_epoch(),
        )

        with pytest.raises(JobCancelledError, match="cancelled"):
            await b.wait_for_dependencies(timeout=2.0)

    async def test_raises_on_timeout(self, queue):
        """wait_for_dependencies raises TimeoutError if deps don't complete."""
        a = await queue.enqueue("my.task")
        b = await queue.enqueue("my.task", depends_on=[a.ulid])

        with pytest.raises(TimeoutError, match="did not complete"):
            await b.wait_for_dependencies(timeout=0.1, poll_interval=0.05)

    async def test_raises_on_missing_dep(self, queue):
        """wait_for_dependencies raises JobFailedError if dep is deleted."""
        a = await queue.enqueue("my.task")
        b = await queue.enqueue("my.task", depends_on=[a.ulid])

        # Delete A
        await Job.query().filter(F("ulid") == a.ulid).delete_all()

        with pytest.raises(JobFailedError, match="not found"):
            await b.wait_for_dependencies(timeout=2.0)


# ---------------------------------------------------------------------------
# TestImmediateModeDependencies
# ---------------------------------------------------------------------------


class TestImmediateModeDependencies:
    """Dependencies in immediate mode."""

    async def test_resolves_on_complete(self, imm_queue):
        """Completing a job in immediate mode resolves dependents."""
        # Enqueue A without immediate mode to keep it pending
        a = await imm_queue.enqueue(
            f"{noop_task.__module__}.{noop_task.__qualname__}",
        )
        # A was executed immediately and completed
        refreshed_a = await Job.query().filter(F("ulid") == a.ulid).first()
        assert refreshed_a.status == JobStatus.COMPLETED.value

        # Now enqueue B depending on A — it should have pending_dep_count=0
        b_job = await imm_queue.enqueue(
            f"{noop_task.__module__}.{noop_task.__qualname__}",
            depends_on=[a.ulid],
        )
        refreshed_b = await Job.query().filter(F("ulid") == b_job.ulid).first()
        assert refreshed_b.status == JobStatus.COMPLETED.value
        assert refreshed_b.pending_dep_count == 0

    async def test_cascades_on_failure(self, imm_queue):
        """Failing a job in immediate mode cascades to dependents."""
        # Enqueue A (boom_task fails — immediate mode catches the exception
        # and returns the job in FAILED state)
        a = await imm_queue.enqueue(
            f"{boom_task.__module__}.{boom_task.__qualname__}",
        )
        assert a.status == JobStatus.FAILED.value

        # Enqueue B depending on A — should raise DependencyError
        with pytest.raises(DependencyError, match="is failed"):
            await imm_queue.enqueue(
                f"{noop_task.__module__}.{noop_task.__qualname__}",
                depends_on=[a.ulid],
            )
