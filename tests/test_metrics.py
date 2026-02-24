"""Tests for M22: Prometheus metrics."""

from __future__ import annotations

import asyncio
from unittest.mock import patch

import pytest
import pytest_asyncio

prometheus_client = pytest.importorskip("prometheus_client")

from prometheus_client import CollectorRegistry
from sqler import AsyncSQLerDB, F

from qler._time import now_epoch
from qler.enums import FailureKind, JobStatus
from qler.metrics import QlerMetrics
from qler.models.job import Job
from qler.queue import Queue
from qler.task import task
from qler.worker import Worker


# ---------------------------------------------------------------------------
# Module-level task functions (required — @task rejects nested functions)
# ---------------------------------------------------------------------------


async def counter_task(x=1):
    return x


async def success_task():
    return "ok"


async def fail_task():
    raise ValueError("boom")


async def retry_task():
    raise ValueError("oops")


async def terminal_task():
    raise ValueError("done")


async def lease_task():
    pass


async def email_task():
    pass


async def report_task():
    pass


async def task_a():
    pass


async def task_b():
    pass


async def claim_counter_task():
    pass


async def timeout_fail_task():
    raise ValueError("timeout")


async def fast_task():
    return 42


async def boom_task():
    raise ValueError("boom")


async def depth_task():
    pass


async def endpoint_task():
    pass


async def lifecycle_task(x):
    return x * 2


async def batch_task(x):
    return x


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def mq():
    """Queue with metrics enabled (private registry, own DB)."""
    _db = AsyncSQLerDB.in_memory(shared=False)
    await _db.connect()
    q = Queue(_db, metrics=True)
    await q.init_db()
    yield q
    await _db.close()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _sample_value(registry, name, labels=None):
    """Extract a single sample value from a registry."""
    for metric in registry.collect():
        for sample in metric.samples:
            if sample.name == name:
                if labels is None or sample.labels == labels:
                    return sample.value
    return None


def _sample_values(registry, name):
    """Extract all sample values for a metric name as {labels_tuple: value}."""
    result = {}
    for metric in registry.collect():
        for sample in metric.samples:
            if sample.name == name:
                key = tuple(sorted(sample.labels.items()))
                result[key] = sample.value
    return result


TASK_PREFIX = f"{counter_task.__module__}."


# ---------------------------------------------------------------------------
# Opt-in tests
# ---------------------------------------------------------------------------


class TestOptIn:
    @pytest_asyncio.fixture
    async def db(self):
        _db = AsyncSQLerDB.in_memory(shared=False)
        await _db.connect()
        yield _db
        await _db.close()

    async def test_metrics_disabled_by_default(self, db):
        q = Queue(db)
        assert q._metrics is None

    async def test_metrics_enabled_with_true(self, db):
        q = Queue(db, metrics=True)
        assert q._metrics is not None
        assert isinstance(q._metrics, QlerMetrics)
        assert q._metrics.registry is not None
        assert q._metrics.jobs_enqueued is not None

    async def test_metrics_with_custom_registry(self, db):
        reg = CollectorRegistry()
        q = Queue(db, metrics=reg)
        assert q._metrics is not None
        assert q._metrics.registry is reg

    async def test_metrics_true_without_prometheus_raises(self, db):
        with patch.dict("sys.modules", {"prometheus_client": None}):
            with pytest.raises(ImportError):
                QlerMetrics()

    async def test_metrics_invalid_type_raises(self, db):
        from qler.exceptions import ConfigurationError

        with pytest.raises(ConfigurationError, match="CollectorRegistry instance"):
            Queue(db, metrics="invalid")


# ---------------------------------------------------------------------------
# Counter tests
# ---------------------------------------------------------------------------


class TestCounters:
    async def test_enqueue_increments_counter(self, mq):
        reg = mq._metrics.registry
        tw = task(mq)(counter_task)

        await tw.enqueue(1)
        val = _sample_value(reg, "qler_jobs_enqueued_total", {"queue": "default", "task": TASK_PREFIX + "counter_task"})
        assert val == 1.0

        await tw.enqueue(2)
        val = _sample_value(reg, "qler_jobs_enqueued_total", {"queue": "default", "task": TASK_PREFIX + "counter_task"})
        assert val == 2.0

    async def test_claim_increments_counter(self, mq):
        reg = mq._metrics.registry
        task(mq)(claim_counter_task)

        await mq.enqueue(TASK_PREFIX + "claim_counter_task")
        await mq.claim_job("w1", ["default"])

        val = _sample_value(reg, "qler_jobs_claimed_total", {"queue": "default", "task": TASK_PREFIX + "claim_counter_task"})
        assert val == 1.0

    async def test_complete_increments_counter(self, mq):
        reg = mq._metrics.registry
        task(mq)(success_task)

        await mq.enqueue(TASK_PREFIX + "success_task")
        claimed = await mq.claim_job("w1", ["default"])
        assert claimed is not None
        await mq.complete_job(claimed, "w1", result="ok")

        val = _sample_value(reg, "qler_jobs_completed_total", {"queue": "default", "task": TASK_PREFIX + "success_task"})
        assert val == 1.0

    async def test_fail_increments_counter_with_kind(self, mq):
        reg = mq._metrics.registry
        task(mq)(fail_task)

        await mq.enqueue(TASK_PREFIX + "fail_task")
        claimed = await mq.claim_job("w1", ["default"])
        assert claimed is not None
        await mq.fail_job(claimed, "w1", ValueError("boom"), failure_kind=FailureKind.EXCEPTION)

        val = _sample_value(
            reg,
            "qler_jobs_failed_total",
            {"queue": "default", "task": TASK_PREFIX + "fail_task", "failure_kind": "exception"},
        )
        assert val == 1.0

    async def test_retry_increments_retry_counter(self, mq):
        """Retryable failure -> retried counter, NOT failed counter."""
        reg = mq._metrics.registry
        tw = task(mq, max_retries=3)(retry_task)

        await tw.enqueue()
        claimed = await mq.claim_job("w1", ["default"])
        assert claimed is not None
        await mq.fail_job(claimed, "w1", ValueError("oops"), failure_kind=FailureKind.EXCEPTION)

        retried = _sample_value(reg, "qler_jobs_retried_total", {"queue": "default", "task": TASK_PREFIX + "retry_task"})
        assert retried == 1.0

        failed = _sample_value(
            reg,
            "qler_jobs_failed_total",
            {"queue": "default", "task": TASK_PREFIX + "retry_task", "failure_kind": "exception"},
        )
        assert failed is None

    async def test_terminal_fail_increments_failed_not_retried(self, mq):
        """Terminal failure -> failed counter, NOT retried counter."""
        reg = mq._metrics.registry
        task(mq)(terminal_task)

        await mq.enqueue(TASK_PREFIX + "terminal_task")
        claimed = await mq.claim_job("w1", ["default"])
        assert claimed is not None
        await mq.fail_job(claimed, "w1", ValueError("done"), failure_kind=FailureKind.EXCEPTION)

        failed = _sample_value(
            reg,
            "qler_jobs_failed_total",
            {"queue": "default", "task": TASK_PREFIX + "terminal_task", "failure_kind": "exception"},
        )
        assert failed == 1.0

        retried = _sample_value(reg, "qler_jobs_retried_total", {"queue": "default", "task": TASK_PREFIX + "terminal_task"})
        assert retried is None

    async def test_timeout_failure_kind_label(self, mq):
        """TIMEOUT failure kind label recorded correctly on failed counter."""
        reg = mq._metrics.registry
        task(mq)(timeout_fail_task)

        await mq.enqueue(TASK_PREFIX + "timeout_fail_task")
        claimed = await mq.claim_job("w1", ["default"])
        assert claimed is not None
        await mq.fail_job(
            claimed, "w1",
            TimeoutError("timed out"),
            failure_kind=FailureKind.TIMEOUT,
        )

        val = _sample_value(
            reg,
            "qler_jobs_failed_total",
            {"queue": "default", "task": TASK_PREFIX + "timeout_fail_task", "failure_kind": "timeout"},
        )
        assert val == 1.0

    async def test_lease_recovery_counter(self, mq):
        reg = mq._metrics.registry
        task(mq)(lease_task)

        await mq.enqueue(TASK_PREFIX + "lease_task")
        claimed = await mq.claim_job("w1", ["default"])
        assert claimed is not None

        # Expire the lease by backdating it
        await Job.query().filter(F("ulid") == claimed.ulid).update_one(
            lease_expires_at=now_epoch() - 100,
        )

        recovered = await mq.recover_expired_leases()
        assert recovered == 1

        val = _sample_value(reg, "qler_leases_recovered_total")
        assert val == 1.0

    async def test_labels_distinguish_queues(self, mq):
        reg = mq._metrics.registry
        task(mq, queue_name="emails")(email_task)
        task(mq, queue_name="reports")(report_task)

        await mq.enqueue(TASK_PREFIX + "email_task", queue_name="emails")
        await mq.enqueue(TASK_PREFIX + "report_task", queue_name="reports")

        email_val = _sample_value(reg, "qler_jobs_enqueued_total", {"queue": "emails", "task": TASK_PREFIX + "email_task"})
        report_val = _sample_value(reg, "qler_jobs_enqueued_total", {"queue": "reports", "task": TASK_PREFIX + "report_task"})
        assert email_val == 1.0
        assert report_val == 1.0

    async def test_labels_distinguish_tasks(self, mq):
        reg = mq._metrics.registry
        task(mq)(task_a)
        task(mq)(task_b)

        await mq.enqueue(TASK_PREFIX + "task_a")
        await mq.enqueue(TASK_PREFIX + "task_a")
        await mq.enqueue(TASK_PREFIX + "task_b")

        a_val = _sample_value(reg, "qler_jobs_enqueued_total", {"queue": "default", "task": TASK_PREFIX + "task_a"})
        b_val = _sample_value(reg, "qler_jobs_enqueued_total", {"queue": "default", "task": TASK_PREFIX + "task_b"})
        assert a_val == 2.0
        assert b_val == 1.0


# ---------------------------------------------------------------------------
# Histogram tests
# ---------------------------------------------------------------------------


class TestHistogram:
    async def test_duration_observed_on_complete(self, mq):
        reg = mq._metrics.registry
        task(mq)(fast_task)

        worker = Worker(mq, queues=["default"], concurrency=1, poll_interval=0.05)
        await mq.enqueue(TASK_PREFIX + "fast_task")

        async def stop_after_one():
            while True:
                await asyncio.sleep(0.05)
                jobs = await Job.query().all()
                if any(j.status == JobStatus.COMPLETED.value for j in jobs):
                    worker._running = False
                    return

        await asyncio.gather(worker.run(), stop_after_one())

        count = _sample_value(reg, "qler_job_duration_seconds_count", {"queue": "default", "task": TASK_PREFIX + "fast_task"})
        assert count == 1.0

        total = _sample_value(reg, "qler_job_duration_seconds_sum", {"queue": "default", "task": TASK_PREFIX + "fast_task"})
        assert 0 < total < 5.0  # instant task, must be positive but not absurd

    async def test_duration_observed_on_fail(self, mq):
        reg = mq._metrics.registry
        task(mq)(boom_task)

        worker = Worker(mq, queues=["default"], concurrency=1, poll_interval=0.05)
        await mq.enqueue(TASK_PREFIX + "boom_task")

        async def stop_after_one():
            while True:
                await asyncio.sleep(0.05)
                jobs = await Job.query().all()
                if any(j.status == JobStatus.FAILED.value for j in jobs):
                    worker._running = False
                    return

        await asyncio.gather(worker.run(), stop_after_one())

        count = _sample_value(reg, "qler_job_duration_seconds_count", {"queue": "default", "task": TASK_PREFIX + "boom_task"})
        assert count == 1.0


# ---------------------------------------------------------------------------
# Gauge tests (queue depth)
# ---------------------------------------------------------------------------


class TestQueueDepth:
    async def test_queue_depth_reflects_db_state(self, mq):
        reg = mq._metrics.registry
        task(mq)(depth_task)

        await mq.enqueue(TASK_PREFIX + "depth_task")
        await mq.enqueue(TASK_PREFIX + "depth_task")
        await mq.enqueue(TASK_PREFIX + "depth_task")

        await mq.claim_job("w1", ["default"])

        await mq._metrics.update_depth()

        depths = _sample_values(reg, "qler_queue_depth")
        pending_key = tuple(sorted({"queue": "default", "status": "pending"}.items()))
        running_key = tuple(sorted({"queue": "default", "status": "running"}.items()))

        assert depths.get(pending_key) == 2
        assert depths.get(running_key) == 1

    async def test_queue_depth_empty_db(self, mq):
        reg = mq._metrics.registry
        await mq._metrics.update_depth()
        depths = _sample_values(reg, "qler_queue_depth")
        assert len(depths) == 0

    async def test_update_depth_noop_when_not_wired(self):
        """update_depth returns silently when no query function is set."""
        reg = CollectorRegistry()
        m = QlerMetrics(registry=reg)
        await m.update_depth()  # Must not raise
        depths = _sample_values(reg, "qler_queue_depth")
        assert len(depths) == 0

    async def test_generate_returns_prometheus_text_bytes(self, mq):
        output = mq._metrics.generate()
        assert isinstance(output, bytes)
        assert b"qler_jobs_enqueued_total" in output
        assert b"qler_job_duration_seconds" in output


# ---------------------------------------------------------------------------
# Endpoint tests
# ---------------------------------------------------------------------------


class TestMetricsEndpoint:
    async def test_metrics_endpoint_returns_text_format(self, mq):
        task(mq)(endpoint_task)
        worker = Worker(mq, queues=["default"], concurrency=1, health_port=19191, poll_interval=0.1)

        await mq.enqueue(TASK_PREFIX + "endpoint_task")

        async def query_endpoint():
            for _ in range(50):
                try:
                    reader, writer = await asyncio.open_connection("127.0.0.1", 19191)
                    writer.write(b"GET /metrics HTTP/1.1\r\nHost: localhost\r\n\r\n")
                    await writer.drain()
                    data = await asyncio.wait_for(reader.read(65536), timeout=5)
                    writer.close()
                    await writer.wait_closed()

                    text = data.decode("utf-8")
                    assert "HTTP/1.1 200 OK" in text
                    assert "text/plain; version=0.0.4" in text
                    assert "qler_jobs_enqueued_total" in text
                    worker._running = False
                    return
                except (ConnectionRefusedError, OSError):
                    await asyncio.sleep(0.05)

            worker._running = False
            pytest.fail("Could not connect to metrics endpoint")

        await asyncio.gather(worker.run(), query_endpoint())

    async def test_metrics_endpoint_404_when_disabled(self, db):
        q = Queue(db)
        await q.init_db()

        worker = Worker(q, queues=["default"], concurrency=1, health_port=19192, poll_interval=0.1)

        async def query_endpoint():
            for _ in range(50):
                try:
                    reader, writer = await asyncio.open_connection("127.0.0.1", 19192)
                    writer.write(b"GET /metrics HTTP/1.1\r\nHost: localhost\r\n\r\n")
                    await writer.drain()
                    data = await asyncio.wait_for(reader.read(65536), timeout=5)
                    writer.close()
                    await writer.wait_closed()

                    text = data.decode("utf-8")
                    assert "HTTP/1.1 404" in text
                    worker._running = False
                    return
                except (ConnectionRefusedError, OSError):
                    await asyncio.sleep(0.05)

            worker._running = False
            pytest.fail("Could not connect to health endpoint")

        await asyncio.gather(worker.run(), query_endpoint())


# ---------------------------------------------------------------------------
# Integration tests
# ---------------------------------------------------------------------------


class TestIntegration:
    async def test_full_lifecycle_metrics(self, mq):
        """Enqueue -> claim -> complete: verify all counters + histogram via worker."""
        reg = mq._metrics.registry
        task(mq)(lifecycle_task)

        worker = Worker(mq, queues=["default"], concurrency=1, poll_interval=0.05)
        await mq.enqueue(TASK_PREFIX + "lifecycle_task", args=(5,))

        async def stop_when_done():
            while True:
                await asyncio.sleep(0.05)
                jobs = await Job.query().all()
                if any(j.status == JobStatus.COMPLETED.value for j in jobs):
                    worker._running = False
                    return

        await asyncio.gather(worker.run(), stop_when_done())

        assert _sample_value(reg, "qler_jobs_enqueued_total", {"queue": "default", "task": TASK_PREFIX + "lifecycle_task"}) == 1.0
        assert _sample_value(reg, "qler_jobs_claimed_total", {"queue": "default", "task": TASK_PREFIX + "lifecycle_task"}) == 1.0
        assert _sample_value(reg, "qler_jobs_completed_total", {"queue": "default", "task": TASK_PREFIX + "lifecycle_task"}) == 1.0
        assert _sample_value(reg, "qler_job_duration_seconds_count", {"queue": "default", "task": TASK_PREFIX + "lifecycle_task"}) == 1.0

    async def test_enqueue_many_increments_counter(self, mq):
        reg = mq._metrics.registry
        tw = task(mq)(batch_task)

        await tw.enqueue_many([(1,), (2,), (3,)])

        val = _sample_value(reg, "qler_jobs_enqueued_total", {"queue": "default", "task": TASK_PREFIX + "batch_task"})
        assert val == 3.0
