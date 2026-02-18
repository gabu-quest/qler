"""Tests for Job and JobAttempt models — fields, defaults, promoted columns, properties."""

from qler.enums import AttemptStatus, JobStatus
from qler.models.attempt import JobAttempt
from qler.models.job import Job


class TestJobDefaults:
    """Verify Job model field defaults and promoted column definitions."""

    def test_promoted_columns(self):
        expected = {"ulid", "status", "queue_name", "priority", "eta", "lease_expires_at", "pending_dep_count"}
        assert set(Job.__promoted__.keys()) == expected

    def test_check_constraints(self):
        assert "status" in Job.__checks__
        for status in JobStatus:
            assert status.value in Job.__checks__["status"]

    def test_default_field_values(self):
        job = Job()
        assert job.status == "pending"
        assert job.queue_name == "default"
        assert job.priority == 0
        assert job.eta == 0
        assert job.lease_expires_at is None
        assert job.task == ""
        assert job.worker_id == ""
        assert job.lease_duration == 300
        assert job.payload_json == "{}"
        assert job.result_json is None
        assert job.last_error is None
        assert job.last_failure_kind is None
        assert job.attempts == 0
        assert job.retry_count == 0
        assert job.max_retries == 0
        assert job.retry_delay == 60
        assert job.last_attempt_id is None
        assert job.correlation_id == ""
        assert job.idempotency_key is None
        assert job.created_at == 0
        assert job.updated_at == 0
        assert job.finished_at is None

    def test_payload_property(self):
        job = Job(payload_json='{"args": [1, 2], "kwargs": {"x": 3}}')
        assert job.payload == {"args": [1, 2], "kwargs": {"x": 3}}

    def test_payload_property_default(self):
        job = Job()
        assert job.payload == {}

    def test_result_property_none(self):
        job = Job()
        assert job.result is None

    def test_result_property_value(self):
        job = Job(result_json='{"value": 42}')
        assert job.result == {"value": 42}


class TestJobAttemptDefaults:
    """Verify JobAttempt model field defaults and promoted column definitions."""

    def test_promoted_columns(self):
        expected = {"ulid", "job_ulid", "status"}
        assert set(JobAttempt.__promoted__.keys()) == expected

    def test_check_constraints(self):
        assert "status" in JobAttempt.__checks__
        for status in AttemptStatus:
            assert status.value in JobAttempt.__checks__["status"]

    def test_default_field_values(self):
        attempt = JobAttempt()
        assert attempt.status == "running"
        assert attempt.ulid == ""
        assert attempt.job_ulid == ""
        assert attempt.attempt_number == 0
        assert attempt.worker_id == ""
        assert attempt.started_at == 0
        assert attempt.finished_at is None
        assert attempt.failure_kind is None
        assert attempt.error is None
        assert attempt.traceback is None
        assert attempt.lease_expires_at is None


class TestJobRoundTrip:
    """Verify Job can be saved and loaded from sqler."""

    async def test_save_and_load(self, queue):
        job = Job(
            ulid="01H0000000000000000000TEST",
            status="pending",
            queue_name="test-queue",
            priority=5,
            task="my_module.my_task",
            payload_json='{"args": [1], "kwargs": {}}',
            created_at=1000,
            updated_at=1000,
        )
        await job.save()
        assert job._id is not None

        loaded = await Job.from_id(job._id)
        assert loaded is not None
        assert loaded.ulid == "01H0000000000000000000TEST"
        assert loaded.status == "pending"
        assert loaded.queue_name == "test-queue"
        assert loaded.priority == 5
        assert loaded.task == "my_module.my_task"
        assert loaded.payload == {"args": [1], "kwargs": {}}
        assert loaded.created_at == 1000

    async def test_attempt_save_and_load(self, queue):
        attempt = JobAttempt(
            ulid="01H0000000000000000ATTEMPT",
            job_ulid="01H0000000000000000000TEST",
            status="running",
            attempt_number=1,
            worker_id="worker-1",
            started_at=1000,
        )
        await attempt.save()
        assert attempt._id is not None

        loaded = await JobAttempt.from_id(attempt._id)
        assert loaded is not None
        assert loaded.ulid == "01H0000000000000000ATTEMPT"
        assert loaded.job_ulid == "01H0000000000000000000TEST"
        assert loaded.status == "running"
        assert loaded.attempt_number == 1
        assert loaded.worker_id == "worker-1"
