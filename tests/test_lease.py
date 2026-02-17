"""Tests for lease recovery and renewal mechanics — no Worker needed."""

import re

import pytest

from qler._time import now_epoch
from qler.enums import AttemptStatus, FailureKind, JobStatus
from qler.models.attempt import JobAttempt
from qler.models.job import Job
from sqler import F

_ULID_PATTERN = re.compile(r"^[0-9A-Z]{26}$")


async def _make_running_job(queue, *, lease_offset: int = 300, worker_id: str = "worker-1"):
    """Create a job and claim it, returning the claimed job.

    lease_offset: seconds from now for lease_expires_at.
    Negative = already expired.
    """
    job = await queue.enqueue("my_task", lease_duration=abs(lease_offset))
    claimed = await queue.claim_job(worker_id, ["default"])
    assert claimed is not None

    # Force lease_expires_at to the desired value
    now = now_epoch()
    target_expiry = now + lease_offset
    await Job.query().filter(F("ulid") == claimed.ulid).update_one(
        lease_expires_at=target_expiry,
    )
    # Also update the attempt's lease_expires_at for consistency
    await JobAttempt.query().filter(F("job_ulid") == claimed.ulid).update_one(
        lease_expires_at=target_expiry,
    )
    await claimed.refresh()
    return claimed


class TestRecoverExpiredLeases:
    """Queue.recover_expired_leases() tests."""

    async def test_recover_expired_jobs(self, queue):
        """Expired RUNNING → PENDING, worker_id cleared."""
        job = await _make_running_job(queue, lease_offset=-10)
        assert job.status == "running"
        assert job.worker_id == "worker-1"

        count = await queue.recover_expired_leases()
        assert count == 1

        await job.refresh()
        assert job.status == "pending"
        assert job.worker_id == ""
        assert job.lease_expires_at is None

    async def test_recover_terminalizes_attempt(self, queue):
        """Attempt gets LEASE_EXPIRED status."""
        job = await _make_running_job(queue, lease_offset=-10)

        await queue.recover_expired_leases()

        attempts = await JobAttempt.query().filter(
            F("job_ulid") == job.ulid
        ).all()
        assert len(attempts) == 1
        assert attempts[0].status == AttemptStatus.LEASE_EXPIRED.value
        assert attempts[0].failure_kind == FailureKind.LEASE_EXPIRED.value
        assert attempts[0].error == "Lease expired"
        assert attempts[0].finished_at is not None

    async def test_recover_does_not_increment_retry_count(self, queue):
        """Lease expiry is infra failure — retry_count stays unchanged."""
        job = await _make_running_job(queue, lease_offset=-10)
        assert job.retry_count == 0

        await queue.recover_expired_leases()
        await job.refresh()
        assert job.retry_count == 0

    async def test_recover_max_per_tick(self, queue):
        """Respects max_per_tick limit."""
        for i in range(5):
            await _make_running_job(queue, lease_offset=-10, worker_id=f"w-{i}")

        count = await queue.recover_expired_leases(max_per_tick=3)
        assert count == 3

        # Remaining 2 still running
        remaining = await Job.query().filter(
            F("status") == JobStatus.RUNNING.value
        ).all()
        assert len(remaining) == 2

    async def test_recover_no_expired(self, queue):
        """Returns 0 when no expired leases exist."""
        count = await queue.recover_expired_leases()
        assert count == 0

    async def test_recover_no_expired_with_active_leases(self, queue):
        """Active (future) leases are NOT recovered."""
        job = await _make_running_job(queue, lease_offset=300)
        assert job.status == "running"

        count = await queue.recover_expired_leases()
        assert count == 0

        await job.refresh()
        assert job.status == "running"  # untouched

    async def test_recover_multiple_expired(self, queue):
        """All expired jobs recovered in one call."""
        jobs = []
        for i in range(3):
            j = await _make_running_job(queue, lease_offset=-10, worker_id=f"w-{i}")
            jobs.append(j)

        count = await queue.recover_expired_leases()
        assert count == 3

        for j in jobs:
            await j.refresh()
            assert j.status == "pending"
            assert j.worker_id == ""


class TestRenewLease:
    """Job.renew_lease() tests."""

    async def test_renew_lease_extends(self, queue):
        """lease_expires_at is updated to now + lease_duration."""
        # Use a short lease that's about to expire — renewal should push it out
        job = await _make_running_job(queue, lease_offset=2)
        before = now_epoch()

        result = await job.renew_lease()
        assert result is True
        # New expiry should be at least now + lease_duration (2s)
        assert job.lease_expires_at >= before + job.lease_duration

    async def test_renew_lease_custom_duration(self, queue):
        """Custom duration works."""
        job = await _make_running_job(queue, lease_offset=60)
        before = now_epoch()

        result = await job.renew_lease(duration=600)
        assert result is True
        assert job.lease_expires_at >= before + 600

    async def test_renew_lease_lost_ownership(self, queue):
        """Wrong worker → False."""
        job = await _make_running_job(queue, lease_offset=60)
        # Change worker_id to simulate ownership loss
        await Job.query().filter(F("ulid") == job.ulid).update_one(
            worker_id="other-worker",
        )
        await job.refresh()
        job.worker_id = "worker-1"  # pretend we still think we own it

        result = await job.renew_lease()
        assert result is False

    async def test_renew_lease_non_running(self, queue):
        """Completed job → False."""
        job = await _make_running_job(queue, lease_offset=60)
        await queue.complete_job(job, "worker-1", result="done")

        # Try to renew a completed job
        result = await job.renew_lease()
        assert result is False
