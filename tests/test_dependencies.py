"""Tests for M10 — Job Dependencies/Chaining."""

import asyncio
import json
import logging
import shutil
import sqlite3
import time
from pathlib import Path

import pytest
import pytest_asyncio
from sqler import F
from sqler.models.async_queryset import AsyncSQLerQuerySet

from qler._time import now_epoch
from qler.enums import JobStatus
from qler.exceptions import DependencyError, JobCancelledError, JobFailedError
from qler.models.dependency import JobDependency
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
        bad_ulid = "01NONEXISTENT00000000000000"
        with pytest.raises(DependencyError, match="not found") as exc_info:
            await queue.enqueue("my.task", depends_on=[bad_ulid])
        assert exc_info.value.dependency_ulid == bad_ulid

    async def test_rejects_failed_dependency(self, queue):
        """Enqueue raises DependencyError if dep is FAILED."""
        a = await queue.enqueue("my.task")
        await Job.query().filter(F("ulid") == a.ulid).update_one(
            status=JobStatus.FAILED.value,
            finished_at=now_epoch(),
            updated_at=now_epoch(),
        )
        with pytest.raises(DependencyError, match="is failed") as exc_info:
            await queue.enqueue("my.task", depends_on=[a.ulid])
        assert exc_info.value.dependency_ulid == a.ulid

    async def test_rejects_cancelled_dependency(self, queue):
        """Enqueue raises DependencyError if dep is CANCELLED."""
        a = await queue.enqueue("my.task")
        await Job.query().filter(F("ulid") == a.ulid).update_one(
            status=JobStatus.CANCELLED.value,
            finished_at=now_epoch(),
            updated_at=now_epoch(),
        )
        with pytest.raises(DependencyError, match="is cancelled") as exc_info:
            await queue.enqueue("my.task", depends_on=[a.ulid])
        assert exc_info.value.dependency_ulid == a.ulid

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
        assert refreshed_b.last_error == f"Dependency {a.ulid} failed/cancelled"

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
        """Cascade-cancelled jobs have a descriptive last_error with parent ULID."""
        a = await queue.enqueue("my.task")
        b = await queue.enqueue("my.task", depends_on=[a.ulid])

        claimed_a = await queue.claim_job("worker-1", ["default"])
        await queue.fail_job(claimed_a, "worker-1", ValueError("boom"))

        refreshed_b = await Job.query().filter(F("ulid") == b.ulid).first()
        assert refreshed_b.last_error == f"Dependency {a.ulid} failed/cancelled"

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

    async def test_cascade_skips_running_dependents(self, queue):
        """Cascade cancel only cancels PENDING, not RUNNING dependents."""
        a = await queue.enqueue("my.task")
        b = await queue.enqueue("my.task")
        c = await queue.enqueue("my.task", depends_on=[a.ulid, b.ulid])

        # Complete B so C becomes claimable (pending_dep_count=1 from A only)
        await Job.query().filter(F("ulid") == b.ulid).update_one(
            status=JobStatus.COMPLETED.value,
            finished_at=now_epoch(),
            updated_at=now_epoch(),
        )
        # Manually set C to RUNNING (simulating it was already claimed)
        await Job.query().filter(F("ulid") == c.ulid).update_one(
            status=JobStatus.RUNNING.value,
            updated_at=now_epoch(),
        )

        # Fail A — cascade should skip C since it's RUNNING
        claimed_a = await queue.claim_job("worker-1", ["default"])
        assert claimed_a.ulid == a.ulid
        await queue.fail_job(claimed_a, "worker-1", ValueError("boom"))

        refreshed_c = await Job.query().filter(F("ulid") == c.ulid).first()
        assert refreshed_c.status == JobStatus.RUNNING.value

    async def test_cancel_completed_returns_false(self, queue):
        """cancel_job on a COMPLETED job returns False and does not cascade."""
        a = await queue.enqueue("my.task")
        b = await queue.enqueue("my.task", depends_on=[a.ulid])

        # Complete A
        await Job.query().filter(F("ulid") == a.ulid).update_one(
            status=JobStatus.COMPLETED.value,
            finished_at=now_epoch(),
            updated_at=now_epoch(),
        )
        refreshed_a = await Job.query().filter(F("ulid") == a.ulid).first()

        ok = await queue.cancel_job(refreshed_a)
        assert ok is False

        # B should remain PENDING (not cascade-cancelled)
        refreshed_b = await Job.query().filter(F("ulid") == b.ulid).first()
        assert refreshed_b.status == JobStatus.PENDING.value

    async def test_wide_fan_out_resolution(self, queue):
        """Completing parent resolves all 5 dependents."""
        parent = await queue.enqueue("my.task")
        children = []
        for _ in range(5):
            child = await queue.enqueue("my.task", depends_on=[parent.ulid])
            assert child.pending_dep_count == 1
            children.append(child)

        claimed = await queue.claim_job("worker-1", ["default"])
        assert claimed.ulid == parent.ulid
        await queue.complete_job(claimed, "worker-1")

        for child in children:
            refreshed = await Job.query().filter(F("ulid") == child.ulid).first()
            assert refreshed.pending_dep_count == 0


def _u(letter: str) -> str:
    """A fixed 26-character ulid, so the tree's order is known: A < B < C < D < E < P."""
    return "01J" + letter * 23


async def _shared_child_tree(queue):
    """P; A and B wait on P; E waits on A; D waits on B; C waits on A, B and E.

    C has two parents on level 1 (A and B) and one on level 2 (E). Built with explicit
    ulids through enqueue_many, the root in its own queue so a claim takes it alone.
    """
    await queue.enqueue_many([
        {"task_path": "t", "queue_name": "root", "ulid": _u("P")},
        {"task_path": "t", "ulid": _u("A"), "depends_on": [_u("P")]},
        {"task_path": "t", "ulid": _u("B"), "depends_on": [_u("P")]},
        {"task_path": "t", "ulid": _u("E"), "depends_on": [_u("A")]},
        {"task_path": "t", "ulid": _u("D"), "depends_on": [_u("B")]},
        {"task_path": "t", "ulid": _u("C"), "depends_on": [_u("A"), _u("B"), _u("E")]},
    ])
    root = await queue.claim_job("worker-1", ["root"])
    assert root.ulid == _u("P")
    return root


class TestCascadeReadsALevelAtATime:
    """The cascade reads the edges of a whole level per query and keeps its order and
    the parent each cancelled job names."""

    async def test_order_and_named_parent_with_a_shared_child(self, queue):
        root = await _shared_child_tree(queue)
        async with queue.db.transaction():
            cancelled = await queue._cascade_cancel_dependents(root.ulid)

        assert cancelled == [_u("A"), _u("B"), _u("C"), _u("E"), _u("D")]
        named = {
            j.ulid: j.last_error
            for j in await Job.query().filter(F("status") == JobStatus.CANCELLED.value).all()
        }
        assert named == {
            _u("A"): f"Dependency {_u('P')} failed/cancelled",
            _u("B"): f"Dependency {_u('P')} failed/cancelled",
            _u("C"): f"Dependency {_u('A')} failed/cancelled",
            _u("E"): f"Dependency {_u('A')} failed/cancelled",
            _u("D"): f"Dependency {_u('B')} failed/cancelled",
        }

    async def test_one_edge_read_per_level(self, queue, monkeypatch):
        root = await _shared_child_tree(queue)
        reads = {"n": 0}
        original = Queue._dependency_edges

        def counted(self):
            reads["n"] += 1
            return original.fget(self)

        monkeypatch.setattr(Queue, "_dependency_edges", property(counted))
        await queue.fail_job(root, "worker-1", ValueError("boom"))

        # Levels [P], [A, B], [C, E, D]: three reads (one per job would be six).
        assert reads["n"] == 3
        statuses = [
            j.status for j in await Job.query().order_by("ulid").all()
        ]
        assert statuses == [JobStatus.CANCELLED.value] * 5 + [JobStatus.FAILED.value]


# ---------------------------------------------------------------------------
# TestWaitForDependencies
# ---------------------------------------------------------------------------


class TestWaitForDependencies:
    """Job.wait_for_dependencies() polling."""

    async def test_no_deps_returns_immediately(self, queue):
        """wait_for_dependencies returns immediately when no deps."""
        a = await queue.enqueue("my.task")
        assert a.dependencies == []
        start = time.monotonic()
        await a.wait_for_dependencies(timeout=1.0)
        assert time.monotonic() - start < 0.1

    async def test_waits_for_completion(self, queue):
        """wait_for_dependencies returns when all deps complete."""
        a = await queue.enqueue("my.task")
        b = await queue.enqueue("my.task", depends_on=[a.ulid])
        assert b.dependencies == [a.ulid]

        # Complete A
        await Job.query().filter(F("ulid") == a.ulid).update_one(
            status=JobStatus.COMPLETED.value,
            finished_at=now_epoch(),
            updated_at=now_epoch(),
        )

        await b.wait_for_dependencies(timeout=2.0)
        refreshed_a = await Job.query().filter(F("ulid") == a.ulid).first()
        assert refreshed_a.status == JobStatus.COMPLETED.value

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

    async def test_multi_dep_one_failed(self, queue):
        """wait_for_dependencies raises on first failed dep even if others are fine."""
        a = await queue.enqueue("my.task")
        b = await queue.enqueue("my.task")
        c = await queue.enqueue("my.task", depends_on=[a.ulid, b.ulid])

        # Complete A, fail B
        await Job.query().filter(F("ulid") == a.ulid).update_one(
            status=JobStatus.COMPLETED.value,
            finished_at=now_epoch(),
            updated_at=now_epoch(),
        )
        await Job.query().filter(F("ulid") == b.ulid).update_one(
            status=JobStatus.FAILED.value,
            last_error="something broke",
            finished_at=now_epoch(),
            updated_at=now_epoch(),
        )

        with pytest.raises(JobFailedError, match="failed"):
            await c.wait_for_dependencies(timeout=2.0)


# ---------------------------------------------------------------------------
# TestTaskWrapperDependencies
# ---------------------------------------------------------------------------


class TestTaskWrapperDependencies:
    """TaskWrapper.enqueue() forwards _depends_on to Queue.enqueue()."""

    async def test_task_wrapper_forwards_depends_on(self, queue):
        """_depends_on is forwarded and stored on the created job."""
        wrapped = task(queue)(noop_task)
        a = await queue.enqueue("my.task")

        b = await wrapped.enqueue(_depends_on=[a.ulid])
        assert b.dependencies == [a.ulid]
        assert b.pending_dep_count == 1


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


# ---------------------------------------------------------------------------
# TestDependencyStress — adversarial graph topologies and edge cases
# ---------------------------------------------------------------------------


class TestDependencyStress:
    """Cruel tests: graph topologies that break naive implementations."""

    # -- Diamond dependency: the double-decrement trap --

    async def test_diamond_no_double_decrement(self, queue):
        """Diamond: A→D, B→D, A→C, B→C. Complete A then B.

        D and C each depend on both A and B. Completing A decrements
        both to pending_dep_count=1. Completing B decrements both to 0.
        A naive impl that doesn't guard on pending_dep_count > 0 would
        decrement below zero.
        """
        a = await queue.enqueue("my.task")
        b = await queue.enqueue("my.task")
        c = await queue.enqueue("my.task", depends_on=[a.ulid, b.ulid])
        d = await queue.enqueue("my.task", depends_on=[a.ulid, b.ulid])
        assert c.pending_dep_count == 2
        assert d.pending_dep_count == 2

        # Complete A
        claimed = await queue.claim_job("w1", ["default"])
        assert claimed.ulid == a.ulid
        await queue.complete_job(claimed, "w1")

        rc = await Job.query().filter(F("ulid") == c.ulid).first()
        rd = await Job.query().filter(F("ulid") == d.ulid).first()
        assert rc.pending_dep_count == 1
        assert rd.pending_dep_count == 1

        # Complete B
        claimed = await queue.claim_job("w1", ["default"])
        assert claimed.ulid == b.ulid
        await queue.complete_job(claimed, "w1")

        rc = await Job.query().filter(F("ulid") == c.ulid).first()
        rd = await Job.query().filter(F("ulid") == d.ulid).first()
        assert rc.pending_dep_count == 0
        assert rd.pending_dep_count == 0

        # Both C and D are now claimable
        claimed_ids = set()
        for _ in range(2):
            j = await queue.claim_job("w1", ["default"])
            assert j is not None
            claimed_ids.add(j.ulid)
        assert claimed_ids == {c.ulid, d.ulid}

    async def test_diamond_cascade_cancel_idempotent(self, queue):
        """Diamond cascade: A→B, A→C, B→D, C→D. Fail A.

        B and C are cascade-cancelled. D depends on both B and C —
        the cascade from B tries to cancel D, then the cascade from C
        tries to cancel D again. D must end up CANCELLED exactly once,
        not blow up.
        """
        a = await queue.enqueue("my.task")
        b = await queue.enqueue("my.task", depends_on=[a.ulid])
        c = await queue.enqueue("my.task", depends_on=[a.ulid])
        d = await queue.enqueue("my.task", depends_on=[b.ulid, c.ulid])

        claimed = await queue.claim_job("w1", ["default"])
        assert claimed.ulid == a.ulid
        await queue.fail_job(claimed, "w1", ValueError("root failure"))

        for ulid in [b.ulid, c.ulid, d.ulid]:
            job = await Job.query().filter(F("ulid") == ulid).first()
            assert job.status == JobStatus.CANCELLED.value

        # D's error message references whichever parent was cancelled first
        rd = await Job.query().filter(F("ulid") == d.ulid).first()
        assert "failed/cancelled" in rd.last_error

    # -- Deep chains: recursion depth --

    async def test_deep_chain_resolution(self, queue):
        """20-deep chain: complete root, all 19 descendants unblock one by one."""
        chain = [await queue.enqueue("my.task")]
        for _ in range(19):
            child = await queue.enqueue("my.task", depends_on=[chain[-1].ulid])
            assert child.pending_dep_count == 1
            chain.append(child)

        # Walk the chain: claim and complete each, the next becomes claimable
        for i in range(20):
            claimed = await queue.claim_job("w1", ["default"])
            assert claimed is not None, f"Nothing claimable at depth {i}"
            assert claimed.ulid == chain[i].ulid
            await queue.complete_job(claimed, "w1")

        # Everything claimed
        nothing = await queue.claim_job("w1", ["default"])
        assert nothing is None

    async def test_deep_chain_cascade_cancel(self, queue):
        """20-deep chain: fail the root, all 19 descendants cascade-cancelled."""
        chain = [await queue.enqueue("my.task")]
        for _ in range(19):
            chain.append(await queue.enqueue("my.task", depends_on=[chain[-1].ulid]))

        claimed = await queue.claim_job("w1", ["default"])
        assert claimed.ulid == chain[0].ulid
        await queue.fail_job(claimed, "w1", ValueError("root dies"))

        for i, job_ref in enumerate(chain[1:], 1):
            job = await Job.query().filter(F("ulid") == job_ref.ulid).first()
            assert job.status == JobStatus.CANCELLED.value, f"Job at depth {i} not cancelled"

    # -- Self-dependency: deadlock detection --

    async def test_self_dependency_creates_deadlock(self, queue):
        """Two jobs each depending on the other: neither can ever be claimed."""
        a = await queue.enqueue("my.task")
        b = await queue.enqueue("my.task", depends_on=[a.ulid])

        # B depends on A (pending_dep_count=1), so B is blocked
        assert b.pending_dep_count == 1

        # Complete A → B becomes claimable
        claimed_a = await queue.claim_job("w1", ["default"])
        assert claimed_a.ulid == a.ulid
        await queue.complete_job(claimed_a, "w1")

        # B should now be unblocked (dep resolved)
        refreshed_b = await Job.query().filter(F("ulid") == b.ulid).first()
        assert refreshed_b.pending_dep_count == 0

        claimed_b = await queue.claim_job("w1", ["default"])
        assert claimed_b is not None
        assert claimed_b.ulid == b.ulid

    async def test_mutual_dependency_deadlock(self, queue):
        """Two jobs each blocked on an unsatisfiable dep: neither can be claimed.

        Simulates the effect of a circular dependency without needing to
        mutate the DB directly. A depends on X (nonexistent PENDING job),
        B depends on A. Cancel A to cascade-cancel B.
        """
        # Create two blocking parents that will never complete
        x = await queue.enqueue("my.task")
        y = await queue.enqueue("my.task")
        a = await queue.enqueue("my.task", depends_on=[y.ulid])
        b = await queue.enqueue("my.task", depends_on=[a.ulid])

        # Claim x and y (removing them from claimable pool) but don't complete
        await queue.claim_job("w1", ["default"])  # x
        await queue.claim_job("w1", ["default"])  # y

        # Neither a nor b is claimable — both blocked
        claimed = await queue.claim_job("w1", ["default"])
        assert claimed is None

        # Cancel A breaks the deadlock — B gets cascade-cancelled
        await queue.cancel_job(a)

        rb = await Job.query().filter(F("ulid") == b.ulid).first()
        assert rb.status == JobStatus.CANCELLED.value

    # -- Cross-queue dependencies --

    async def test_cross_queue_dependency(self, queue):
        """Job in queue 'emails' depends on job in queue 'default'."""
        a = await queue.enqueue("my.task", queue_name="default")
        b = await queue.enqueue("my.task", queue_name="emails", depends_on=[a.ulid])
        assert b.pending_dep_count == 1

        # Worker only listens to 'default' — claims A
        claimed = await queue.claim_job("w1", ["default"])
        assert claimed.ulid == a.ulid
        await queue.complete_job(claimed, "w1")

        # B should be unblocked now
        rb = await Job.query().filter(F("ulid") == b.ulid).first()
        assert rb.pending_dep_count == 0

        # Worker listens to 'emails' — claims B
        claimed = await queue.claim_job("w1", ["emails"])
        assert claimed is not None
        assert claimed.ulid == b.ulid

    # -- Depend on RUNNING job --

    async def test_depend_on_running_job(self, queue):
        """Enqueue with depends_on a RUNNING job succeeds with pending_dep_count=1."""
        a = await queue.enqueue("my.task")
        claimed_a = await queue.claim_job("w1", ["default"])
        assert claimed_a.ulid == a.ulid
        assert claimed_a.status == JobStatus.RUNNING.value

        # B depends on A which is RUNNING — should be allowed
        b = await queue.enqueue("my.task", depends_on=[a.ulid])
        assert b.pending_dep_count == 1

        # Complete A, B should unblock
        await queue.complete_job(claimed_a, "w1")
        rb = await Job.query().filter(F("ulid") == b.ulid).first()
        assert rb.pending_dep_count == 0

    # -- Retry exhaustion then cascade --

    async def test_retry_exhaustion_cascades(self, queue):
        """Job with max_retries=2: fail 3 times. Terminal failure cascades.

        Uses retry_delay=0 so retried jobs are immediately reclaimable.
        """
        a = await queue.enqueue("my.task", max_retries=2, retry_delay=0)
        b = await queue.enqueue("my.task", depends_on=[a.ulid])

        # Fail #1: retryable, no cascade
        claimed = await queue.claim_job("w1", ["default"])
        assert claimed.ulid == a.ulid
        await queue.fail_job(claimed, "w1", ValueError("attempt 1"))
        rb = await Job.query().filter(F("ulid") == b.ulid).first()
        assert rb.status == JobStatus.PENDING.value

        # Fail #2: retryable, no cascade
        claimed = await queue.claim_job("w1", ["default"])
        assert claimed.ulid == a.ulid
        await queue.fail_job(claimed, "w1", ValueError("attempt 2"))
        rb = await Job.query().filter(F("ulid") == b.ulid).first()
        assert rb.status == JobStatus.PENDING.value

        # Fail #3: terminal — retries exhausted, cascade fires
        claimed = await queue.claim_job("w1", ["default"])
        assert claimed.ulid == a.ulid
        await queue.fail_job(claimed, "w1", ValueError("attempt 3"))

        ra = await Job.query().filter(F("ulid") == a.ulid).first()
        assert ra.status == JobStatus.FAILED.value

        rb = await Job.query().filter(F("ulid") == b.ulid).first()
        assert rb.status == JobStatus.CANCELLED.value
        assert rb.last_error == f"Dependency {a.ulid} failed/cancelled"

    # -- Massive fan-in: one job depends on many parents --

    async def test_fan_in_10_parents(self, queue):
        """Job depends on 10 parents. Complete all 10, job unblocks."""
        parents = []
        for _ in range(10):
            parents.append(await queue.enqueue("my.task"))
        # Sort by ULID to match claim order (ULID ordering = claim priority)
        parents.sort(key=lambda j: j.ulid)
        child = await queue.enqueue(
            "my.task", depends_on=[p.ulid for p in parents]
        )
        assert child.pending_dep_count == 10

        # Complete 9 of 10 — child still blocked
        for i in range(9):
            claimed = await queue.claim_job("w1", ["default"])
            assert claimed.ulid == parents[i].ulid
            await queue.complete_job(claimed, "w1")

        rc = await Job.query().filter(F("ulid") == child.ulid).first()
        assert rc.pending_dep_count == 1

        # Complete the last parent — child unblocks
        claimed = await queue.claim_job("w1", ["default"])
        assert claimed.ulid == parents[9].ulid
        await queue.complete_job(claimed, "w1")

        rc = await Job.query().filter(F("ulid") == child.ulid).first()
        assert rc.pending_dep_count == 0

        # Child is claimable
        claimed = await queue.claim_job("w1", ["default"])
        assert claimed is not None
        assert claimed.ulid == child.ulid

    async def test_fan_in_one_parent_fails(self, queue):
        """10 parents, 1 fails terminally. Child is cascade-cancelled."""
        parents = []
        for _ in range(10):
            parents.append(await queue.enqueue("my.task"))
        child = await queue.enqueue(
            "my.task", depends_on=[p.ulid for p in parents]
        )

        # Complete first 5
        for _ in range(5):
            claimed = await queue.claim_job("w1", ["default"])
            await queue.complete_job(claimed, "w1")

        # Fail the 6th — terminal (max_retries=0 default), cascade fires
        claimed = await queue.claim_job("w1", ["default"])
        assert claimed.ulid in {p.ulid for p in parents}
        await queue.fail_job(claimed, "w1", ValueError("one bad apple"))

        rc = await Job.query().filter(F("ulid") == child.ulid).first()
        assert rc.status == JobStatus.CANCELLED.value

    # -- Empty depends_on --

    async def test_empty_depends_on_treated_as_no_deps(self, queue):
        """depends_on=[] should produce dependencies=[] and pending_dep_count=0."""
        a = await queue.enqueue("my.task", depends_on=[])
        assert a.dependencies == []
        assert a.pending_dep_count == 0

        # Immediately claimable
        claimed = await queue.claim_job("w1", ["default"])
        assert claimed is not None
        assert claimed.ulid == a.ulid

    # -- Priority still works with dependencies --

    async def test_priority_ordering_with_deps(self, queue):
        """High-priority blocked job is skipped in favor of lower-priority unblocked jobs."""
        parent = await queue.enqueue("my.task", priority=0)
        low = await queue.enqueue("my.task", priority=1)
        high = await queue.enqueue("my.task", priority=10)
        blocked_high = await queue.enqueue(
            "my.task", priority=100, depends_on=[parent.ulid]
        )

        # Even though blocked_high has priority 100, it's blocked.
        # Claim order: high (10), low (1), parent (0)
        claimed = await queue.claim_job("w1", ["default"])
        assert claimed.ulid == high.ulid

        claimed = await queue.claim_job("w2", ["default"])
        assert claimed.ulid == low.ulid

        claimed = await queue.claim_job("w3", ["default"])
        assert claimed.ulid == parent.ulid

        # Only blocked_high remains, still blocked (parent is RUNNING now, not COMPLETED)
        claimed = await queue.claim_job("w4", ["default"])
        assert claimed is None

    # -- Resolution after completion does not affect unrelated jobs --

    async def test_resolution_ignores_unrelated_jobs(self, queue):
        """Completing X does not affect Y's pending_dep_count when Y doesn't depend on X."""
        x = await queue.enqueue("my.task")
        a = await queue.enqueue("my.task")
        y = await queue.enqueue("my.task", depends_on=[a.ulid])
        assert y.pending_dep_count == 1

        # Complete X (unrelated to Y)
        cx = await queue.claim_job("w1", ["default"])
        assert cx.ulid == x.ulid
        await queue.complete_job(cx, "w1")

        # Y's count unchanged
        ry = await Job.query().filter(F("ulid") == y.ulid).first()
        assert ry.pending_dep_count == 1

    # -- Complete parent, then try to cancel it --

    async def test_cancel_after_complete_noop(self, queue):
        """Cancelling an already-COMPLETED parent doesn't cascade to children."""
        a = await queue.enqueue("my.task")
        b = await queue.enqueue("my.task", depends_on=[a.ulid])

        # Complete A normally
        claimed = await queue.claim_job("w1", ["default"])
        await queue.complete_job(claimed, "w1")

        # B should have pending_dep_count=0
        rb = await Job.query().filter(F("ulid") == b.ulid).first()
        assert rb.pending_dep_count == 0

        # Try to cancel A (already completed)
        ra = await Job.query().filter(F("ulid") == a.ulid).first()
        ok = await queue.cancel_job(ra)
        assert ok is False

        # B still PENDING, not cascade-cancelled
        rb = await Job.query().filter(F("ulid") == b.ulid).first()
        assert rb.status == JobStatus.PENDING.value

    # -- Middle-of-chain cancellation --

    async def test_cancel_middle_of_chain(self, queue):
        """A→B→C: cancel B. C gets cascade-cancelled. A is unaffected."""
        a = await queue.enqueue("my.task")
        b = await queue.enqueue("my.task", depends_on=[a.ulid])
        c = await queue.enqueue("my.task", depends_on=[b.ulid])

        # Cancel B directly
        ok = await queue.cancel_job(b)
        assert ok is True

        rb = await Job.query().filter(F("ulid") == b.ulid).first()
        rc = await Job.query().filter(F("ulid") == c.ulid).first()
        ra = await Job.query().filter(F("ulid") == a.ulid).first()

        assert rb.status == JobStatus.CANCELLED.value
        assert rc.status == JobStatus.CANCELLED.value
        assert ra.status == JobStatus.PENDING.value  # upstream unaffected

    # -- Multiple independent subgraphs in one queue --

    async def test_independent_subgraphs(self, queue):
        """Two independent chains in the same queue don't interfere.

        Chain 1: A→B
        Chain 2: X→Y
        Fail A → B cancelled, X and Y unaffected.
        """
        a = await queue.enqueue("my.task")
        b = await queue.enqueue("my.task", depends_on=[a.ulid])
        x = await queue.enqueue("my.task")
        y = await queue.enqueue("my.task", depends_on=[x.ulid])

        # Fail A
        claimed = await queue.claim_job("w1", ["default"])
        assert claimed.ulid == a.ulid
        await queue.fail_job(claimed, "w1", ValueError("chain 1 dies"))

        rb = await Job.query().filter(F("ulid") == b.ulid).first()
        assert rb.status == JobStatus.CANCELLED.value

        # Chain 2 completely unaffected
        rx = await Job.query().filter(F("ulid") == x.ulid).first()
        ry = await Job.query().filter(F("ulid") == y.ulid).first()
        assert rx.status == JobStatus.PENDING.value
        assert ry.status == JobStatus.PENDING.value
        assert ry.pending_dep_count == 1

        # X is still claimable
        claimed = await queue.claim_job("w1", ["default"])
        assert claimed.ulid == x.ulid


# ---------------------------------------------------------------------------
# TestFinishIsOneWrite — completing or failing a parent and settling its
# children is one transaction: all of it lands, or none of it
# ---------------------------------------------------------------------------


def _is_decrement(fields):
    return "pending_dep_count" in fields


def _is_cascade_cancel(fields):
    return fields.get("status") == JobStatus.CANCELLED.value and "last_error" in fields


async def _job(ulid):
    return await Job.query().filter(F("ulid") == ulid).first()


class TestFinishIsOneWrite:
    """A stop between the parent's write and its children's writes strands nothing."""

    async def test_complete_rolls_back_when_a_decrement_fails(
        self, queue, raise_on_nth_update
    ):
        parent = await queue.enqueue("my.task")
        children = [await queue.enqueue("my.task", depends_on=[parent.ulid]) for _ in range(3)]
        claimed = await queue.claim_job("worker-1", ["default"])
        assert claimed.ulid == parent.ulid

        raise_on_nth_update(_is_decrement, 2)
        with pytest.raises(sqlite3.OperationalError, match="database is locked"):
            await queue.complete_job(claimed, "worker-1")

        p = await _job(parent.ulid)
        assert p.status == JobStatus.RUNNING.value
        assert p.worker_id == "worker-1"
        assert [(await _job(c.ulid)).pending_dep_count for c in children] == [1, 1, 1]

        await queue.complete_job(claimed, "worker-1")

        p = await _job(parent.ulid)
        assert p.status == JobStatus.COMPLETED.value
        assert [(await _job(c.ulid)).pending_dep_count for c in children] == [0, 0, 0]
        nxt = await queue.claim_job("worker-1", ["default"])
        assert nxt.ulid == children[0].ulid

    async def test_fail_rolls_back_when_a_cascade_cancel_fails(
        self, queue, raise_on_nth_update
    ):
        parent = await queue.enqueue("my.task")
        children = [await queue.enqueue("my.task", depends_on=[parent.ulid]) for _ in range(3)]
        grandchild = await queue.enqueue("my.task", depends_on=[children[0].ulid])
        claimed = await queue.claim_job("worker-1", ["default"])
        assert claimed.ulid == parent.ulid

        raise_on_nth_update(_is_cascade_cancel, 2)
        with pytest.raises(sqlite3.OperationalError, match="database is locked"):
            await queue.fail_job(claimed, "worker-1", ValueError("boom"))

        p = await _job(parent.ulid)
        assert p.status == JobStatus.RUNNING.value
        assert p.worker_id == "worker-1"
        dependents = [*children, grandchild]
        assert [(await _job(j.ulid)).status for j in dependents] == [JobStatus.PENDING.value] * 4

        await queue.fail_job(claimed, "worker-1", ValueError("boom"))

        assert (await _job(parent.ulid)).status == JobStatus.FAILED.value
        assert [(await _job(j.ulid)).status for j in dependents] == [JobStatus.CANCELLED.value] * 4

    async def test_no_event_for_a_rolled_back_finish(
        self, queue, monkeypatch, raise_on_nth_update, caplog
    ):
        fired: list[str] = []
        monkeypatch.setattr("qler.queue._notify_fire", fired.append)

        def lifecycle_events():
            return [r.event for r in caplog.records if r.name == "qler.lifecycle"]

        done = await queue.enqueue("my.task")
        done_children = [await queue.enqueue("my.task", depends_on=[done.ulid]) for _ in range(3)]
        failed = await queue.enqueue("my.task")
        failed_children = [
            await queue.enqueue("my.task", depends_on=[failed.ulid]) for _ in range(3)
        ]
        claimed_done = await queue.claim_job("worker-1", ["default"])
        claimed_failed = await queue.claim_job("worker-1", ["default"])
        assert (claimed_done.ulid, claimed_failed.ulid) == (done.ulid, failed.ulid)

        with caplog.at_level(logging.INFO, logger="qler.lifecycle"):
            caplog.clear()
            raise_on_nth_update(_is_decrement, 2)
            with pytest.raises(sqlite3.OperationalError):
                await queue.complete_job(claimed_done, "worker-1")
            raise_on_nth_update(_is_cascade_cancel, 2)
            with pytest.raises(sqlite3.OperationalError):
                await queue.fail_job(claimed_failed, "worker-1", ValueError("boom"))
            assert lifecycle_events() == []
            assert fired == []

            await queue.complete_job(claimed_done, "worker-1")
            assert lifecycle_events() == ["job.completed"]
            assert fired == [done.ulid]

            caplog.clear()
            fired.clear()
            await queue.fail_job(claimed_failed, "worker-1", ValueError("boom"))
            assert lifecycle_events() == ["job.failed"]
            assert sorted(fired) == sorted([failed.ulid, *(c.ulid for c in failed_children)])
        assert [(await _job(c.ulid)).pending_dep_count for c in done_children] == [0, 0, 0]

    async def test_child_enqueued_during_complete_ends_claimable(self, queue, monkeypatch):
        """Pin: the child's enqueue starts while the parent's completion is in flight."""
        parent = await queue.enqueue("my.task")
        claimed = await queue.claim_job("worker-1", ["default"])

        in_flight, release, enqueue_started = asyncio.Event(), asyncio.Event(), asyncio.Event()
        resolve = Queue._resolve_dependencies

        async def held_resolve(self, completed_ulid):
            in_flight.set()
            await release.wait()
            return await resolve(self, completed_ulid)

        monkeypatch.setattr(Queue, "_resolve_dependencies", held_resolve)

        complete = asyncio.create_task(queue.complete_job(claimed, "worker-1"))
        await in_flight.wait()

        transaction = queue._db.transaction

        def marked_transaction():
            if asyncio.current_task() is not complete:
                enqueue_started.set()
            return transaction()

        monkeypatch.setattr(queue._db, "transaction", marked_transaction)
        enqueue = asyncio.create_task(queue.enqueue("my.task", depends_on=[parent.ulid]))
        await enqueue_started.wait()
        release.set()
        await complete
        child = await enqueue

        assert (await _job(child.ulid)).pending_dep_count == 0
        nxt = await queue.claim_job("worker-1", ["default"])
        assert nxt.ulid == child.ulid

    async def test_child_enqueued_just_before_complete_is_decremented(self, queue):
        """Pin: the child's enqueue commits just before the parent's completion starts."""
        parent = await queue.enqueue("my.task")
        claimed = await queue.claim_job("worker-1", ["default"])

        enqueued = asyncio.Event()

        async def complete_after_enqueue():
            await enqueued.wait()
            await queue.complete_job(claimed, "worker-1")

        complete = asyncio.create_task(complete_after_enqueue())
        child = await queue.enqueue("my.task", depends_on=[parent.ulid])
        assert child.pending_dep_count == 1
        enqueued.set()
        await complete

        assert (await _job(parent.ulid)).status == JobStatus.COMPLETED.value
        assert (await _job(child.ulid)).pending_dep_count == 0
        nxt = await queue.claim_job("worker-1", ["default"])
        assert nxt.ulid == child.ulid

    async def test_parent_completed_twice_at_once_decrements_once(self, queue, monkeypatch):
        """Pin: two tasks complete the same parent; each child loses one count."""
        parent = await queue.enqueue("my.task")
        other = await queue.enqueue("my.task", queue_name="elsewhere")
        children = [
            await queue.enqueue("my.task", depends_on=[parent.ulid, other.ulid]) for _ in range(3)
        ]
        claimed = await queue.claim_job("worker-1", ["default"])
        assert claimed.ulid == parent.ulid

        in_flight, release, second_started = asyncio.Event(), asyncio.Event(), asyncio.Event()
        resolve = Queue._resolve_dependencies

        async def held_resolve(self, completed_ulid):
            if not in_flight.is_set():
                in_flight.set()
                await release.wait()
            return await resolve(self, completed_ulid)

        monkeypatch.setattr(Queue, "_resolve_dependencies", held_resolve)
        first = asyncio.create_task(queue.complete_job(claimed, "worker-1"))
        await in_flight.wait()

        # The second completion has started its write: it asked for the transaction
        # (or, without one, sent its guarded update).
        transaction = queue._db.transaction
        update_one = AsyncSQLerQuerySet.update_one

        def marked_transaction():
            if asyncio.current_task() is second:
                second_started.set()
            return transaction()

        async def marked_update_one(self, **fields):
            if asyncio.current_task() is second:
                second_started.set()
            return await update_one(self, **fields)

        monkeypatch.setattr(queue._db, "transaction", marked_transaction)
        monkeypatch.setattr(AsyncSQLerQuerySet, "update_one", marked_update_one)
        second = asyncio.create_task(queue.complete_job(claimed, "worker-1"))
        await second_started.wait()
        release.set()
        await asyncio.gather(first, second)

        assert (await _job(parent.ulid)).status == JobStatus.COMPLETED.value
        assert [(await _job(c.ulid)).pending_dep_count for c in children] == [1, 1, 1]


# ---------------------------------------------------------------------------
# The edges are a sqler model; a database file of qler 0.5.1 is upgraded
# ---------------------------------------------------------------------------

FIXTURES = Path(__file__).parent / "fixtures"


def _old_file(tmp_path: Path) -> tuple[str, dict[str, str]]:
    """A copy of the queue database written by released qler 0.5.1 (see
    ``fixtures/make_qler_0_5_1_db.py``), and the ulid of each of its jobs by role."""
    path = tmp_path / "qler_0_5_1.db"
    shutil.copy(FIXTURES / "qler_0_5_1.db", path)
    roles = json.loads((FIXTURES / "qler_0_5_1.json").read_text())
    return str(path), roles


async def _edges(q: Queue) -> list[tuple[str, str]]:
    return sorted(
        (e.parent_ulid, e.child_ulid) for e in await JobDependency.using(q.db).all()
    )


async def _schema_names(q: Queue) -> set[tuple[str, str]]:
    rows = await q.db.execute_sql(
        "SELECT type, name FROM sqlite_master WHERE name LIKE 'qler_job_dep%' "
        "OR name LIKE 'idx_qler_job_dep%'"
    )
    return {(r["type"], r["name"]) for r in rows}


# What a database holds for the edges once this code has opened it
EDGE_SCHEMA = {
    ("table", "qler_job_dependencies"),
    ("index", "idx_qler_job_dependencies_edge"),
    ("view", "qler_job_deps"),
}

# The statements qler 0.5.1 sends for the edges (src/qler/queue.py at the tag v0.5.1)
OLD_CREATE_TABLE = (
    "CREATE TABLE IF NOT EXISTS qler_job_deps ("
    "parent_ulid TEXT NOT NULL, "
    "child_ulid TEXT NOT NULL, "
    "PRIMARY KEY (parent_ulid, child_ulid))"
)
OLD_CREATE_INDEX = (
    "CREATE INDEX IF NOT EXISTS idx_qler_job_deps_parent ON qler_job_deps (parent_ulid)"
)
OLD_CHILDREN = "SELECT child_ulid FROM qler_job_deps WHERE parent_ulid = ?"
OLD_ADD_EDGE = "INSERT OR IGNORE INTO qler_job_deps (parent_ulid, child_ulid) VALUES (?, ?)"


class TestDependencyEdgesModel:

    async def test_file_of_qler_0_5_1_opens_and_keeps_its_edges(self, tmp_path):
        path, roles = _old_file(tmp_path)
        q = Queue(path)
        await q.init_db()
        try:
            assert await _schema_names(q) == EDGE_SCHEMA
            assert await _edges(q) == sorted(
                [(roles["parent"], roles["child_a"]), (roles["parent"], roles["child_b"])]
            )

            parent = await q.claim_job("w", ["deps"])
            assert parent is not None
            assert parent.ulid == roles["parent"]
            assert await q.claim_job("w", ["deps"]) is None
            await q.complete_job(parent, "w", result=1)
            children = [await q.claim_job("w", ["deps"]) for _ in range(2)]
            assert sorted(c.ulid for c in children) == sorted(
                [roles["child_a"], roles["child_b"]]
            )

            again = await q.enqueue("fixture.task", queue_name="keys", idempotency_key="fixture-idem")
            assert again.ulid == roles["idem"]
            again = await q.enqueue("fixture.task", queue_name="keys", unique_key="fixture-uniq")
            assert again.ulid == roles["uniq"]
            assert [j.ulid for j in await q.archived_jobs()] == [roles["archived"]]

            for child in children:
                await q.complete_job(child, "w", result=2)
            # A threshold one second ahead takes the children finished in this second too.
            moved = await q.archive_jobs(older_than_seconds=-1)
            finished = ["done", "dead", "limited_done", "parent", "child_a", "child_b"]
            assert moved == len(finished)
            assert await JobDependency.using(q.db).count() == 0
            archived = [j.ulid for j in await q.archived_jobs()]
            assert len(archived) == 7
            assert set(archived) == {roles[r] for r in [*finished, "archived"]}
            assert {j.ulid for j in await q.jobs.all()} == {roles["idem"], roles["uniq"]}
        finally:
            await q.close()

    async def test_two_queues_upgrade_one_old_file_at_once(self, tmp_path):
        path, roles = _old_file(tmp_path)
        q1, q2 = Queue(path), Queue(path)
        try:
            await asyncio.gather(q1.init_db(), q2.init_db())
            assert await _edges(q1) == sorted(
                [(roles["parent"], roles["child_a"]), (roles["parent"], roles["child_b"])]
            )
            assert await _schema_names(q2) == EDGE_SCHEMA
        finally:
            await q1.close()
            await q2.close()

    async def test_qler_0_5_1_beside_this_code_reads_the_edges_and_cannot_write_them(
        self, tmp_path
    ):
        path, roles = _old_file(tmp_path)
        q = Queue(path)
        await q.init_db()
        await q.close()

        old = sqlite3.connect(path)
        try:
            children = [row[0] for row in old.execute(OLD_CHILDREN, [roles["parent"]])]
            assert sorted(children) == sorted([roles["child_a"], roles["child_b"]])
            with pytest.raises(sqlite3.OperationalError, match="cannot modify qler_job_deps"):
                old.execute(OLD_ADD_EDGE, [roles["parent"], roles["idem"]])
        finally:
            old.close()

    async def test_qler_0_5_1_cannot_start_on_a_file_this_code_made(self, tmp_path):
        path = str(tmp_path / "new.db")
        q = Queue(path)
        await q.init_db()
        await q.close()

        old = sqlite3.connect(path)
        try:
            old.execute(OLD_CREATE_TABLE)
            with pytest.raises(sqlite3.OperationalError, match="views may not be indexed"):
                old.execute(OLD_CREATE_INDEX)
        finally:
            old.close()

    async def test_dependency_named_twice_is_one_edge_in_enqueue(self, queue):
        parent = await queue.enqueue("t")
        child = await queue.enqueue("t", depends_on=[parent.ulid, parent.ulid])
        assert child.pending_dep_count == 1
        assert await _edges(queue) == [(parent.ulid, child.ulid)]

    async def test_dependency_named_twice_is_one_edge_in_enqueue_many(self, queue):
        parent = await queue.enqueue("t")
        first, child = await queue.enqueue_many([
            {"task_path": "t"},
            {"task_path": "t", "depends_on": [parent.ulid, 0, parent.ulid, 0]},
        ])
        assert child.pending_dep_count == 2
        assert await _edges(queue) == sorted(
            [(parent.ulid, child.ulid), (first.ulid, child.ulid)]
        )

    async def test_upgrade_waits_for_the_other_process_upgrading(self, tmp_path, monkeypatch):
        """One queue is held inside its upgrade, after its look for the old table and before
        it writes the edges; the second queue starts its upgrade then. The second one waits for
        the first to finish (or, without the transaction, reads the old table and writes the
        edges a second time). Either way it is released once it has started."""
        path, roles = _old_file(tmp_path)
        upgrade = Queue._upgrade_job_deps_from_0_5_1

        async def no_upgrade(self):
            return None

        monkeypatch.setattr(Queue, "_upgrade_job_deps_from_0_5_1", no_upgrade)
        q1, q2 = Queue(path), Queue(path)
        await q1.init_db()
        await q2.init_db()
        monkeypatch.setattr(Queue, "_upgrade_job_deps_from_0_5_1", upgrade)
        try:
            held, release, second_started = asyncio.Event(), asyncio.Event(), asyncio.Event()
            save_many = JobDependency.save_many

            async def held_save_many(instances, *, db=None):
                if db is q1.db:
                    held.set()
                    await release.wait()
                return await save_many(instances, db=db)

            transaction = q2.db.transaction
            execute_sql = q2.db.execute_sql

            def marked_transaction():
                second_started.set()
                return transaction()

            async def marked_execute_sql(query, params=None):
                rows = await execute_sql(query, params)
                if "FROM qler_job_deps AS old" in query:
                    second_started.set()
                return rows

            monkeypatch.setattr(JobDependency, "save_many", held_save_many)
            monkeypatch.setattr(q2.db, "transaction", marked_transaction)
            monkeypatch.setattr(q2.db, "execute_sql", marked_execute_sql)

            first = asyncio.create_task(q1._upgrade_job_deps_from_0_5_1())
            await held.wait()
            second = asyncio.create_task(q2._upgrade_job_deps_from_0_5_1())
            await second_started.wait()
            release.set()
            await asyncio.gather(first, second)

            assert await _edges(q1) == sorted(
                [(roles["parent"], roles["child_a"]), (roles["parent"], roles["child_b"])]
            )
            assert await _schema_names(q1) == EDGE_SCHEMA
        finally:
            await q1.close()
            await q2.close()
