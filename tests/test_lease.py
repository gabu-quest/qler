"""Tests for lease recovery and renewal mechanics, and the worker's renewal loop."""

import asyncio
import logging
import re
import sqlite3

import pytest
from conftest import make_worker
from sqler import F

from qler._time import now_epoch
from qler.enums import AttemptStatus, FailureKind, JobStatus
from qler.models.job import Job
from qler.queue import Queue
from qler.task import task

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
    await queue.jobs.filter(F("ulid") == claimed.ulid).update_one(
        lease_expires_at=target_expiry,
    )
    # Also update the attempt's lease_expires_at for consistency
    await queue.attempts.filter(F("job_ulid") == claimed.ulid).update_one(
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
        """Attempt gets LEASE_EXPIRED status with bounded timestamp."""
        job = await _make_running_job(queue, lease_offset=-10)
        before = now_epoch()

        await queue.recover_expired_leases()
        after = now_epoch()

        attempts = await queue.attempts.filter(F("job_ulid") == job.ulid).all()
        assert len(attempts) == 1
        assert attempts[0].status == AttemptStatus.LEASE_EXPIRED.value
        assert attempts[0].failure_kind == FailureKind.LEASE_EXPIRED.value
        assert attempts[0].error == "Lease expired"
        assert before <= attempts[0].finished_at <= after

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
        remaining = await queue.jobs.filter(F("status") == JobStatus.RUNNING.value).all()
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

    async def test_recover_expired_lease_resets_progress(self, queue):
        """Progress fields are cleared when a lease-expired job is recovered."""
        job = await _make_running_job(queue, lease_offset=-10)
        # Set progress on the running job
        await queue.jobs.filter(F("ulid") == job.ulid).update_one(
            progress=75, progress_message="three quarters"
        )
        recovered = await queue.recover_expired_leases()
        assert recovered == 1
        refreshed = await queue.jobs.filter(F("ulid") == job.ulid).first()
        assert refreshed.status == JobStatus.PENDING.value
        assert refreshed.progress is None
        assert refreshed.progress_message == ""

    async def test_reset_and_attempt_end_are_one_write(self, queue, raise_on_nth_update, caplog):
        """The attempt's end fails once: nothing is reset, no event; the next call recovers it."""
        job = await _make_running_job(queue, lease_offset=-10)

        raise_on_nth_update(
            lambda fields: fields.get("status") == AttemptStatus.LEASE_EXPIRED.value
        )

        def recovered_events():
            return [
                r
                for r in caplog.records
                if r.name == "qler.lifecycle" and r.event == "job.lease_recovered"
            ]

        with caplog.at_level(logging.INFO, logger="qler.lifecycle"):
            with pytest.raises(sqlite3.OperationalError, match="database is locked"):
                await queue.recover_expired_leases()

            stored = await queue.jobs.filter(F("ulid") == job.ulid).first()
            assert stored.status == JobStatus.RUNNING.value
            assert stored.worker_id == "worker-1"
            assert stored.lease_expires_at is not None
            attempts = await queue.attempts.filter(F("job_ulid") == job.ulid).all()
            assert [a.status for a in attempts] == [AttemptStatus.RUNNING.value]
            assert recovered_events() == []

            assert await queue.recover_expired_leases() == 1

            stored = await queue.jobs.filter(F("ulid") == job.ulid).first()
            assert stored.status == JobStatus.PENDING.value
            attempts = await queue.attempts.filter(F("job_ulid") == job.ulid).all()
            assert [a.status for a in attempts] == [AttemptStatus.LEASE_EXPIRED.value]
            assert len(recovered_events()) == 1


class TestRenewLease:
    """Job.renew_lease() tests."""

    async def test_renew_lease_extends(self, queue):
        """lease_expires_at is updated to now + lease_duration with bounded range."""
        # Use a short lease that's about to expire — renewal should push it out
        job = await _make_running_job(queue, lease_offset=2)
        before = now_epoch()

        result = await job.renew_lease()
        after = now_epoch()
        assert result is True
        # Verify via independent DB read (not trusting _sync_from alone)
        await job.refresh()
        assert job.lease_expires_at >= before + job.lease_duration
        assert job.lease_expires_at <= after + job.lease_duration + 1

    async def test_renew_lease_custom_duration(self, queue):
        """Custom duration works with bounded range."""
        job = await _make_running_job(queue, lease_offset=60)
        before = now_epoch()

        result = await job.renew_lease(duration=600)
        after = now_epoch()
        assert result is True
        # Verify via independent DB read
        await job.refresh()
        assert job.lease_expires_at >= before + 600
        assert job.lease_expires_at <= after + 600 + 1

    async def test_renew_lease_lost_ownership(self, queue):
        """Wrong worker → False."""
        job = await _make_running_job(queue, lease_offset=60)
        # Change worker_id to simulate ownership loss
        await queue.jobs.filter(F("ulid") == job.ulid).update_one(
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


# ---------------------------------------------------------------------------
# Worker._lease_renewal_loop: a running job keeps its lease
# ---------------------------------------------------------------------------

_renewal_release: asyncio.Event | None = None
_renewal_started: asyncio.Event | None = None
_renewal_runs = 0


async def _held_until_released():
    """First run waits for the test's release; a later run returns at once."""
    global _renewal_runs
    _renewal_runs += 1
    if _renewal_runs == 1:
        _renewal_started.set()
        await _renewal_release.wait()
        return "first run"
    return "second run"


async def _runs_for(seconds):
    """First run takes `seconds`; a later run (a second worker's) returns at once."""
    global _renewal_runs
    _renewal_runs += 1
    if _renewal_runs == 1:
        _renewal_started.set()
        await asyncio.sleep(seconds)
        return "first run"
    return "second run"


async def _stop(worker, worker_task):
    worker._running = False
    await asyncio.wait_for(worker_task, timeout=10.0)


class TestWorkerLeaseRenewal:
    """The worker renews every running job's lease before it can end."""

    @pytest.fixture(autouse=True)
    def _reset(self):
        global _renewal_release, _renewal_started, _renewal_runs
        _renewal_release = asyncio.Event()
        _renewal_started = asyncio.Event()
        _renewal_runs = 0
        yield

    async def test_renewal_error_is_logged_and_next_pass_renews(self, db, monkeypatch, caplog):
        """One locked-database error is logged with the job id; the next pass renews."""
        queue = Queue(db, default_lease_duration=2)
        await queue.init_db()
        wrapped = task(queue)(_held_until_released)
        job = await wrapped.enqueue()

        real_renew = Job.renew_lease
        calls = []
        renewed_after_error = asyncio.Event()
        lease_after = []

        async def renew_failing_once(self, duration=None):
            calls.append(self.ulid)
            if len(calls) == 1:
                raise sqlite3.OperationalError("database is locked")
            result = await real_renew(self, duration)
            lease_after.append(self.lease_expires_at)
            renewed_after_error.set()
            return result

        monkeypatch.setattr(Job, "renew_lease", renew_failing_once)

        w = make_worker(queue)
        worker_task = asyncio.create_task(w.run())
        try:
            await asyncio.wait_for(_renewal_started.wait(), timeout=5.0)
            claimed = await queue.jobs.filter(F("ulid") == job.ulid).first()
            claim_lease = claimed.lease_expires_at
            with caplog.at_level("WARNING", logger="qler.worker"):
                await asyncio.wait_for(renewed_after_error.wait(), timeout=5.0)
            row = await queue.jobs.filter(F("ulid") == job.ulid).first()
        finally:
            _renewal_release.set()
            await _stop(w, worker_task)

        assert calls[:2] == [job.ulid, job.ulid]
        assert row.status == JobStatus.RUNNING.value
        assert row.lease_expires_at == lease_after[0]
        assert row.lease_expires_at > claim_lease
        failures = [
            r
            for r in caplog.records
            if r.name == "qler.worker" and "Lease renewal failed" in r.getMessage()
        ]
        assert len(failures) == 1
        assert job.ulid in failures[0].getMessage()
        assert "database is locked" in failures[0].getMessage()

    async def test_running_worker_recovers_a_job_whose_lease_ended(
        self, queue, monkeypatch, caplog
    ):
        """The loop, not the start-up pass, resets a job claimed by a worker that is gone."""
        job = await queue.enqueue("my_task", queue_name="orphans", lease_duration=2)
        claimed = await queue.claim_job("ghost-worker", ["orphans"])
        assert claimed is not None and claimed.ulid == job.ulid

        real_recover = Queue.recover_expired_leases
        results = []
        recovered_by_loop = asyncio.Event()

        async def recover_and_note(self, **kwargs):
            count = await real_recover(self, **kwargs)
            results.append(count)
            if count:
                recovered_by_loop.set()
            return count

        monkeypatch.setattr(Queue, "recover_expired_leases", recover_and_note)

        w = make_worker(queue, lease_recovery_interval=0.1)
        with caplog.at_level(logging.INFO, logger="qler.lifecycle"):
            worker_task = asyncio.create_task(w.run())
            try:
                await asyncio.wait_for(recovered_by_loop.wait(), timeout=10.0)
            finally:
                await _stop(w, worker_task)

        # The start-up pass found nothing (the lease was still running); one loop pass recovered it.
        assert results[0] == 0
        assert [c for c in results if c] == [1]
        row = await queue.jobs.filter(F("ulid") == job.ulid).first()
        assert row.status == JobStatus.PENDING.value
        assert row.worker_id == ""
        attempts = await queue.attempts.filter(F("job_ulid") == job.ulid).all()
        assert [a.status for a in attempts] == [AttemptStatus.LEASE_EXPIRED.value]
        events = [
            r
            for r in caplog.records
            if r.name == "qler.lifecycle" and r.event == "job.lease_recovered"
        ]
        assert [r.job_id for r in events] == [job.ulid]

    async def test_short_lease_job_is_never_recovered_while_running(self, db):
        """A job whose lease is far shorter than the queue default keeps it.

        The claim leases for the queue default, so the queue starts with a
        default equal to the job's 2 s lease and is raised to 60 s once the job
        runs: from there the job lives on its own lease while the loop's old
        wake-up (default / 3 = 20 s) is ten lease lengths away.
        """
        q = Queue(db, default_lease_duration=2, default_max_retries=3)
        await q.init_db()
        wrapped = task(q)(_runs_for)
        job = await q.enqueue(wrapped.task_path, args=(6.0,), lease_duration=2)

        w1 = make_worker(q)
        w1_task = asyncio.create_task(w1.run())
        await asyncio.wait_for(_renewal_started.wait(), timeout=5.0)
        q.default_lease_duration = 60
        w2 = make_worker(q, lease_recovery_interval=0.1)
        w2_task = asyncio.create_task(w2.run())
        try:
            result = await job.wait(timeout=15.0, poll_interval=0.05)
        finally:
            await _stop(w2, w2_task)
            await _stop(w1, w1_task)

        assert result.status == JobStatus.COMPLETED.value
        assert result.result == "first run"
        assert _renewal_runs == 1
        attempts = await q.attempts.filter(F("job_ulid") == job.ulid).all()
        assert [(a.status, a.worker_id) for a in attempts] == [
            (AttemptStatus.COMPLETED.value, w1.worker_id),
        ]
