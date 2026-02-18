"""Tests for rate limiting — parse_rate, try_acquire, task/queue integration."""

import asyncio

import pytest
import pytest_asyncio
from sqler import AsyncSQLerDB, F

from qler.enums import JobStatus
from qler.exceptions import ConfigurationError
from qler.models.job import Job
from qler.queue import Queue
from qler.rate_limit import RateSpec, parse_rate, try_acquire
from qler.task import TaskWrapper, task
from qler.worker import Worker


# Module-level functions (required by @task's qualname validation)
async def send_email(to: str):
    return f"sent to {to}"


async def noop_task():
    return "ok"


async def unlimited_task():
    return "unlimited"


@pytest_asyncio.fixture
async def db():
    _db = AsyncSQLerDB.in_memory(shared=False)
    await _db.connect()
    yield _db
    await _db.close()


@pytest_asyncio.fixture
async def queue(db):
    q = Queue(db)
    await q.init_db()
    yield q


# ------------------------------------------------------------------
# parse_rate
# ------------------------------------------------------------------


class TestParseRate:

    def test_per_second(self):
        spec = parse_rate("5/s")
        assert spec.limit == 5
        assert spec.window_seconds == 1

    def test_per_minute(self):
        spec = parse_rate("10/m")
        assert spec.limit == 10
        assert spec.window_seconds == 60

    def test_per_hour(self):
        spec = parse_rate("100/h")
        assert spec.limit == 100
        assert spec.window_seconds == 3600

    def test_per_day(self):
        spec = parse_rate("1000/d")
        assert spec.limit == 1000
        assert spec.window_seconds == 86400

    def test_invalid_format(self):
        with pytest.raises(ConfigurationError, match="Invalid rate limit spec"):
            parse_rate("abc")

    def test_missing_unit(self):
        with pytest.raises(ConfigurationError, match="Invalid rate limit spec"):
            parse_rate("10/x")

    def test_refill_rate(self):
        spec = parse_rate("10/m")
        assert abs(spec.refill_rate - 10 / 60) < 1e-9


# ------------------------------------------------------------------
# try_acquire
# ------------------------------------------------------------------


class TestTryAcquire:

    async def test_allows_fresh_bucket(self, queue):
        """First request on a new bucket should succeed."""
        rate = parse_rate("10/m")
        result = await try_acquire("test:fresh", rate)
        assert result is True

    async def test_exhausted_bucket(self, queue):
        """Consuming all tokens should block subsequent requests."""
        rate = parse_rate("3/m")
        results = []
        for _ in range(5):
            results.append(await try_acquire("test:exhaust", rate))
        # First 3 should succeed, last 2 should fail
        assert results[:3] == [True, True, True]
        assert results[3:] == [False, False]

    async def test_refills_over_time(self, queue):
        """After time passes, tokens should refill."""
        rate = parse_rate("1/s")

        # Consume the token
        result1 = await try_acquire("test:refill", rate)
        assert result1 is True

        # Immediately should fail
        result2 = await try_acquire("test:refill", rate)
        assert result2 is False

        # Wait for refill (1 token/second)
        await asyncio.sleep(1.1)

        # Should succeed again
        result3 = await try_acquire("test:refill", rate)
        assert result3 is True


# ------------------------------------------------------------------
# @task rate_limit integration
# ------------------------------------------------------------------


class TestTaskRateLimit:

    async def test_decorator_stores_rate_spec(self, queue):
        """@task(rate_limit='10/m') stores RateSpec on TaskWrapper."""
        wrapped = task(queue, rate_limit="10/m")(send_email)
        assert wrapped.rate_spec is not None
        assert wrapped.rate_spec.limit == 10
        assert wrapped.rate_spec.window_seconds == 60

    async def test_decorator_no_rate_limit(self, queue):
        """@task without rate_limit has None rate_spec."""
        wrapped = task(queue)(noop_task)
        assert wrapped.rate_spec is None

    async def test_invalid_rate_limit_at_decoration(self, queue):
        """Invalid rate_limit string raises ConfigurationError at decoration."""
        with pytest.raises(ConfigurationError, match="Invalid rate limit spec"):
            task(queue, rate_limit="bad")(noop_task)


# ------------------------------------------------------------------
# claim_job rate limiting integration
# ------------------------------------------------------------------


class TestClaimRateLimit:

    async def test_rate_limited_requeues(self, queue):
        """Exhausted task-level bucket requeues job with delayed ETA."""
        wrapped = task(queue, rate_limit="1/m")(send_email)

        # Enqueue 3 jobs
        j1 = await wrapped.enqueue("a@test.com")
        j2 = await wrapped.enqueue("b@test.com")
        j3 = await wrapped.enqueue("c@test.com")

        # First claim should succeed (consumes 1 token)
        claimed1 = await queue.claim_job("worker-1", ["default"])
        assert claimed1 is not None
        assert claimed1.status == JobStatus.RUNNING.value

        # Second claim should be rate-limited → requeue
        claimed2 = await queue.claim_job("worker-1", ["default"])
        assert claimed2 is None  # Rate limited, returned None

        # The requeued job should have a delayed ETA
        remaining = await Job.query().filter(
            F("status") == JobStatus.PENDING.value
        ).all()
        assert len(remaining) == 2
        # At least one should have a future ETA (delayed)
        future_etas = [j for j in remaining if j.eta > j1.eta]
        assert len(future_etas) >= 1

    async def test_rate_limit_allows(self, queue):
        """With tokens available, job is claimed normally."""
        wrapped = task(queue, rate_limit="100/m")(send_email)

        await wrapped.enqueue("a@test.com")
        claimed = await queue.claim_job("worker-1", ["default"])
        assert claimed is not None
        assert claimed.status == JobStatus.RUNNING.value

    async def test_queue_level_rate_limit(self, db):
        """Queue-level rate limit blocks claims when exhausted."""
        q = Queue(db, rate_limits={"default": "1/m"})
        await q.init_db()

        wrapped = task(q)(noop_task)

        await wrapped.enqueue()
        await wrapped.enqueue()

        # First claim should succeed
        claimed1 = await q.claim_job("worker-1", ["default"])
        assert claimed1 is not None

        # Second claim should be rate-limited
        claimed2 = await q.claim_job("worker-1", ["default"])
        assert claimed2 is None

    async def test_immediate_mode_bypasses(self, db):
        """Immediate mode ignores rate limits."""
        q = Queue(db, immediate=True, rate_limits={"default": "1/m"})
        await q.init_db()

        wrapped = task(q, rate_limit="1/m")(send_email)

        # Both should execute immediately without rate limiting
        j1 = await wrapped.enqueue("a@test.com")
        assert j1.status == JobStatus.COMPLETED.value

        j2 = await wrapped.enqueue("b@test.com")
        assert j2.status == JobStatus.COMPLETED.value
