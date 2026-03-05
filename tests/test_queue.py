"""Tests for Queue — init, enqueue, claim, complete, fail operations."""

import re
import time
from unittest.mock import patch

import pytest
from qler._time import now_epoch
from qler.enums import FailureKind, JobStatus
from qler.exceptions import (
    ConfigurationError,
    DependencyError,
    PayloadNotSerializableError,
    PayloadTooLargeError,
)
from qler.models.attempt import JobAttempt
from qler.models.job import Job
from qler.queue import Queue, _calculate_retry_eta
from sqler import F

_ULID_PATTERN = re.compile(r"^[0-9A-Z]{26}$")


class TestQueueInit:
    """Verify Queue initialization and configuration."""

    async def test_init_with_db_instance(self, db):
        q = Queue(db)
        assert q._owns_db is False

    def test_init_with_path(self):
        q = Queue("/tmp/test_qler.db")
        assert q._owns_db is True
        assert q._db_path == "/tmp/test_qler.db"

    def test_default_config(self, db):
        q = Queue(db)
        assert q.default_lease_duration == 300
        assert q.default_max_retries == 0
        assert q.default_retry_delay == 60
        assert q.max_payload_size == 1_000_000

    def test_custom_config(self, db):
        q = Queue(
            db,
            default_lease_duration=600,
            default_max_retries=3,
            default_retry_delay=120,
            max_payload_size=500_000,
        )
        assert q.default_lease_duration == 600
        assert q.default_max_retries == 3
        assert q.default_retry_delay == 120
        assert q.max_payload_size == 500_000

    def test_rejects_retry_delay_zero(self, db):
        with pytest.raises(ConfigurationError, match="default_retry_delay must be >= 1"):
            Queue(db, default_retry_delay=0)

    async def test_init_db_idempotent(self, db):
        q = Queue(db)
        await q.init_db()
        await q.init_db()  # Should not raise


class TestEnqueue:
    """Verify Queue.enqueue creates jobs correctly."""

    async def test_basic_enqueue(self, queue):
        job = await queue.enqueue("my_module.my_task")
        assert _ULID_PATTERN.match(job.ulid), f"Expected ULID format, got {job.ulid!r}"
        assert job.status == "pending"
        assert job.task == "my_module.my_task"
        assert job.queue_name == "default"
        assert job.priority == 0

    async def test_enqueue_with_args(self, queue):
        job = await queue.enqueue("my_task", args=(1, 2), kwargs={"x": 3})
        assert job.payload == {"args": [1, 2], "kwargs": {"x": 3}}

    async def test_enqueue_with_delay(self, queue):
        before = now_epoch()
        job = await queue.enqueue("my_task", delay=60)
        after = now_epoch()
        assert before + 60 <= job.eta <= after + 60

    async def test_enqueue_with_eta(self, queue):
        eta = now_epoch() + 3600
        job = await queue.enqueue("my_task", eta=eta)
        assert job.eta == eta

    async def test_enqueue_eta_overrides_delay(self, queue):
        eta = now_epoch() + 3600
        job = await queue.enqueue("my_task", delay=60, eta=eta)
        assert job.eta == eta

    async def test_enqueue_with_priority(self, queue):
        job = await queue.enqueue("my_task", priority=10)
        assert job.priority == 10

    async def test_enqueue_with_queue_name(self, queue):
        job = await queue.enqueue("my_task", queue_name="emails")
        assert job.queue_name == "emails"

    async def test_enqueue_with_correlation_id(self, queue):
        job = await queue.enqueue("my_task", correlation_id="req-123")
        assert job.correlation_id == "req-123"

    async def test_enqueue_with_max_retries(self, queue):
        job = await queue.enqueue("my_task", max_retries=3)
        assert job.max_retries == 3

    async def test_enqueue_with_retry_delay(self, queue):
        job = await queue.enqueue("my_task", retry_delay=120)
        assert job.retry_delay == 120

    async def test_enqueue_sets_created_at(self, queue):
        before = now_epoch()
        job = await queue.enqueue("my_task")
        after = now_epoch()
        assert before <= job.created_at <= after
        assert before <= job.updated_at <= after


class TestEnqueueIdempotency:
    """Verify idempotency key deduplication."""

    async def test_idempotency_returns_existing(self, queue):
        job1 = await queue.enqueue("my_task", idempotency_key="key-1")
        job2 = await queue.enqueue("my_task", idempotency_key="key-1")
        assert job1.ulid == job2.ulid

    async def test_idempotency_different_keys(self, queue):
        job1 = await queue.enqueue("my_task", idempotency_key="key-1")
        job2 = await queue.enqueue("my_task", idempotency_key="key-2")
        assert job1.ulid != job2.ulid

    async def test_idempotency_ignores_cancelled(self, queue):
        job1 = await queue.enqueue("my_task", idempotency_key="key-cancel")
        await job1.cancel()
        job2 = await queue.enqueue("my_task", idempotency_key="key-cancel")
        assert job1.ulid != job2.ulid


class TestEnqueuePayloadValidation:
    """Verify payload validation on enqueue."""

    async def test_rejects_non_serializable(self, queue):
        with pytest.raises(PayloadNotSerializableError, match="not JSON-serializable"):
            await queue.enqueue("my_task", args=(object(),))

    async def test_rejects_oversized_payload(self, queue):
        queue.max_payload_size = 100
        with pytest.raises(PayloadTooLargeError, match="exceeds max"):
            await queue.enqueue("my_task", args=("x" * 200,))


class TestClaim:
    """Verify Queue.claim_job atomic claiming."""

    async def test_claim_returns_job(self, queue):
        await queue.enqueue("my_task", queue_name="default")
        job = await queue.claim_job("worker-1", ["default"])
        assert job is not None
        assert job.status == "running"
        assert job.worker_id == "worker-1"

    async def test_claim_empty_queue(self, queue):
        job = await queue.claim_job("worker-1", ["default"])
        assert job is None

    async def test_claim_respects_queue_filter(self, queue):
        await queue.enqueue("my_task", queue_name="emails")
        job = await queue.claim_job("worker-1", ["notifications"])
        assert job is None

    async def test_claim_correct_queue(self, queue):
        await queue.enqueue("my_task", queue_name="emails")
        job = await queue.claim_job("worker-1", ["emails"])
        assert job is not None
        assert job.queue_name == "emails"

    async def test_claim_skips_future_eta(self, queue):
        await queue.enqueue("my_task", eta=now_epoch() + 3600)
        job = await queue.claim_job("worker-1", ["default"])
        assert job is None

    async def test_claim_priority_ordering(self, queue):
        await queue.enqueue("low", priority=1)
        await queue.enqueue("high", priority=10)
        await queue.enqueue("medium", priority=5)

        job = await queue.claim_job("worker-1", ["default"])
        assert job is not None
        assert job.task == "high"
        assert job.priority == 10

    async def test_claim_increments_attempts(self, queue):
        await queue.enqueue("my_task")
        job = await queue.claim_job("worker-1", ["default"])
        assert job is not None
        assert job.attempts == 1

    async def test_claim_sets_lease_expires(self, queue):
        before = now_epoch()
        await queue.enqueue("my_task")
        job = await queue.claim_job("worker-1", ["default"])
        after = now_epoch()
        assert job is not None
        expected_min = before + queue.default_lease_duration
        expected_max = after + queue.default_lease_duration
        assert expected_min <= job.lease_expires_at <= expected_max

    async def test_claim_creates_attempt_record(self, queue):
        await queue.enqueue("my_task")
        job = await queue.claim_job("worker-1", ["default"])
        assert job is not None

        attempts = await JobAttempt.query().filter(
            F("job_ulid") == job.ulid
        ).all()
        assert len(attempts) == 1
        assert attempts[0].status == "running"
        assert attempts[0].worker_id == "worker-1"
        assert attempts[0].attempt_number == 1

    async def test_claim_rejects_empty_queues_list(self, queue):
        with pytest.raises(ConfigurationError, match="queues list must not be empty"):
            await queue.claim_job("worker-1", [])

    async def test_claim_sets_last_attempt_id(self, queue):
        await queue.enqueue("my_task")
        job = await queue.claim_job("worker-1", ["default"])
        assert job is not None
        assert _ULID_PATTERN.match(job.last_attempt_id), f"Expected ULID, got {job.last_attempt_id!r}"


class TestComplete:
    """Verify Queue.complete_job transitions and attempt finalization."""

    async def test_complete_sets_status(self, queue):
        await queue.enqueue("my_task")
        job = await queue.claim_job("worker-1", ["default"])
        await queue.complete_job(job, "worker-1", result={"answer": 42})

        await job.refresh()
        assert job.status == "completed"
        assert job.result == {"answer": 42}
        assert job.finished_at is not None
        assert job.worker_id == ""
        assert job.lease_expires_at is None

    async def test_complete_terminalizes_attempt(self, queue):
        before = now_epoch()
        await queue.enqueue("my_task")
        job = await queue.claim_job("worker-1", ["default"])
        await queue.complete_job(job, "worker-1")
        after = now_epoch()

        attempts = await JobAttempt.query().filter(
            F("job_ulid") == job.ulid
        ).all()
        assert len(attempts) == 1
        assert attempts[0].status == "completed"
        assert before <= attempts[0].finished_at <= after

    async def test_complete_no_result(self, queue):
        await queue.enqueue("my_task")
        job = await queue.claim_job("worker-1", ["default"])
        await queue.complete_job(job, "worker-1")

        await job.refresh()
        assert job.status == "completed"
        assert job.result_json is None

    async def test_complete_wrong_worker_ignored(self, queue):
        await queue.enqueue("my_task")
        job = await queue.claim_job("worker-1", ["default"])
        await queue.complete_job(job, "worker-WRONG")

        await job.refresh()
        assert job.status == "running"
        assert job.worker_id == "worker-1"
        assert job.lease_expires_at is not None
        assert job.finished_at is None
        assert job.result_json is None

    async def test_complete_non_serializable_result_fails(self, queue):
        await queue.enqueue("my_task", max_retries=0)
        job = await queue.claim_job("worker-1", ["default"])
        await queue.complete_job(job, "worker-1", result=object())

        await job.refresh()
        assert job.status == "failed"
        assert job.last_failure_kind == "exception"
        assert job.finished_at is not None
        assert job.worker_id == ""
        assert job.lease_expires_at is None


class TestFail:
    """Verify Queue.fail_job with retry logic and attempt tracking."""

    async def test_fail_no_retries_marks_failed(self, queue):
        await queue.enqueue("my_task", max_retries=0)
        job = await queue.claim_job("worker-1", ["default"])
        exc = ValueError("something broke")
        await queue.fail_job(job, "worker-1", exc)

        await job.refresh()
        assert job.status == "failed"
        assert job.last_error == "something broke"
        assert job.last_failure_kind == "exception"
        assert job.finished_at is not None
        assert job.worker_id == ""

    async def test_fail_retryable_goes_pending(self, queue):
        before = now_epoch()
        await queue.enqueue("my_task", max_retries=3, retry_delay=10)
        job = await queue.claim_job("worker-1", ["default"])
        exc = RuntimeError("transient")
        await queue.fail_job(job, "worker-1", exc)

        await job.refresh()
        after = now_epoch()
        assert job.status == "pending"
        assert job.retry_count == 1
        assert job.attempts == 1
        # ETA: base_delay(10) * 2^0 = 10, plus up to 10% jitter (1)
        assert before + 10 <= job.eta <= after + 11
        assert job.finished_at is None

    async def test_fail_exhausted_retries_marks_failed(self, queue):
        await queue.enqueue("my_task", max_retries=1, retry_delay=1)
        job = await queue.claim_job("worker-1", ["default"])

        # First failure — retry
        await queue.fail_job(job, "worker-1", RuntimeError("fail-1"))
        await job.refresh()
        assert job.status == "pending"
        assert job.retry_count == 1

        # Reset ETA to now via atomic update so we can reclaim
        await Job.query().filter(F("ulid") == job.ulid).update_one(eta=now_epoch())

        # Re-claim
        job = await queue.claim_job("worker-1", ["default"])
        assert job is not None

        # Second failure — exhausted
        await queue.fail_job(job, "worker-1", RuntimeError("fail-2"))
        await job.refresh()
        assert job.status == "failed"
        assert job.finished_at is not None

    async def test_fail_non_retryable_immediate_failed(self, queue):
        await queue.enqueue("my_task", max_retries=3)
        job = await queue.claim_job("worker-1", ["default"])
        await queue.fail_job(
            job, "worker-1", RuntimeError("bad"),
            failure_kind=FailureKind.TASK_NOT_FOUND,
        )

        await job.refresh()
        assert job.status == "failed"  # non-retryable → immediate fail

    async def test_fail_terminalizes_attempt(self, queue):
        before = now_epoch()
        await queue.enqueue("my_task", max_retries=0)
        job = await queue.claim_job("worker-1", ["default"])
        exc = ValueError("boom")
        await queue.fail_job(job, "worker-1", exc)
        after = now_epoch()

        attempts = await JobAttempt.query().filter(
            F("job_ulid") == job.ulid
        ).all()
        assert len(attempts) == 1
        assert attempts[0].status == "failed"
        assert attempts[0].error == "boom"
        assert attempts[0].failure_kind == "exception"
        assert attempts[0].traceback is not None
        assert "ValueError" in attempts[0].traceback
        assert before <= attempts[0].finished_at <= after

    async def test_fail_wrong_worker_ignored(self, queue):
        await queue.enqueue("my_task")
        job = await queue.claim_job("worker-1", ["default"])
        await queue.fail_job(job, "worker-WRONG", RuntimeError("x"))

        await job.refresh()
        assert job.status == "running"
        assert job.worker_id == "worker-1"
        assert job.lease_expires_at is not None
        assert job.finished_at is None


class TestRetryEta:
    """Verify _calculate_retry_eta exponential backoff."""

    _FIXED_NOW = 1_000_000

    @patch("qler.queue.now_epoch", return_value=_FIXED_NOW)
    def test_first_retry(self, mock_now):
        job = Job(retry_delay=60, retry_count=0)
        eta = _calculate_retry_eta(job)
        # base_delay * 2^0 = 60, plus up to 10% jitter (6)
        assert self._FIXED_NOW + 60 <= eta <= self._FIXED_NOW + 66

    @patch("qler.queue.now_epoch", return_value=_FIXED_NOW)
    def test_second_retry(self, mock_now):
        job = Job(retry_delay=60, retry_count=1)
        eta = _calculate_retry_eta(job)
        # base_delay * 2^1 = 120, plus up to 10% jitter (12)
        assert self._FIXED_NOW + 120 <= eta <= self._FIXED_NOW + 132

    @patch("qler.queue.now_epoch", return_value=_FIXED_NOW)
    def test_third_retry(self, mock_now):
        job = Job(retry_delay=60, retry_count=2)
        eta = _calculate_retry_eta(job)
        # base_delay * 2^2 = 240, plus up to 10% jitter (24)
        assert self._FIXED_NOW + 240 <= eta <= self._FIXED_NOW + 264

    @patch("qler.queue.now_epoch", return_value=_FIXED_NOW)
    def test_capped_at_max_delay(self, mock_now):
        """Verify retry delay cannot exceed 24 hours."""
        job = Job(retry_delay=3600, retry_count=10)
        eta = _calculate_retry_eta(job)
        max_delay = 86_400  # _MAX_RETRY_DELAY
        max_jitter = int(max_delay * 0.1)
        assert self._FIXED_NOW + max_delay <= eta <= self._FIXED_NOW + max_delay + max_jitter


class TestPoisonPill:
    """Verify claim_job handles corrupt payload_json gracefully."""

    async def test_poison_pill_marks_failed(self, queue):
        """A job with invalid JSON payload is marked FAILED on claim."""
        # Enqueue a valid job, then corrupt its payload_json directly
        job = await queue.enqueue("my_task")
        await Job.query().filter(F("ulid") == job.ulid).update_one(
            payload_json="NOT VALID JSON {{{"
        )

        # Claim should return None (skips the poison pill)
        claimed = await queue.claim_job("worker-1", ["default"])
        assert claimed is None

        # Verify the job is marked FAILED
        await job.refresh()
        assert job.status == "failed"
        assert job.last_error == "Payload is not valid JSON"
        assert job.last_failure_kind == "payload_invalid"
        assert job.finished_at is not None

        # Verify a failed attempt was recorded
        attempts = await JobAttempt.query().filter(
            F("job_ulid") == job.ulid
        ).all()
        assert len(attempts) == 1
        assert attempts[0].status == "failed"
        assert attempts[0].failure_kind == "payload_invalid"


class TestQueueClose:
    """Verify Queue.close() resource cleanup."""

    async def test_close_owned_db(self, tmp_path):
        db_path = str(tmp_path / "test_close.db")
        q = Queue(db_path)
        await q.init_db()
        assert q._db is not None
        assert q._initialized is True

        await q.close()
        assert q._db is None
        assert q._initialized is False

    async def test_close_unowned_db_noop(self, db):
        q = Queue(db)
        await q.init_db()

        await q.close()
        # External DB should NOT be closed
        assert q._db is db
        assert q._owns_db is False

    async def test_close_idempotent(self, tmp_path):
        db_path = str(tmp_path / "test_close2.db")
        q = Queue(db_path)
        await q.init_db()

        await q.close()
        await q.close()  # Should not raise

    def test_db_property_raises_before_init(self):
        q = Queue("/tmp/nonexistent.db")
        with pytest.raises(RuntimeError, match="not initialized"):
            q.db


class TestErrorTruncation:
    """Verify error and traceback are capped at storage limits."""

    async def test_long_error_truncated(self, queue):
        await queue.enqueue("my_task", max_retries=0)
        job = await queue.claim_job("worker-1", ["default"])
        long_error = "x" * 2000
        await queue.fail_job(job, "worker-1", ValueError(long_error))

        await job.refresh()
        assert job.status == "failed"
        assert len(job.last_error) <= 1024

    async def test_traceback_stored_in_attempt(self, queue):
        await queue.enqueue("my_task", max_retries=0)
        job = await queue.claim_job("worker-1", ["default"])
        await queue.fail_job(job, "worker-1", ValueError("traceback test"))

        attempts = await JobAttempt.query().filter(
            F("job_ulid") == job.ulid
        ).all()
        assert len(attempts) == 1
        assert attempts[0].traceback is not None
        assert "ValueError" in attempts[0].traceback
        assert "traceback test" in attempts[0].traceback


class TestBatchEnqueue:
    """Queue.enqueue_many() — atomic batch job creation."""

    async def test_batch_creates_all_jobs(self, queue):
        """All jobs in a batch are created in a single call."""
        jobs = await queue.enqueue_many([
            {"task_path": "app.task_a", "args": (1,)},
            {"task_path": "app.task_b", "args": (2,)},
            {"task_path": "app.task_c", "args": (3,)},
        ])
        assert len(jobs) == 3
        assert jobs[0].task == "app.task_a"
        assert jobs[1].task == "app.task_b"
        assert jobs[2].task == "app.task_c"
        for job in jobs:
            assert job.status == JobStatus.PENDING.value
            assert job._id is not None  # saved to DB

    async def test_empty_batch_returns_empty_list(self, queue):
        jobs = await queue.enqueue_many([])
        assert jobs == []

    async def test_batch_per_job_options(self, queue):
        """Each job can have individual options."""
        jobs = await queue.enqueue_many([
            {"task_path": "t1", "priority": 10, "queue_name": "high"},
            {"task_path": "t2", "delay": 60, "max_retries": 3},
            {"task_path": "t3", "timeout": 30},
        ])
        assert len(jobs) == 3
        assert jobs[0].priority == 10
        assert jobs[0].queue_name == "high"
        assert jobs[1].max_retries == 3
        assert jobs[1].eta > jobs[0].eta  # delayed
        assert jobs[2].timeout == 30

    async def test_batch_atomic_on_bad_payload(self, queue):
        """If any job has a bad payload, none are created."""
        with pytest.raises(PayloadNotSerializableError, match="index 1"):
            await queue.enqueue_many([
                {"task_path": "good", "args": (1,)},
                {"task_path": "bad", "args": (object(),)},
                {"task_path": "good2", "args": (3,)},
            ])

        # Verify no jobs were created
        count = await Job.query().count()
        assert count == 0

    async def test_batch_missing_task_path(self, queue):
        with pytest.raises(ConfigurationError, match="index 0.*task_path"):
            await queue.enqueue_many([{"args": (1,)}])

    async def test_batch_idempotency(self, queue):
        """Idempotency keys are checked per-job within a batch."""
        # Pre-create a job with idempotency key
        existing = await queue.enqueue("existing_task", idempotency_key="key-1")

        jobs = await queue.enqueue_many([
            {"task_path": "new_task", "args": (1,)},
            {"task_path": "dupe_task", "idempotency_key": "key-1"},
        ])
        assert len(jobs) == 2
        assert jobs[0].task == "new_task"
        # Second job should be the existing one (idempotency match)
        assert jobs[1].ulid == existing.ulid

    async def test_batch_with_intra_batch_dependencies(self, queue):
        """Jobs within a batch can depend on earlier jobs in the same batch."""
        batch = [
            {"task_path": "parent"},
        ]
        parents = await queue.enqueue_many(batch)
        parent_ulid = parents[0].ulid

        children = await queue.enqueue_many([
            {"task_path": "child", "depends_on": [parent_ulid]},
        ])
        assert len(children) == 1
        assert children[0].pending_dep_count == 1
        assert children[0].dependencies == [parent_ulid]

    async def test_batch_intra_batch_integer_index_deps(self, queue):
        """Integer indices in depends_on resolve to earlier jobs in the batch."""
        jobs = await queue.enqueue_many([
            {"task_path": "step_a"},
            {"task_path": "step_b", "depends_on": [0]},
            {"task_path": "step_c", "depends_on": [0, 1]},
        ])
        assert len(jobs) == 3
        # step_b depends on step_a
        assert jobs[1].dependencies == [jobs[0].ulid]
        assert jobs[1].pending_dep_count == 1
        # step_c depends on both step_a and step_b
        assert jobs[2].dependencies == [jobs[0].ulid, jobs[1].ulid]
        assert jobs[2].pending_dep_count == 2
        # step_a has no deps
        assert jobs[0].dependencies == []
        assert jobs[0].pending_dep_count == 0

    async def test_batch_integer_index_self_dep(self, queue):
        """Integer index pointing to self raises DependencyError."""
        with pytest.raises(DependencyError, match="index 1.*out of range"):
            await queue.enqueue_many([
                {"task_path": "step_a"},
                {"task_path": "step_b", "depends_on": [1]},
            ])

    async def test_batch_integer_index_out_of_bounds(self, queue):
        """Integer index beyond batch size raises DependencyError."""
        with pytest.raises(DependencyError, match="index 0.*out of range"):
            await queue.enqueue_many([
                {"task_path": "step_a", "depends_on": [5]},
            ])

    async def test_batch_integer_index_negative(self, queue):
        """Negative integer index raises DependencyError."""
        with pytest.raises(DependencyError, match="index 1.*out of range"):
            await queue.enqueue_many([
                {"task_path": "step_a"},
                {"task_path": "step_b", "depends_on": [-1]},
            ])

    async def test_batch_mixed_integer_and_ulid_deps(self, queue):
        """Integer indices and ULID strings can be mixed in depends_on."""
        # Create an external parent job first
        external = await queue.enqueue_many([{"task_path": "external"}])
        ext_ulid = external[0].ulid

        jobs = await queue.enqueue_many([
            {"task_path": "step_a"},
            {"task_path": "step_b", "depends_on": [0, ext_ulid]},
        ])
        assert len(jobs) == 2
        assert ext_ulid in jobs[1].dependencies
        assert jobs[0].ulid in jobs[1].dependencies
        assert jobs[1].pending_dep_count == 2

    async def test_batch_payload_too_large(self, queue):
        """Payload size check applies per job in batch."""
        large = "x" * (queue.max_payload_size + 1)
        with pytest.raises(PayloadTooLargeError, match="index 0"):
            await queue.enqueue_many([
                {"task_path": "big", "args": (large,)},
            ])

    async def test_batch_dependency_not_found(self, queue):
        """Missing dependency raises DependencyError with index."""
        with pytest.raises(DependencyError, match="index 0.*not found"):
            await queue.enqueue_many([
                {"task_path": "child", "depends_on": ["NONEXISTENT"]},
            ])

    async def test_batch_default_values(self, queue):
        """Jobs use queue defaults when options not specified."""
        jobs = await queue.enqueue_many([
            {"task_path": "t1"},
        ])
        assert len(jobs) == 1
        assert jobs[0].queue_name == "default"
        assert jobs[0].priority == 0
        assert jobs[0].max_retries == queue.default_max_retries
        assert jobs[0].retry_delay == queue.default_retry_delay
        assert jobs[0].lease_duration == queue.default_lease_duration

    async def test_batch_intra_batch_idempotency(self, queue):
        """Two specs with the same idempotency_key in one batch → only 1 job created."""
        jobs = await queue.enqueue_many([
            {"task_path": "task_a", "idempotency_key": "dup"},
            {"task_path": "task_b", "idempotency_key": "dup"},
            {"task_path": "task_c", "idempotency_key": "unique"},
        ])
        assert len(jobs) == 3  # 3 entries returned (2nd is reference to 1st)

        # First and second should be the same job object
        assert jobs[0].ulid == jobs[1].ulid
        assert jobs[0].task == "task_a"  # first spec wins
        # Third is distinct
        assert jobs[2].ulid != jobs[0].ulid
        assert jobs[2].task == "task_c"

        # Only 2 unique jobs in the DB
        unique_ulids = {j.ulid for j in jobs}
        assert len(unique_ulids) == 2

        total = await Job.query().count()
        assert total == 2


class TestBatchEnqueuePerformance:
    """Verify enqueue_many scales well with large batches."""

    async def test_enqueue_many_5k_no_deps(self, queue):
        """5K items with no deps/idem/unique completes in <10s."""
        specs = [
            {"task_path": f"task_{i}", "kwargs": {"i": i}}
            for i in range(5000)
        ]
        t0 = time.perf_counter()
        jobs = await queue.enqueue_many(specs)
        elapsed = time.perf_counter() - t0

        assert len(jobs) == 5000
        assert all(j._id is not None for j in jobs)
        assert len({j.ulid for j in jobs}) == 5000  # all unique
        assert elapsed < 10, f"enqueue_many(5K) took {elapsed:.1f}s, expected <10s"

        total = await Job.query().count()
        assert total == 5000

    async def test_enqueue_many_1k_with_intra_batch_deps(self, queue):
        """1K items with intra-batch deps — all dep edges created correctly."""
        specs = []
        # Job 0 has no deps, jobs 1-999 depend on the previous job (linear chain)
        specs.append({"task_path": "chain_0"})
        for i in range(1, 1000):
            specs.append({"task_path": f"chain_{i}", "depends_on": [i - 1]})

        t0 = time.perf_counter()
        jobs = await queue.enqueue_many(specs)
        elapsed = time.perf_counter() - t0

        assert len(jobs) == 1000
        assert elapsed < 10, f"enqueue_many(1K deps) took {elapsed:.1f}s, expected <10s"

        # Verify dep edges in the DB
        cur = await queue._db.adapter.execute(
            "SELECT COUNT(*) FROM qler_job_deps", []
        )
        row = await cur.fetchone()
        await cur.close()
        assert row[0] == 999  # 999 edges (each job depends on previous)

        # First job has no deps
        assert jobs[0].pending_dep_count == 0
        # All others have 1 pending dep
        for j in jobs[1:]:
            assert j.pending_dep_count == 1

    async def test_enqueue_many_batch_idempotency_prefetch(self, queue):
        """Batch idempotency pre-fetch deduplicates against DB in one query."""
        # Pre-create 50 jobs with idempotency keys
        pre_specs = [
            {"task_path": "pre_task", "idempotency_key": f"key_{i}"}
            for i in range(50)
        ]
        pre_jobs = await queue.enqueue_many(pre_specs)
        assert len(pre_jobs) == 50

        # Now enqueue 200 specs: 50 with existing idem keys + 150 new
        specs = []
        for i in range(50):
            specs.append({"task_path": "dup_task", "idempotency_key": f"key_{i}"})
        for i in range(150):
            specs.append({"task_path": "new_task", "idempotency_key": f"new_key_{i}"})

        jobs = await queue.enqueue_many(specs)
        assert len(jobs) == 200

        # First 50 should be the pre-existing jobs
        for i in range(50):
            assert jobs[i].ulid == pre_jobs[i].ulid
            assert jobs[i].task == "pre_task"

        # Last 150 should be new
        new_ulids = {j.ulid for j in jobs[50:]}
        assert len(new_ulids) == 150

        total = await Job.query().count()
        assert total == 200  # 50 pre-existing + 150 new

    async def test_enqueue_many_fan_in_deps(self, queue):
        """Many jobs depending on one parent — dep edges batched correctly."""
        # Create a parent job first
        parent = await queue.enqueue("parent_task")

        # 500 children all depending on the parent
        specs = [
            {"task_path": f"child_{i}", "depends_on": [parent.ulid]}
            for i in range(500)
        ]
        jobs = await queue.enqueue_many(specs)
        assert len(jobs) == 500

        # All dep edges should exist
        cur = await queue._db.adapter.execute(
            "SELECT COUNT(*) FROM qler_job_deps WHERE parent_ulid = ?",
            [parent.ulid],
        )
        row = await cur.fetchone()
        await cur.close()
        assert row[0] == 500

        # Each child has exactly 1 pending dep (the parent)
        assert all(j.pending_dep_count == 1 for j in jobs)
