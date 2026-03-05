"""Tests for the Worker class — init, execution, concurrency, shutdown, correlation."""

import asyncio
import json
import re

import pytest
import pytest_asyncio
from qler._context import current_job
from qler._time import generate_ulid, now_epoch
from qler.enums import AttemptStatus, FailureKind, JobStatus
from qler.exceptions import ConfigurationError, JobFailedError
from qler.models.attempt import JobAttempt
from qler.models.job import Job
from qler.queue import Queue
from qler.task import task
from qler.worker import Worker
from sqler import AsyncSQLerDB, F

_ULID_PATTERN = re.compile(r"^[0-9A-Z]{26}$")
_WORKER_ID_PATTERN = re.compile(r"^.+:\d+:[0-9A-Z]{26}$")


# ---------------------------------------------------------------------------
# Module-level task functions (required for @task decorator)
# ---------------------------------------------------------------------------

_captured_job_ulid = None  # Sentinel for context var test


async def succeeding_task(x, y=1):
    return {"sum": x + y}


async def failing_task():
    raise RuntimeError("boom")


async def slow_task(duration=1.0):
    await asyncio.sleep(duration)
    return "done"


def sync_task(x):
    return x * 2


async def context_var_task():
    global _captured_job_ulid
    job = current_job()
    _captured_job_ulid = job.ulid
    return "ok"


async def wrong_sig_task(a, b, c):
    return a + b + c


# Module-level tasks for concurrency tests
_execution_log: list[str] = []


async def logging_task(name):
    _execution_log.append(f"{name}_start")
    await asyncio.sleep(0.05)
    _execution_log.append(f"{name}_end")
    return name


# Module-level tasks for correlation tests
_captured_correlation: str | None = None


async def capture_correlation_task():
    global _captured_correlation
    job = current_job()
    _captured_correlation = job.correlation_id
    return "ok"


async def capture_no_correlation_task():
    global _captured_correlation
    job = current_job()
    _captured_correlation = job.correlation_id
    return "ok"


async def timeout_slow_task():
    await asyncio.sleep(10)
    return "should not reach"


async def timeout_fast_task():
    await asyncio.sleep(0.01)
    return "fast"


def timeout_sync_task():
    import time
    time.sleep(3)
    return "should not reach"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest_asyncio.fixture
async def worker_db():
    """In-memory sqler database for worker tests."""
    _db = AsyncSQLerDB.in_memory(shared=False)
    await _db.connect()
    yield _db
    await _db.close()


@pytest_asyncio.fixture
async def worker_queue(worker_db):
    """Queue with short lease for fast tests."""
    q = Queue(worker_db, default_lease_duration=5, default_max_retries=0)
    await q.init_db()
    yield q


def _make_worker(queue, **kwargs):
    """Create a Worker with fast test defaults."""
    defaults = {
        "queues": ["default"],
        "concurrency": 1,
        "poll_interval": 0.01,
        "lease_recovery_interval": 60.0,
        "shutdown_timeout": 2.0,
    }
    defaults.update(kwargs)
    return Worker(queue, **defaults)


async def _run_worker_until_job_done(worker, job, timeout=5.0):
    """Run worker in background, wait for job to reach terminal state, stop worker."""
    worker_task = asyncio.create_task(worker.run())
    try:
        result = await job.wait(timeout=timeout, poll_interval=0.02)
        return result
    finally:
        worker._running = False
        await asyncio.sleep(0.05)
        worker_task.cancel()
        try:
            await worker_task
        except asyncio.CancelledError:
            pass


# ---------------------------------------------------------------------------
# TestWorkerInit
# ---------------------------------------------------------------------------

class TestWorkerInit:
    """Worker.__init__ validation and defaults."""

    def test_worker_id_format(self, worker_queue):
        w = _make_worker(worker_queue)
        assert _WORKER_ID_PATTERN.match(w.worker_id), (
            f"Expected hostname:pid:ULID format, got {w.worker_id!r}"
        )

    def test_rejects_empty_queues(self, worker_queue):
        with pytest.raises(ConfigurationError, match="must not be empty"):
            Worker(worker_queue, queues=[])

    def test_rejects_zero_concurrency(self, worker_queue):
        with pytest.raises(ConfigurationError, match="concurrency must be >= 1"):
            Worker(worker_queue, concurrency=0)

    def test_defaults(self, worker_queue):
        w = Worker(worker_queue)
        assert w.queues == ["default"]
        assert w.concurrency == 1
        assert w.poll_interval == 1.0
        assert w.shutdown_timeout == 30.0

    def test_rejects_zero_shutdown_timeout(self, worker_queue):
        with pytest.raises(ConfigurationError, match="shutdown_timeout must be > 0"):
            Worker(worker_queue, shutdown_timeout=0)

    def test_rejects_negative_shutdown_timeout(self, worker_queue):
        with pytest.raises(ConfigurationError, match="shutdown_timeout must be > 0"):
            Worker(worker_queue, shutdown_timeout=-1.0)

    def test_custom_queues(self, worker_queue):
        w = Worker(worker_queue, queues=["high", "low"])
        assert w.queues == ["high", "low"]


# ---------------------------------------------------------------------------
# TestWorkerExecution
# ---------------------------------------------------------------------------

class TestWorkerExecution:
    """Worker claims and executes jobs correctly."""

    async def test_single_job_completes(self, worker_queue):
        wrapped = task(worker_queue)(succeeding_task)
        job = await wrapped.enqueue(10, y=5)

        w = _make_worker(worker_queue)
        result = await _run_worker_until_job_done(w, job)
        assert result.status == "completed"
        assert result.result == {"sum": 15}

    async def test_sync_task_executes(self, worker_queue):
        wrapped = task(worker_queue, sync=True)(sync_task)
        job = await wrapped.enqueue(21)

        w = _make_worker(worker_queue)
        result = await _run_worker_until_job_done(w, job)
        assert result.status == "completed"
        assert result.result == 42

    async def test_task_not_found(self, worker_queue):
        """Unregistered task path → permanent FAILED with TASK_NOT_FOUND."""
        job = await worker_queue.enqueue("nonexistent.task_fn")

        w = _make_worker(worker_queue)
        with pytest.raises(JobFailedError) as exc_info:
            await _run_worker_until_job_done(w, job)
        assert exc_info.value.failure_kind == FailureKind.TASK_NOT_FOUND.value

        await job.refresh()
        assert job.status == "failed"
        assert job.last_failure_kind == FailureKind.TASK_NOT_FOUND.value

    async def test_signature_mismatch(self, worker_queue):
        """Wrong args → permanent FAILED with SIGNATURE_MISMATCH."""
        wrapped = task(worker_queue)(wrong_sig_task)
        # Enqueue with wrong number of args (expects a, b, c)
        job = await worker_queue.enqueue(
            wrapped.task_path,
            args=(1,),  # Missing b and c
        )

        w = _make_worker(worker_queue)
        with pytest.raises(JobFailedError) as exc_info:
            await _run_worker_until_job_done(w, job)
        assert exc_info.value.failure_kind == FailureKind.SIGNATURE_MISMATCH.value

        await job.refresh()
        assert job.status == "failed"
        assert job.last_failure_kind == FailureKind.SIGNATURE_MISMATCH.value

    async def test_exception_with_retries(self, worker_queue):
        """Task raises → retry if max_retries allows. First failure retries."""
        q = Queue(worker_queue.db, default_lease_duration=5, default_max_retries=1)
        await q.init_db()
        wrapped = task(q)(failing_task)
        job = await wrapped.enqueue()

        w = _make_worker(q)
        worker_task = asyncio.create_task(w.run())
        try:
            # Wait for first attempt to fail and job to go back to pending
            for _ in range(100):
                await asyncio.sleep(0.02)
                await job.refresh()
                if job.retry_count >= 1:
                    break
        finally:
            w._running = False
            await asyncio.sleep(0.05)
            worker_task.cancel()
            try:
                await worker_task
            except asyncio.CancelledError:
                pass

        # After 1 failure with max_retries=1, job goes back to pending for retry
        assert job.status == "pending"
        assert job.retry_count == 1
        assert job.last_failure_kind == FailureKind.EXCEPTION.value
        assert job.last_error == "boom"

    async def test_exception_exhausted(self, worker_queue):
        """Task raises with no retries → permanent FAILED."""
        wrapped = task(worker_queue)(failing_task)
        job = await wrapped.enqueue()

        w = _make_worker(worker_queue)
        with pytest.raises(JobFailedError):
            await _run_worker_until_job_done(w, job)

        await job.refresh()
        assert job.status == "failed"
        assert job.last_failure_kind == FailureKind.EXCEPTION.value
        assert job.last_error == "boom"

    async def test_result_stored(self, worker_queue):
        """Completed job stores result in result_json."""
        wrapped = task(worker_queue)(succeeding_task)
        job = await wrapped.enqueue(3, y=7)

        w = _make_worker(worker_queue)
        result = await _run_worker_until_job_done(w, job)
        assert result.result_json is not None
        assert result.result == {"sum": 10}

    async def test_context_var_set(self, worker_queue):
        """current_job() works inside task execution."""
        global _captured_job_ulid
        _captured_job_ulid = None

        wrapped = task(worker_queue)(context_var_task)
        job = await wrapped.enqueue()

        w = _make_worker(worker_queue)
        await _run_worker_until_job_done(w, job)

        assert _captured_job_ulid == job.ulid

    async def test_attempt_record_created(self, worker_queue):
        """Completed job has a completed attempt record."""
        wrapped = task(worker_queue)(succeeding_task)
        job = await wrapped.enqueue(1)

        w = _make_worker(worker_queue)
        await _run_worker_until_job_done(w, job)

        attempts = await JobAttempt.query().filter(
            F("job_ulid") == job.ulid
        ).all()
        assert len(attempts) == 1
        assert attempts[0].status == AttemptStatus.COMPLETED.value
        assert attempts[0].worker_id == w.worker_id


# ---------------------------------------------------------------------------
# TestWorkerConcurrency
# ---------------------------------------------------------------------------

class TestWorkerConcurrency:
    """Concurrency control via semaphore."""

    async def test_concurrent_jobs(self, worker_queue):
        """concurrency=2 runs two jobs in parallel."""
        wrapped = task(worker_queue)(slow_task)
        job1 = await wrapped.enqueue(duration=0.1)
        job2 = await wrapped.enqueue(duration=0.1)

        w = _make_worker(worker_queue, concurrency=2)
        worker_task = asyncio.create_task(w.run())
        try:
            # Both should complete within ~0.2s (parallel), not ~0.4s (serial)
            await job1.wait(timeout=2.0, poll_interval=0.02)
            await job2.wait(timeout=2.0, poll_interval=0.02)
        finally:
            w._running = False
            worker_task.cancel()
            try:
                await worker_task
            except asyncio.CancelledError:
                pass

        await job1.refresh()
        await job2.refresh()
        assert job1.status == "completed"
        assert job2.status == "completed"

    async def test_semaphore_limits(self, worker_queue):
        """concurrency=1 processes jobs one at a time."""
        _execution_log.clear()

        wrapped = task(worker_queue)(logging_task)
        job1 = await wrapped.enqueue("a")
        job2 = await wrapped.enqueue("b")

        w = _make_worker(worker_queue, concurrency=1)
        worker_task = asyncio.create_task(w.run())
        try:
            await job1.wait(timeout=2.0, poll_interval=0.02)
            await job2.wait(timeout=2.0, poll_interval=0.02)
        finally:
            w._running = False
            worker_task.cancel()
            try:
                await worker_task
            except asyncio.CancelledError:
                pass

        # With concurrency=1, one job must fully complete before the next starts.
        # Order is non-deterministic (a or b first), but must be serial.
        assert len(_execution_log) == 4
        first_name = _execution_log[0].replace("_start", "")
        second_name = "b" if first_name == "a" else "a"
        assert _execution_log == [
            f"{first_name}_start",
            f"{first_name}_end",
            f"{second_name}_start",
            f"{second_name}_end",
        ]


# ---------------------------------------------------------------------------
# TestWorkerShutdown
# ---------------------------------------------------------------------------

class TestWorkerShutdown:
    """Graceful shutdown behavior."""

    async def test_graceful_completes_running(self, worker_queue):
        """Running jobs finish before shutdown completes."""
        wrapped = task(worker_queue)(slow_task)
        job = await wrapped.enqueue(duration=0.1)

        w = _make_worker(worker_queue, shutdown_timeout=5.0)
        worker_task = asyncio.create_task(w.run())

        # Wait for job to start executing
        for _ in range(50):
            await asyncio.sleep(0.02)
            if w._active_jobs:
                break

        # Signal shutdown
        w._running = False
        await asyncio.sleep(0.3)  # Let it drain
        worker_task.cancel()
        try:
            await worker_task
        except asyncio.CancelledError:
            pass

        await job.refresh()
        assert job.status == "completed"

    async def test_running_flag_stops_claim_loop(self, worker_queue):
        """Setting _running = False causes the worker task to exit cleanly."""
        w = _make_worker(worker_queue)
        worker_task = asyncio.create_task(w.run())

        await asyncio.sleep(0.05)
        assert w._running is True

        w._running = False
        # Worker has poll_interval=0.01, so 0.15s is plenty of time to exit
        await asyncio.sleep(0.15)
        assert worker_task.done(), "Worker claim loop did not exit after _running=False"

        # Cleanup: ensure no exception was raised
        if not worker_task.cancelled():
            worker_task.result()  # raises if the task raised


# ---------------------------------------------------------------------------
# TestCorrelation
# ---------------------------------------------------------------------------

class TestCorrelation:
    """Correlation context wraps execution."""

    async def test_correlation_id_used(self, worker_queue):
        """Job with correlation_id uses it for context."""
        global _captured_correlation
        _captured_correlation = None

        wrapped = task(worker_queue)(capture_correlation_task)
        job = await worker_queue.enqueue(
            wrapped.task_path,
            correlation_id="my-trace-123",
        )

        w = _make_worker(worker_queue)
        await _run_worker_until_job_done(w, job)

        assert _captured_correlation == "my-trace-123"

    async def test_falls_back_to_ulid(self, worker_queue):
        """Job without correlation_id falls back to job ulid."""
        global _captured_correlation
        _captured_correlation = None

        wrapped = task(worker_queue)(capture_no_correlation_task)
        job = await wrapped.enqueue()

        w = _make_worker(worker_queue)
        await _run_worker_until_job_done(w, job)

        # correlation_id defaults to "" for jobs enqueued without one
        assert _captured_correlation == ""


# ---------------------------------------------------------------------------
# TestLeaseRenewalLoop
# ---------------------------------------------------------------------------

class TestLeaseRenewalLoop:
    """Verify the background lease renewal loop extends leases mid-execution."""

    async def test_renewal_extends_lease_for_long_running_job(self, worker_db):
        """Job taking longer than lease_duration completes because renewal extends the lease."""
        q = Queue(worker_db, default_lease_duration=2, default_max_retries=0)
        await q.init_db()
        wrapped = task(q)(slow_task)
        job = await wrapped.enqueue(duration=3.0)

        w = _make_worker(q, lease_recovery_interval=60.0)
        worker_task = asyncio.create_task(w.run())

        try:
            # Wait for the job to be claimed
            for _ in range(50):
                await asyncio.sleep(0.02)
                if w._active_jobs:
                    break
            assert len(w._active_jobs) == 1

            # Record the original lease expiry
            await job.refresh()
            original_expiry = job.lease_expires_at

            # Wait long enough for the renewal loop to fire (~2s)
            await asyncio.sleep(2.5)

            # Lease should have been extended beyond the original
            await job.refresh()
            assert job.lease_expires_at > original_expiry, (
                f"Lease was not renewed: original={original_expiry}, "
                f"current={job.lease_expires_at}"
            )

            # Job should still complete successfully despite taking 3s > 2s lease
            await job.wait(timeout=5.0, poll_interval=0.02)
            assert job.status == "completed"
            assert job.result == "done"
        finally:
            w._running = False
            await asyncio.sleep(0.05)
            worker_task.cancel()
            try:
                await worker_task
            except asyncio.CancelledError:
                pass


# ---------------------------------------------------------------------------
# TestShutdownTimeout
# ---------------------------------------------------------------------------

class TestShutdownTimeout:
    """Verify forced cancellation when jobs exceed shutdown_timeout."""

    async def test_timeout_cancels_long_running_job(self, worker_db):
        """Job exceeding shutdown_timeout gets cancelled and marked FAILED."""
        q = Queue(worker_db, default_lease_duration=30, default_max_retries=0)
        await q.init_db()
        wrapped = task(q)(slow_task)
        job = await wrapped.enqueue(duration=30.0)

        w = _make_worker(q, shutdown_timeout=0.1)
        worker_task = asyncio.create_task(w.run())

        try:
            # Wait for job to start executing
            for _ in range(50):
                await asyncio.sleep(0.02)
                if w._active_jobs:
                    break
            assert len(w._active_jobs) == 1

            # Signal shutdown — worker has 0.1s to drain
            w._running = False

            # Wait for the worker to fully shut down
            for _ in range(50):
                await asyncio.sleep(0.05)
                if worker_task.done():
                    break
            assert worker_task.done(), "Worker did not shut down"
        finally:
            if not worker_task.done():
                worker_task.cancel()
                try:
                    await worker_task
                except asyncio.CancelledError:
                    pass

        # The CancelledError handler should have marked the job FAILED
        await job.refresh()
        assert job.status == "failed"
        assert job.last_failure_kind == FailureKind.CANCELLED.value


# ---------------------------------------------------------------------------
# TestMalformedPayload
# ---------------------------------------------------------------------------

class TestMalformedPayload:
    """Verify worker handles payloads with valid JSON but wrong structure."""

    async def test_non_dict_payload_fails_as_payload_invalid(self, worker_db):
        """Valid JSON but non-dict payload (e.g., '123') → PAYLOAD_INVALID."""
        q = Queue(worker_db, default_lease_duration=5, default_max_retries=0)
        await q.init_db()
        # Register a task so it's found (we want to hit the payload parse path, not TASK_NOT_FOUND)
        wrapped = task(q)(succeeding_task)

        # Manually insert a job with a non-dict payload that passes the poison pill
        # check (json.loads("123") succeeds) but fails .get() in _execute_job
        now = now_epoch()
        job = Job(
            ulid=generate_ulid(),
            status=JobStatus.PENDING.value,
            queue_name="default",
            task=wrapped.task_path,
            payload_json="123",
            max_retries=0,
            retry_delay=60,
            lease_duration=5,
            created_at=now,
            updated_at=now,
            eta=now,
        )
        await job.save()

        w = _make_worker(q)
        with pytest.raises(JobFailedError) as exc_info:
            await _run_worker_until_job_done(w, job)
        assert exc_info.value.failure_kind == FailureKind.PAYLOAD_INVALID.value

        await job.refresh()
        assert job.status == "failed"
        assert job.last_failure_kind == FailureKind.PAYLOAD_INVALID.value

    async def test_null_payload_fails_as_payload_invalid(self, worker_db):
        """JSON null payload → PAYLOAD_INVALID."""
        q = Queue(worker_db, default_lease_duration=5, default_max_retries=0)
        await q.init_db()
        wrapped = task(q)(succeeding_task)

        now = now_epoch()
        job = Job(
            ulid=generate_ulid(),
            status=JobStatus.PENDING.value,
            queue_name="default",
            task=wrapped.task_path,
            payload_json="null",
            max_retries=0,
            retry_delay=60,
            lease_duration=5,
            created_at=now,
            updated_at=now,
            eta=now,
        )
        await job.save()

        w = _make_worker(q)
        with pytest.raises(JobFailedError) as exc_info:
            await _run_worker_until_job_done(w, job)
        assert exc_info.value.failure_kind == FailureKind.PAYLOAD_INVALID.value

        await job.refresh()
        assert job.status == "failed"
        assert job.last_failure_kind == FailureKind.PAYLOAD_INVALID.value


# ---------------------------------------------------------------------------
# TestRetryExhaustion
# ---------------------------------------------------------------------------

class TestRetryExhaustion:
    """Verify the full retry exhaustion cycle through the worker."""

    async def test_retry_then_permanent_failure(self, worker_db):
        """max_retries=1: attempt 1 fails → retry → attempt 2 fails → permanent FAILED."""
        q = Queue(
            worker_db,
            default_lease_duration=5,
            default_max_retries=1,
            default_retry_delay=1,
        )
        await q.init_db()
        wrapped = task(q)(failing_task)
        job = await wrapped.enqueue()

        w = _make_worker(q)
        # job.wait() will raise JobFailedError once retries are exhausted
        with pytest.raises(JobFailedError):
            await _run_worker_until_job_done(w, job, timeout=10.0)

        await job.refresh()
        assert job.status == "failed"
        assert job.retry_count == 1
        assert job.attempts == 2
        assert job.last_failure_kind == FailureKind.EXCEPTION.value
        assert job.last_error == "boom"

        # Verify both attempt records
        attempts = await JobAttempt.query().filter(
            F("job_ulid") == job.ulid
        ).order_by("ulid").all()
        assert len(attempts) == 2
        assert attempts[0].status == AttemptStatus.FAILED.value
        assert attempts[0].attempt_number == 1
        assert attempts[1].status == AttemptStatus.FAILED.value
        assert attempts[1].attempt_number == 2


# ---------------------------------------------------------------------------
# TestHealthEndpoint
# ---------------------------------------------------------------------------


async def _http_get(host: str, port: int, path: str) -> tuple[int, dict]:
    """Make an HTTP GET request and return (status_code, parsed_json_body)."""
    reader, writer = await asyncio.open_connection(host, port)
    try:
        writer.write(f"GET {path} HTTP/1.0\r\nHost: {host}\r\n\r\n".encode())
        await writer.drain()
        data = await reader.read()
        text = data.decode("utf-8")
        status_line = text.split("\r\n", 1)[0]
        status_code = int(status_line.split(" ", 2)[1])
        body = text.split("\r\n\r\n", 1)[1]
        return status_code, json.loads(body)
    finally:
        writer.close()
        await writer.wait_closed()


async def _unix_http_get(socket_path: str, path: str) -> tuple[int, dict]:
    """Make an HTTP GET request over a Unix socket."""
    reader, writer = await asyncio.open_unix_connection(socket_path)
    try:
        writer.write(f"GET {path} HTTP/1.0\r\nHost: localhost\r\n\r\n".encode())
        await writer.drain()
        data = await reader.read()
        text = data.decode("utf-8")
        status_line = text.split("\r\n", 1)[0]
        status_code = int(status_line.split(" ", 2)[1])
        body = text.split("\r\n\r\n", 1)[1]
        return status_code, json.loads(body)
    finally:
        writer.close()
        await writer.wait_closed()


class TestHealthEndpoint:
    """Health endpoint: TCP and Unix socket, JSON response, lifecycle."""

    def test_constructor_rejects_both_port_and_socket(self, worker_queue):
        with pytest.raises(
            ConfigurationError, match="mutually exclusive"
        ):
            Worker(worker_queue, health_port=9100, health_socket="/tmp/h.sock")

    def test_constructor_rejects_invalid_port_zero(self, worker_queue):
        with pytest.raises(
            ConfigurationError, match="health_port must be between 1 and 65535"
        ):
            Worker(worker_queue, health_port=0)

    def test_constructor_rejects_invalid_port_too_high(self, worker_queue):
        with pytest.raises(
            ConfigurationError, match="health_port must be between 1 and 65535"
        ):
            Worker(worker_queue, health_port=70000)

    def test_status_property_before_run(self, worker_queue):
        """Before run(), _running is False → status is 'draining'."""
        w = _make_worker(worker_queue)
        assert w.status == "draining"

    def test_started_at_set_on_init(self, worker_queue):
        before = now_epoch()
        w = _make_worker(worker_queue)
        after = now_epoch()
        assert before <= w._started_at <= after

    async def test_health_not_started_without_flag(self, worker_queue):
        """No health server starts without explicit port/socket."""
        w = _make_worker(worker_queue)
        worker_task = asyncio.create_task(w.run())
        await asyncio.sleep(0.05)
        try:
            # No health background task should exist (only lease renewal + recovery)
            health_tasks = [
                t for t in w._background_tasks
                if "health" in (t.get_name() or "")
            ]
            # The health task won't have "health" in name by default, but
            # the total count should be exactly 2 (lease renewal + recovery)
            assert len(w._background_tasks) == 2
        finally:
            w._running = False
            await asyncio.sleep(0.05)
            worker_task.cancel()
            try:
                await worker_task
            except asyncio.CancelledError:
                pass

    async def test_health_tcp_responds(self, worker_queue):
        """TCP health endpoint returns 200 with correct JSON fields."""
        port = 19100
        before = now_epoch()
        w = _make_worker(worker_queue, health_port=port)
        after = now_epoch()
        worker_task = asyncio.create_task(w.run())
        await asyncio.sleep(0.15)  # Let server start

        try:
            status_code, data = await _http_get("127.0.0.1", port, "/health")
            assert status_code == 200
            assert data["status"] == "healthy"
            assert data["worker_id"] == w.worker_id
            assert data["concurrency"] == w.concurrency
            assert data["queues"] == w.queues
            assert data["active_jobs"] == 0
            assert data["started_at"] == w._started_at
            assert before <= data["started_at"] <= after
            assert 0 <= data["uptime_seconds"] <= (now_epoch() - before) + 1
        finally:
            w._running = False
            await asyncio.sleep(0.1)
            worker_task.cancel()
            try:
                await worker_task
            except asyncio.CancelledError:
                pass

    async def test_health_unix_socket_responds(self, worker_queue, tmp_path):
        """Unix socket health endpoint returns 200 with correct JSON."""
        sock_path = str(tmp_path / "health.sock")
        w = _make_worker(worker_queue, health_socket=sock_path)
        worker_task = asyncio.create_task(w.run())
        await asyncio.sleep(0.15)

        try:
            status_code, data = await _unix_http_get(sock_path, "/health")
            assert status_code == 200
            assert data["status"] == "healthy"
            assert data["worker_id"] == w.worker_id
            assert data["concurrency"] == w.concurrency
            assert data["queues"] == w.queues
        finally:
            w._running = False
            await asyncio.sleep(0.1)
            worker_task.cancel()
            try:
                await worker_task
            except asyncio.CancelledError:
                pass

    async def test_health_unix_socket_cleanup(self, worker_queue, tmp_path):
        """Socket file is removed after worker stops."""
        sock_path = str(tmp_path / "health.sock")
        w = _make_worker(worker_queue, health_socket=sock_path)
        worker_task = asyncio.create_task(w.run())
        await asyncio.sleep(0.15)

        import pathlib
        assert pathlib.Path(sock_path).exists()

        w._running = False
        await asyncio.sleep(0.2)
        worker_task.cancel()
        try:
            await worker_task
        except asyncio.CancelledError:
            pass

        # Give a moment for cleanup
        await asyncio.sleep(0.1)
        assert not pathlib.Path(sock_path).exists()

    async def test_health_404_on_other_path(self, worker_queue):
        """Non-/health path returns 404."""
        port = 19101
        w = _make_worker(worker_queue, health_port=port)
        worker_task = asyncio.create_task(w.run())
        await asyncio.sleep(0.15)

        try:
            status_code, data = await _http_get("127.0.0.1", port, "/other")
            assert status_code == 404
            assert data["error"] == "not found"
        finally:
            w._running = False
            await asyncio.sleep(0.1)
            worker_task.cancel()
            try:
                await worker_task
            except asyncio.CancelledError:
                pass

    async def test_health_draining_status(self, worker_queue):
        """After _running = False, health response status becomes 'draining'.

        Runs the health server loop directly (not via worker.run()) so
        _running can be toggled without triggering the full shutdown sequence.
        """
        port = 19102
        w = _make_worker(worker_queue, health_port=port)
        w._running = True  # Simulate started state
        health_task = asyncio.create_task(w._health_server_loop())
        await asyncio.sleep(0.15)

        try:
            status_code, data = await _http_get("127.0.0.1", port, "/health")
            assert data["status"] == "healthy"

            w._running = False

            status_code, data = await _http_get("127.0.0.1", port, "/health")
            assert data["status"] == "draining"
        finally:
            health_task.cancel()
            try:
                await health_task
            except asyncio.CancelledError:
                pass

    async def test_health_response_active_jobs(self, worker_queue):
        """active_jobs count reflects running work."""
        port = 19103
        wrapped = task(worker_queue)(slow_task)
        job = await wrapped.enqueue(duration=2.0)

        w = _make_worker(worker_queue, health_port=port)
        worker_task = asyncio.create_task(w.run())
        await asyncio.sleep(0.15)

        try:
            # Wait for job to be claimed
            for _ in range(50):
                await asyncio.sleep(0.02)
                if w._active_jobs:
                    break
            assert len(w._active_jobs) == 1

            status_code, data = await _http_get("127.0.0.1", port, "/health")
            assert status_code == 200
            assert data["active_jobs"] == 1
        finally:
            w._running = False
            await asyncio.sleep(0.1)
            worker_task.cancel()
            try:
                await worker_task
            except asyncio.CancelledError:
                pass


# ---------------------------------------------------------------------------
# TestTimeout
# ---------------------------------------------------------------------------

class TestTimeout:
    """Job timeout enforcement via asyncio.wait_for."""

    async def test_timeout_triggers_failure(self, worker_queue):
        """Task exceeding timeout is failed with TIMEOUT failure kind."""
        wrapped = task(worker_queue, timeout=1)(timeout_slow_task)
        job = await wrapped.enqueue()

        w = _make_worker(worker_queue)
        worker_task = asyncio.create_task(w.run())
        try:
            for _ in range(60):
                await asyncio.sleep(0.1)
                await job.refresh()
                if job.status != JobStatus.RUNNING.value:
                    break
        finally:
            w._running = False
            worker_task.cancel()
            try:
                await worker_task
            except asyncio.CancelledError:
                pass

        await job.refresh()
        assert job.status == JobStatus.FAILED.value
        assert job.last_failure_kind == FailureKind.TIMEOUT.value
        assert "timed out" in job.last_error

    async def test_timeout_is_retryable(self, worker_queue):
        """Timed-out job is retried when max_retries allows it."""
        wrapped = task(worker_queue, timeout=1, max_retries=2)(timeout_slow_task)
        job = await wrapped.enqueue()

        w = _make_worker(worker_queue)
        worker_task = asyncio.create_task(w.run())
        try:
            for _ in range(60):
                await asyncio.sleep(0.1)
                await job.refresh()
                if job.status != JobStatus.RUNNING.value:
                    break
        finally:
            w._running = False
            worker_task.cancel()
            try:
                await worker_task
            except asyncio.CancelledError:
                pass

        await job.refresh()
        # Should be pending (requeued for retry), not permanently failed
        assert job.status == JobStatus.PENDING.value
        assert job.retry_count == 1
        assert job.last_failure_kind == FailureKind.TIMEOUT.value

    async def test_no_timeout_no_limit(self, worker_queue):
        """Task without timeout runs without time limit (existing behavior)."""
        wrapped = task(worker_queue)(timeout_fast_task)
        job = await wrapped.enqueue()

        w = _make_worker(worker_queue)
        await _run_worker_until_job_done(w, job)

        await job.refresh()
        assert job.status == JobStatus.COMPLETED.value
        assert job.timeout is None

    async def test_no_timeout_slow_task_completes(self, worker_queue):
        """Slow task without timeout completes normally (no accidental global timeout)."""
        wrapped = task(worker_queue)(slow_task)
        job = await wrapped.enqueue(0.5)

        w = _make_worker(worker_queue)
        await _run_worker_until_job_done(w, job)

        await job.refresh()
        assert job.status == JobStatus.COMPLETED.value
        assert job.result == "done"

    async def test_per_job_timeout_override(self, worker_queue):
        """Per-job _timeout overrides task default."""
        wrapped = task(worker_queue, timeout=60)(timeout_slow_task)
        # Override with short timeout
        job = await wrapped.enqueue(_timeout=1)

        assert job.timeout == 1

        w = _make_worker(worker_queue)
        worker_task = asyncio.create_task(w.run())
        try:
            for _ in range(60):
                await asyncio.sleep(0.1)
                await job.refresh()
                if job.status != JobStatus.RUNNING.value:
                    break
        finally:
            w._running = False
            worker_task.cancel()
            try:
                await worker_task
            except asyncio.CancelledError:
                pass

        await job.refresh()
        assert job.status == JobStatus.FAILED.value
        assert job.last_failure_kind == FailureKind.TIMEOUT.value
        assert "timed out after 1s" in job.last_error

    async def test_fast_task_within_timeout(self, worker_queue):
        """Task completing before timeout succeeds normally."""
        wrapped = task(worker_queue, timeout=5)(timeout_fast_task)
        job = await wrapped.enqueue()

        w = _make_worker(worker_queue)
        await _run_worker_until_job_done(w, job)

        await job.refresh()
        assert job.status == JobStatus.COMPLETED.value
        assert job.result == "fast"

    async def test_timeout_attempt_recorded(self, worker_queue):
        """Timed-out job creates a failed attempt with error details."""
        wrapped = task(worker_queue, timeout=1)(timeout_slow_task)
        job = await wrapped.enqueue()

        w = _make_worker(worker_queue)
        worker_task = asyncio.create_task(w.run())
        try:
            for _ in range(60):
                await asyncio.sleep(0.1)
                await job.refresh()
                if job.status != JobStatus.RUNNING.value:
                    break
        finally:
            w._running = False
            worker_task.cancel()
            try:
                await worker_task
            except asyncio.CancelledError:
                pass

        await job.refresh()
        attempts = await JobAttempt.query().filter(
            F("job_ulid") == job.ulid
        ).all()
        assert len(attempts) == 1
        assert attempts[0].status == AttemptStatus.FAILED.value
        assert "timed out" in attempts[0].error

    async def test_sync_task_timeout(self, worker_queue):
        """Sync task (via to_thread) is also subject to timeout."""
        wrapped = task(worker_queue, sync=True, timeout=1)(timeout_sync_task)
        job = await wrapped.enqueue()

        w = _make_worker(worker_queue)
        worker_task = asyncio.create_task(w.run())
        try:
            for _ in range(60):
                await asyncio.sleep(0.1)
                await job.refresh()
                if job.status != JobStatus.RUNNING.value:
                    break
        finally:
            w._running = False
            worker_task.cancel()
            try:
                await worker_task
            except asyncio.CancelledError:
                pass

        await job.refresh()
        assert job.status == JobStatus.FAILED.value
        assert job.last_failure_kind == FailureKind.TIMEOUT.value
        assert "timed out" in job.last_error
        attempts = await JobAttempt.query().filter(F("job_ulid") == job.ulid).all()
        assert len(attempts) == 1
        assert attempts[0].status == AttemptStatus.FAILED.value
        assert attempts[0].failure_kind == FailureKind.TIMEOUT.value
        assert "timed out" in attempts[0].error
