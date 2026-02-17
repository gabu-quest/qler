"""End-to-end lifecycle tests — full job flows from enqueue to terminal state."""

import asyncio

import pytest

from qler._time import now_epoch
from qler.enums import AttemptStatus, FailureKind, JobStatus
from qler.exceptions import JobCancelledError, JobFailedError
from qler.models.attempt import JobAttempt
from qler.models.job import Job
from qler.queue import Queue
from qler.task import task
from sqler import F


# Module-level task function for decorator tests
async def process_order(order_id, notify=False):
    return {"order_id": order_id, "notified": notify}


class TestHappyPath:
    """enqueue → claim → complete with attempt history."""

    async def test_full_lifecycle(self, queue):
        # Enqueue
        job = await queue.enqueue(
            "my_module.process",
            args=(42,),
            kwargs={"verbose": True},
            queue_name="default",
            priority=5,
        )
        assert job.status == "pending"
        assert job.attempts == 0

        # Claim
        claimed = await queue.claim_job("worker-1", ["default"])
        assert claimed is not None
        assert claimed.ulid == job.ulid
        assert claimed.status == "running"
        assert claimed.attempts == 1
        assert claimed.worker_id == "worker-1"

        # Complete
        await queue.complete_job(claimed, "worker-1", result={"processed": True})
        await claimed.refresh()
        assert claimed.status == "completed"
        assert claimed.result == {"processed": True}
        assert claimed.finished_at is not None
        assert claimed.worker_id == ""
        assert claimed.lease_expires_at is None

        # Verify attempt history
        attempts = await JobAttempt.query().filter(
            F("job_ulid") == job.ulid
        ).all()
        assert len(attempts) == 1
        assert attempts[0].status == "completed"
        assert attempts[0].attempt_number == 1
        assert attempts[0].worker_id == "worker-1"


class TestRetryFlow:
    """enqueue → claim → fail+retry → claim → complete."""

    async def test_fail_retry_complete(self, queue):
        job = await queue.enqueue("my_task", max_retries=2, retry_delay=1)

        # First claim + fail
        claimed = await queue.claim_job("worker-1", ["default"])
        assert claimed is not None
        assert claimed.attempts == 1
        await queue.fail_job(claimed, "worker-1", RuntimeError("transient error"))

        await claimed.refresh()
        assert claimed.status == "pending"
        assert claimed.retry_count == 1

        # Reset ETA to now via atomic update so we can reclaim
        await Job.query().filter(F("ulid") == claimed.ulid).update_one(eta=now_epoch())

        # Second claim + complete
        reclaimed = await queue.claim_job("worker-2", ["default"])
        assert reclaimed is not None
        assert reclaimed.ulid == job.ulid
        assert reclaimed.attempts == 2
        await queue.complete_job(reclaimed, "worker-2", result="done")

        await reclaimed.refresh()
        assert reclaimed.status == "completed"
        assert reclaimed.result == "done"

        # Verify attempt history — 2 attempts
        attempts = await JobAttempt.query().filter(
            F("job_ulid") == job.ulid
        ).order_by("ulid").all()
        assert len(attempts) == 2
        assert attempts[0].status == "failed"
        assert attempts[0].worker_id == "worker-1"
        assert attempts[1].status == "completed"
        assert attempts[1].worker_id == "worker-2"


class TestExhaustedRetries:
    """enqueue → claim → fail exhausted → FAILED terminal."""

    async def test_exhausted_retries(self, queue):
        job = await queue.enqueue("my_task", max_retries=0)

        claimed = await queue.claim_job("worker-1", ["default"])
        assert claimed is not None
        await queue.fail_job(claimed, "worker-1", ValueError("permanent"))

        await claimed.refresh()
        assert claimed.status == "failed"
        assert claimed.finished_at is not None
        assert claimed.last_error == "permanent"
        assert claimed.last_failure_kind == "exception"


class TestCancel:
    """Cancel lifecycle tests."""

    async def test_cancel_pending(self, queue):
        job = await queue.enqueue("my_task")
        assert job.status == "pending"

        result = await job.cancel()
        assert result is True
        assert job.status == "cancelled"
        assert job.finished_at is not None

    async def test_cancel_running_fails(self, queue):
        await queue.enqueue("my_task")
        job = await queue.claim_job("worker-1", ["default"])
        assert job is not None
        assert job.status == "running"

        result = await job.cancel()
        assert result is False
        await job.refresh()
        assert job.status == "running"  # unchanged


class TestRetryMethod:
    """Job.retry() lifecycle tests."""

    async def test_retry_failed_job(self, queue):
        job = await queue.enqueue("my_task", max_retries=0)

        claimed = await queue.claim_job("worker-1", ["default"])
        await queue.fail_job(claimed, "worker-1", ValueError("err"))
        await claimed.refresh()
        assert claimed.status == "failed"

        result = await claimed.retry()
        assert result is True
        assert claimed.status == "pending"
        assert claimed.retry_count == 0
        assert claimed.finished_at is None

    async def test_retry_non_failed_returns_false(self, queue):
        job = await queue.enqueue("my_task")
        result = await job.retry()
        assert result is False  # Can't retry a pending job


class TestWait:
    """Job.wait() lifecycle tests."""

    async def test_wait_completed(self, queue):
        job = await queue.enqueue("my_task")
        claimed = await queue.claim_job("worker-1", ["default"])

        async def complete_later():
            await asyncio.sleep(0.05)
            await queue.complete_job(claimed, "worker-1", result="ok")

        asyncio.create_task(complete_later())
        result = await job.wait(timeout=2.0, poll_interval=0.02)
        assert result.status == "completed"

    async def test_wait_failed_raises(self, queue):
        job = await queue.enqueue("my_task", max_retries=0)
        claimed = await queue.claim_job("worker-1", ["default"])

        async def fail_later():
            await asyncio.sleep(0.05)
            await queue.fail_job(claimed, "worker-1", ValueError("boom"))

        asyncio.create_task(fail_later())
        with pytest.raises(JobFailedError, match="failed"):
            await job.wait(timeout=2.0, poll_interval=0.02)

    async def test_wait_cancelled_raises(self, queue):
        job = await queue.enqueue("my_task")

        async def cancel_later():
            await asyncio.sleep(0.05)
            await job.cancel()

        asyncio.create_task(cancel_later())
        with pytest.raises(JobCancelledError, match="cancelled"):
            await job.wait(timeout=2.0, poll_interval=0.02)

    async def test_wait_timeout(self, queue):
        job = await queue.enqueue("my_task")
        with pytest.raises(TimeoutError, match="did not complete"):
            await job.wait(timeout=0.1, poll_interval=0.02)


class TestAttemptHistory:
    """Verify attempt records accumulate correctly across claim/fail cycles."""

    async def test_multiple_attempts_tracked(self, queue):
        job = await queue.enqueue("my_task", max_retries=2, retry_delay=1)

        # Attempt 1: fail
        claimed = await queue.claim_job("worker-1", ["default"])
        await queue.fail_job(claimed, "worker-1", RuntimeError("err-1"))
        # Reset ETA to now via atomic update
        await Job.query().filter(F("ulid") == job.ulid).update_one(eta=now_epoch())

        # Attempt 2: fail
        claimed = await queue.claim_job("worker-2", ["default"])
        await queue.fail_job(claimed, "worker-2", RuntimeError("err-2"))
        await Job.query().filter(F("ulid") == job.ulid).update_one(eta=now_epoch())

        # Attempt 3: complete
        claimed = await queue.claim_job("worker-3", ["default"])
        await queue.complete_job(claimed, "worker-3", result="success")

        # Verify 3 attempt records
        attempts = await JobAttempt.query().filter(
            F("job_ulid") == job.ulid
        ).order_by("ulid").all()
        assert len(attempts) == 3

        assert attempts[0].status == "failed"
        assert attempts[0].worker_id == "worker-1"
        assert attempts[0].error == "err-1"
        assert attempts[0].attempt_number == 1

        assert attempts[1].status == "failed"
        assert attempts[1].worker_id == "worker-2"
        assert attempts[1].error == "err-2"
        assert attempts[1].attempt_number == 2

        assert attempts[2].status == "completed"
        assert attempts[2].worker_id == "worker-3"
        assert attempts[2].attempt_number == 3

    async def test_attempts_vs_retry_count(self, queue):
        """attempts counts total claims, retry_count counts retries after failure."""
        job = await queue.enqueue("my_task", max_retries=3, retry_delay=1)

        # Attempt 1: claim + fail
        claimed = await queue.claim_job("w", ["default"])
        await queue.fail_job(claimed, "w", RuntimeError("e"))
        await claimed.refresh()
        assert claimed.attempts == 1
        assert claimed.retry_count == 1

        await Job.query().filter(F("ulid") == claimed.ulid).update_one(eta=now_epoch())

        # Attempt 2: claim + fail
        claimed = await queue.claim_job("w", ["default"])
        await queue.fail_job(claimed, "w", RuntimeError("e"))
        await claimed.refresh()
        assert claimed.attempts == 2
        assert claimed.retry_count == 2

        await Job.query().filter(F("ulid") == claimed.ulid).update_one(eta=now_epoch())

        # Attempt 3: claim + complete
        claimed = await queue.claim_job("w", ["default"])
        assert claimed.attempts == 3
        await queue.complete_job(claimed, "w", result="ok")
        await claimed.refresh()
        assert claimed.attempts == 3
        assert claimed.retry_count == 2  # unchanged on complete


class TestIdempotencyLifecycle:
    """Verify idempotency key deduplication in lifecycle context."""

    async def test_dedup_returns_same_job(self, queue):
        job1 = await queue.enqueue("my_task", args=(1,), idempotency_key="charge-123")
        job2 = await queue.enqueue("my_task", args=(2,), idempotency_key="charge-123")
        assert job1.ulid == job2.ulid
        # Payload from first enqueue is preserved
        assert job1.payload == {"args": [1], "kwargs": {}}

    async def test_dedup_after_cancel_creates_new(self, queue):
        job1 = await queue.enqueue("my_task", idempotency_key="charge-456")
        await job1.cancel()

        job2 = await queue.enqueue("my_task", idempotency_key="charge-456")
        assert job1.ulid != job2.ulid
        assert job2.status == "pending"


class TestTaskDecoratorLifecycle:
    """Verify @task + Queue integration end-to-end."""

    async def test_task_enqueue_claim_complete(self, queue):
        wrapped = task(queue)(process_order)

        # Enqueue via decorator
        job = await wrapped.enqueue(42, notify=True)
        assert job.task == wrapped.task_path
        assert job.payload == {"args": [42], "kwargs": {"notify": True}}

        # Claim
        claimed = await queue.claim_job("worker-1", ["default"])
        assert claimed is not None
        assert claimed.ulid == job.ulid

        # Complete
        await queue.complete_job(claimed, "worker-1", result={"order_id": 42})
        await claimed.refresh()
        assert claimed.status == "completed"
        assert claimed.result == {"order_id": 42}
