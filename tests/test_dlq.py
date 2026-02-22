"""Tests for M11 — Dead Letter Queue."""

import pytest
import pytest_asyncio
from sqler import AsyncSQLerDB, F

from qler._time import now_epoch
from qler.enums import FailureKind, JobStatus
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
    """Queue without DLQ for comparison tests."""
    q = Queue(db)
    await q.init_db()
    yield q


@pytest_asyncio.fixture
async def dlq_queue(db):
    """Queue with DLQ configured."""
    q = Queue(db, dlq="dead_letters")
    await q.init_db()
    yield q


@pytest_asyncio.fixture
async def imm_queue(db):
    """Immediate-mode queue with DLQ."""
    q = Queue(db, immediate=True, dlq="dead_letters")
    await q.init_db()
    task(q)(noop_task)
    task(q)(add_task)
    task(q)(boom_task)
    yield q


@pytest_asyncio.fixture
async def imm_queue_no_dlq(db):
    """Immediate-mode queue without DLQ."""
    q = Queue(db, immediate=True)
    await q.init_db()
    task(q)(noop_task)
    task(q)(add_task)
    task(q)(boom_task)
    yield q


# ---------------------------------------------------------------------------
# TestDLQConfiguration
# ---------------------------------------------------------------------------


class TestDLQConfiguration:
    """Queue with/without dlq parameter."""

    async def test_queue_without_dlq(self, queue):
        """Default queue has no DLQ configured."""
        assert queue._dlq is None

    async def test_queue_with_dlq(self, dlq_queue):
        """Queue with dlq parameter stores the name."""
        assert dlq_queue._dlq == "dead_letters"

    async def test_custom_dlq_name(self, db):
        """DLQ name can be customized."""
        q = Queue(db, dlq="failures")
        await q.init_db()
        assert q._dlq == "failures"


# ---------------------------------------------------------------------------
# TestDLQOnFailure
# ---------------------------------------------------------------------------


class TestDLQOnFailure:
    """Terminal failure moves job to DLQ when configured."""

    async def test_terminal_failure_moves_to_dlq(self, dlq_queue):
        """Job that exhausts retries lands in the DLQ queue."""
        job = await dlq_queue.enqueue(
            "my.task", queue_name="default", max_retries=0,
        )
        claimed = await dlq_queue.claim_job("w1", ["default"])
        assert claimed is not None
        assert claimed.ulid == job.ulid

        await dlq_queue.fail_job(claimed, "w1", ValueError("boom"), failure_kind=FailureKind.EXCEPTION)

        updated = await Job.query().filter(F("ulid") == job.ulid).first()
        assert updated.status == JobStatus.FAILED.value
        assert updated.queue_name == "dead_letters"
        assert updated.original_queue == "default"
        assert updated.finished_at is not None

    async def test_retryable_failure_stays_in_original_queue(self, dlq_queue):
        """A retryable failure does NOT move to DLQ — it stays pending."""
        job = await dlq_queue.enqueue(
            "my.task", queue_name="work", max_retries=3,
        )
        claimed = await dlq_queue.claim_job("w1", ["work"])
        assert claimed is not None

        await dlq_queue.fail_job(claimed, "w1", ValueError("retry me"), failure_kind=FailureKind.EXCEPTION)

        updated = await Job.query().filter(F("ulid") == job.ulid).first()
        assert updated.status == JobStatus.PENDING.value
        assert updated.queue_name == "work"
        assert updated.original_queue == ""
        assert updated.retry_count == 1

    async def test_no_dlq_stays_in_original_queue(self, queue):
        """Without DLQ configured, terminal failure stays in place."""
        job = await queue.enqueue("my.task", queue_name="default", max_retries=0)
        claimed = await queue.claim_job("w1", ["default"])
        assert claimed is not None

        await queue.fail_job(claimed, "w1", ValueError("boom"), failure_kind=FailureKind.EXCEPTION)

        updated = await Job.query().filter(F("ulid") == job.ulid).first()
        assert updated.status == JobStatus.FAILED.value
        assert updated.queue_name == "default"
        assert updated.original_queue == ""

    async def test_dlq_preserves_error_info(self, dlq_queue):
        """DLQ jobs retain last_error and last_failure_kind."""
        job = await dlq_queue.enqueue("my.task", max_retries=0)
        claimed = await dlq_queue.claim_job("w1", ["default"])
        await dlq_queue.fail_job(claimed, "w1", RuntimeError("details here"), failure_kind=FailureKind.EXCEPTION)

        updated = await Job.query().filter(F("ulid") == job.ulid).first()
        assert updated.last_error == "details here"
        assert updated.last_failure_kind == FailureKind.EXCEPTION.value

    async def test_retry_exhaustion_then_dlq(self, dlq_queue):
        """Job retries max_retries times, then final failure goes to DLQ."""
        job = await dlq_queue.enqueue("my.task", max_retries=2, retry_delay=1)

        for i in range(2):
            # Reset ETA so retry is immediately claimable
            await Job.query().filter(F("ulid") == job.ulid).update_one(eta=0)
            claimed = await dlq_queue.claim_job("w1", ["default"])
            assert claimed is not None, f"Claim #{i+1} returned None"
            await dlq_queue.fail_job(claimed, "w1", ValueError(f"fail #{i+1}"), failure_kind=FailureKind.EXCEPTION)

        # After 2 retries, retry_count=2 == max_retries=2, next failure is terminal
        await Job.query().filter(F("ulid") == job.ulid).update_one(eta=0)
        claimed = await dlq_queue.claim_job("w1", ["default"])
        assert claimed is not None
        await dlq_queue.fail_job(claimed, "w1", ValueError("final"), failure_kind=FailureKind.EXCEPTION)

        updated = await Job.query().filter(F("ulid") == job.ulid).first()
        assert updated.status == JobStatus.FAILED.value
        assert updated.queue_name == "dead_letters"
        assert updated.original_queue == "default"

    async def test_dlq_from_non_default_queue(self, dlq_queue):
        """DLQ correctly records original_queue for non-default queues."""
        job = await dlq_queue.enqueue("my.task", queue_name="emails", max_retries=0)
        claimed = await dlq_queue.claim_job("w1", ["emails"])
        await dlq_queue.fail_job(claimed, "w1", ValueError("send failed"), failure_kind=FailureKind.EXCEPTION)

        updated = await Job.query().filter(F("ulid") == job.ulid).first()
        assert updated.queue_name == "dead_letters"
        assert updated.original_queue == "emails"


# ---------------------------------------------------------------------------
# TestDLQReplay
# ---------------------------------------------------------------------------


class TestDLQReplay:
    """Replay DLQ jobs back to their original queue."""

    async def _make_dlq_job(self, dlq_queue, queue_name="default", task_name="my.task"):
        """Helper: enqueue → claim → fail terminal → returns the DLQ'd job."""
        job = await dlq_queue.enqueue(task_name, queue_name=queue_name, max_retries=0)
        claimed = await dlq_queue.claim_job("w1", [queue_name])
        await dlq_queue.fail_job(claimed, "w1", ValueError("boom"), failure_kind=FailureKind.EXCEPTION)
        return await Job.query().filter(F("ulid") == job.ulid).first()

    async def test_replay_resets_to_original_queue(self, dlq_queue):
        """Replay moves job back to original_queue with status=PENDING."""
        dlq_job = await self._make_dlq_job(dlq_queue, queue_name="work")
        assert dlq_job.queue_name == "dead_letters"
        assert dlq_job.original_queue == "work"

        updated = await dlq_queue.replay_job(dlq_job)
        assert updated is not None
        assert updated.status == JobStatus.PENDING.value
        assert updated.queue_name == "work"
        assert updated.original_queue == ""
        assert updated.retry_count == 0
        assert updated.finished_at is None

    async def test_replay_to_different_queue(self, dlq_queue):
        """Replay with queue_name override sends to a different queue."""
        dlq_job = await self._make_dlq_job(dlq_queue)

        updated = await dlq_queue.replay_job(dlq_job, queue_name="high_priority")
        assert updated is not None
        assert updated.queue_name == "high_priority"
        assert updated.original_queue == ""

    async def test_replay_non_failed_rejected(self, dlq_queue):
        """Replay on a non-FAILED job returns None."""
        job = await dlq_queue.enqueue("my.task")
        assert job.status == JobStatus.PENDING.value

        result = await dlq_queue.replay_job(job)
        assert result is None

    async def test_replay_clears_original_queue(self, dlq_queue):
        """After replay, original_queue is empty string."""
        dlq_job = await self._make_dlq_job(dlq_queue)
        updated = await dlq_queue.replay_job(dlq_job)
        assert updated.original_queue == ""

    async def test_replay_sets_eta_to_now(self, dlq_queue):
        """Replayed job is immediately eligible for claiming."""
        before = now_epoch()
        dlq_job = await self._make_dlq_job(dlq_queue)
        updated = await dlq_queue.replay_job(dlq_job)
        assert updated.eta >= before
        assert updated.eta <= now_epoch()

    async def test_replay_preserves_attempt_history(self, dlq_queue):
        """Replay does not delete attempt records."""
        from qler.models.attempt import JobAttempt

        dlq_job = await self._make_dlq_job(dlq_queue)
        attempts_before = await JobAttempt.query().filter(
            F("job_ulid") == dlq_job.ulid
        ).all()
        assert len(attempts_before) == 1

        await dlq_queue.replay_job(dlq_job)

        attempts_after = await JobAttempt.query().filter(
            F("job_ulid") == dlq_job.ulid
        ).all()
        assert len(attempts_after) == 1  # unchanged

    async def test_replay_defaults_to_default_queue(self, dlq_queue):
        """If original_queue is empty, replay falls back to 'default'."""
        # Manually create a FAILED job in DLQ with no original_queue
        job = await dlq_queue.enqueue("my.task", queue_name="dead_letters", max_retries=0)
        # Force to FAILED status (simulating direct placement)
        await Job.query().filter(F("ulid") == job.ulid).update_one(
            status=JobStatus.FAILED.value,
            finished_at=now_epoch(),
        )
        dlq_job = await Job.query().filter(F("ulid") == job.ulid).first()
        assert dlq_job.original_queue == ""

        updated = await dlq_queue.replay_job(dlq_job)
        assert updated is not None
        assert updated.queue_name == "default"


# ---------------------------------------------------------------------------
# TestDLQWithDependencies
# ---------------------------------------------------------------------------


class TestDLQWithDependencies:
    """DLQ + dependency cascade interactions."""

    async def test_dlq_still_cascades_cancel_dependents(self, dlq_queue):
        """Terminal failure + DLQ still cascade-cancels dependent jobs."""
        parent = await dlq_queue.enqueue("my.task", queue_name="default", max_retries=0)
        child = await dlq_queue.enqueue("my.child", depends_on=[parent.ulid])

        # Fail parent terminally
        claimed = await dlq_queue.claim_job("w1", ["default"])
        await dlq_queue.fail_job(claimed, "w1", ValueError("parent died"), failure_kind=FailureKind.EXCEPTION)

        # Parent should be in DLQ
        parent_updated = await Job.query().filter(F("ulid") == parent.ulid).first()
        assert parent_updated.queue_name == "dead_letters"
        assert parent_updated.status == JobStatus.FAILED.value

        # Child should be cascade-cancelled
        child_updated = await Job.query().filter(F("ulid") == child.ulid).first()
        assert child_updated.status == JobStatus.CANCELLED.value

    async def test_fan_in_parent_fails_to_dlq(self, dlq_queue):
        """Fan-in: parent with multiple deps fails → DLQ, all children cancelled."""
        dep_a = await dlq_queue.enqueue("my.dep", queue_name="default", max_retries=0)
        dep_b = await dlq_queue.enqueue("my.dep", queue_name="default", max_retries=0)
        fan_out = await dlq_queue.enqueue("my.fan", depends_on=[dep_a.ulid, dep_b.ulid])

        # Complete dep_a
        claimed_a = await dlq_queue.claim_job("w1", ["default"])
        await dlq_queue.complete_job(claimed_a, "w1", result="ok")

        # Fail dep_b terminally
        claimed_b = await dlq_queue.claim_job("w1", ["default"])
        await dlq_queue.fail_job(claimed_b, "w1", ValueError("dep_b died"), failure_kind=FailureKind.EXCEPTION)

        # dep_b goes to DLQ
        dep_b_updated = await Job.query().filter(F("ulid") == dep_b.ulid).first()
        assert dep_b_updated.queue_name == "dead_letters"

        # fan_out should be cascade-cancelled
        fan_updated = await Job.query().filter(F("ulid") == fan_out.ulid).first()
        assert fan_updated.status == JobStatus.CANCELLED.value


# ---------------------------------------------------------------------------
# TestDLQImmediate
# ---------------------------------------------------------------------------


class TestDLQImmediate:
    """Immediate mode + DLQ."""

    async def test_immediate_failure_goes_to_dlq(self, imm_queue):
        """In immediate mode, a failing task with max_retries=0 goes to DLQ."""
        boom_path = f"{__name__}.boom_task"
        wrapper = imm_queue._tasks[boom_path]
        job = await wrapper.enqueue()

        assert job.status == JobStatus.FAILED.value
        assert job.queue_name == "dead_letters"
        assert job.original_queue == "default"

    async def test_immediate_success_unaffected(self, imm_queue):
        """Successful immediate-mode jobs don't touch DLQ."""
        wrapper = imm_queue._tasks[f"{__name__}.add_task"]
        job = await wrapper.enqueue(1, 2)

        assert job.status == JobStatus.COMPLETED.value
        assert job.queue_name == "default"
        assert job.original_queue == ""

    async def test_immediate_without_dlq_stays_in_place(self, imm_queue_no_dlq):
        """Immediate mode without DLQ: failure stays in original queue."""
        boom_path = f"{__name__}.boom_task"
        wrapper = imm_queue_no_dlq._tasks[boom_path]
        job = await wrapper.enqueue()

        assert job.status == JobStatus.FAILED.value
        assert job.queue_name == "default"
        assert job.original_queue == ""


# ---------------------------------------------------------------------------
# TestDLQStress
# ---------------------------------------------------------------------------


class TestDLQStress:
    """Stress and edge-case scenarios."""

    async def test_multiple_jobs_to_dlq(self, dlq_queue):
        """Multiple jobs can fail to DLQ independently."""
        jobs = []
        for i in range(5):
            j = await dlq_queue.enqueue(f"task.{i}", queue_name="work", max_retries=0)
            jobs.append(j)

        for j in jobs:
            claimed = await dlq_queue.claim_job("w1", ["work"])
            assert claimed is not None
            await dlq_queue.fail_job(claimed, "w1", ValueError(f"fail {j.ulid}"), failure_kind=FailureKind.EXCEPTION)

        dlq_jobs = await Job.query().filter(
            (F("queue_name") == "dead_letters") & (F("status") == JobStatus.FAILED.value)
        ).all()
        assert len(dlq_jobs) == 5
        for dj in dlq_jobs:
            assert dj.original_queue == "work"

    async def test_replay_all_dlq_jobs(self, dlq_queue):
        """All DLQ jobs can be replayed back."""
        for i in range(3):
            j = await dlq_queue.enqueue(f"task.{i}", queue_name="default", max_retries=0)
            claimed = await dlq_queue.claim_job("w1", ["default"])
            await dlq_queue.fail_job(claimed, "w1", ValueError("fail"), failure_kind=FailureKind.EXCEPTION)

        dlq_jobs = await Job.query().filter(
            (F("queue_name") == "dead_letters") & (F("status") == JobStatus.FAILED.value)
        ).all()
        assert len(dlq_jobs) == 3

        for dj in dlq_jobs:
            updated = await dlq_queue.replay_job(dj)
            assert updated is not None
            assert updated.status == JobStatus.PENDING.value
            assert updated.queue_name == "default"

        remaining = await Job.query().filter(
            (F("queue_name") == "dead_letters") & (F("status") == JobStatus.FAILED.value)
        ).all()
        assert len(remaining) == 0

    async def test_dlq_jobs_not_claimed_by_normal_workers(self, dlq_queue):
        """Workers listening to 'default' do NOT claim DLQ jobs."""
        job = await dlq_queue.enqueue("my.task", queue_name="default", max_retries=0)
        claimed = await dlq_queue.claim_job("w1", ["default"])
        await dlq_queue.fail_job(claimed, "w1", ValueError("fail"), failure_kind=FailureKind.EXCEPTION)

        # Verify job is in DLQ
        updated = await Job.query().filter(F("ulid") == job.ulid).first()
        assert updated.queue_name == "dead_letters"

        # Try to claim from "default" — should get nothing
        result = await dlq_queue.claim_job("w2", ["default"])
        assert result is None

        # Try to claim from "dead_letters" — also nothing (status=FAILED, not PENDING)
        result = await dlq_queue.claim_job("w2", ["dead_letters"])
        assert result is None

    async def test_dlq_with_custom_name(self, db):
        """DLQ with a custom name works the same way."""
        q = Queue(db, dlq="my_failures")
        await q.init_db()

        job = await q.enqueue("my.task", max_retries=0)
        claimed = await q.claim_job("w1", ["default"])
        await q.fail_job(claimed, "w1", ValueError("oops"), failure_kind=FailureKind.EXCEPTION)

        updated = await Job.query().filter(F("ulid") == job.ulid).first()
        assert updated.queue_name == "my_failures"
        assert updated.original_queue == "default"

    async def test_replay_then_fail_again_to_dlq(self, dlq_queue):
        """A replayed job that fails again goes back to DLQ."""
        # First failure → DLQ
        job = await dlq_queue.enqueue("my.task", queue_name="default", max_retries=0)
        claimed = await dlq_queue.claim_job("w1", ["default"])
        await dlq_queue.fail_job(claimed, "w1", ValueError("first"), failure_kind=FailureKind.EXCEPTION)

        dlq_job = await Job.query().filter(F("ulid") == job.ulid).first()
        assert dlq_job.queue_name == "dead_letters"

        # Replay
        replayed = await dlq_queue.replay_job(dlq_job)
        assert replayed.queue_name == "default"
        assert replayed.status == JobStatus.PENDING.value

        # Second failure → DLQ again
        claimed2 = await dlq_queue.claim_job("w1", ["default"])
        assert claimed2 is not None
        assert claimed2.ulid == job.ulid
        await dlq_queue.fail_job(claimed2, "w1", ValueError("second"), failure_kind=FailureKind.EXCEPTION)

        final = await Job.query().filter(F("ulid") == job.ulid).first()
        assert final.queue_name == "dead_letters"
        assert final.original_queue == "default"
        assert final.last_error == "second"
