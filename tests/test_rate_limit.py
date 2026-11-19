"""Tests for rate limiting — parse_rate, try_acquire, task/queue integration."""

import asyncio

import pytest
import pytest_asyncio
from sqler import AsyncSQLerDB, F

from qler.enums import JobStatus
from qler.exceptions import ConfigurationError
from qler.models.bucket import RateLimitBucket
from qler.models.job import Job
from qler.queue import Queue
from qler.rate_limit import parse_rate, try_acquire
from qler.task import task


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

    async def test_allows_fresh_bucket(self, queue, db):
        """First request on a new bucket should succeed."""
        rate = parse_rate("10/m")
        result = await try_acquire("test:fresh", rate, db=db)
        assert result is True

    async def test_exhausted_bucket(self, queue, db):
        """Consuming all tokens should block subsequent requests."""
        rate = parse_rate("3/m")
        results = []
        for _ in range(5):
            results.append(await try_acquire("test:exhaust", rate, db=db))
        # First 3 should succeed, last 2 should fail
        assert results[:3] == [True, True, True]
        assert results[3:] == [False, False]

    async def test_refills_over_time(self, queue, db):
        """After time passes, tokens should refill."""
        rate = parse_rate("1/s")

        # Consume the token
        result1 = await try_acquire("test:refill", rate, db=db)
        assert result1 is True

        # Immediately should fail
        result2 = await try_acquire("test:refill", rate, db=db)
        assert result2 is False

        # Wait for refill (1 token/second)
        await asyncio.sleep(1.1)

        # Should succeed again
        result3 = await try_acquire("test:refill", rate, db=db)
        assert result3 is True


# ------------------------------------------------------------------
# try_acquire under concurrent callers: the decision and its write are one
# ------------------------------------------------------------------


async def _bucket_rows(db, key):
    return await RateLimitBucket.using(db).filter(F("bucket_key") == key).all()


FROZEN_NOW = 1_000_000


@pytest.fixture
def frozen_clock(monkeypatch):
    """Hold the limiter's clock still, so every call falls in one window and none refills."""
    import qler.rate_limit as rate_limit_module

    monkeypatch.setattr(rate_limit_module, "now_epoch", lambda: FROZEN_NOW)


class TestTryAcquireConcurrent:

    async def test_first_use_from_eight_tasks(self, disk_queue, disk_db, frozen_clock):
        """Eight tasks race on a key nobody used yet: exactly the limit pass, one row."""
        rate = parse_rate("3/m")
        results = await asyncio.gather(
            *(try_acquire("test:first", rate, db=disk_db) for _ in range(8)),
            return_exceptions=True,
        )
        assert sorted(results, key=repr) == [False] * 5 + [True] * 3
        rows = await _bucket_rows(disk_db, "test:first")
        assert len(rows) == 1
        assert rows[0].tokens == 0.0

    async def test_existing_bucket_from_eight_tasks(
        self, disk_queue, disk_db, frozen_clock
    ):
        """Eight tasks race on a full bucket that already exists: exactly the limit pass."""
        rate = parse_rate("3/m")
        await RateLimitBucket(
            bucket_key="test:existing",
            tokens=3.0,
            max_tokens=3,
            refill_rate=rate.refill_rate,
            last_refill_at=FROZEN_NOW,
        ).save(db=disk_db)
        results = await asyncio.gather(
            *(try_acquire("test:existing", rate, db=disk_db) for _ in range(8)),
            return_exceptions=True,
        )
        assert sorted(results, key=repr) == [False] * 5 + [True] * 3
        rows = await _bucket_rows(disk_db, "test:existing")
        assert len(rows) == 1
        assert rows[0].tokens == 0.0

    async def test_first_use_from_two_queues_on_one_file(self, tmp_path, frozen_clock):
        """Two Queue objects (two pools) on one file stand in for two processes."""
        path = str(tmp_path / "two.db")
        db_a = AsyncSQLerDB.on_disk(path)
        db_b = AsyncSQLerDB.on_disk(path)
        await db_a.connect()
        await db_b.connect()
        try:
            qa = Queue(db_a)
            await qa.init_db()
            qb = Queue(db_b)
            await qb.init_db()
            rate = parse_rate("3/m")
            results = await asyncio.gather(
                *(try_acquire("test:two", rate, db=db_a) for _ in range(4)),
                *(try_acquire("test:two", rate, db=db_b) for _ in range(4)),
                return_exceptions=True,
            )
            assert sorted(results, key=repr) == [False] * 5 + [True] * 3
            rows = await _bucket_rows(db_a, "test:two")
            assert len(rows) == 1
            assert rows[0].tokens == 0.0
        finally:
            await db_a.close()
            await db_b.close()

    async def test_clock_stepping_back_gives_no_extra_token(self, queue, db, monkeypatch):
        """A clock that steps back never yields more than the limit, nor negative tokens."""
        import qler.rate_limit as rate_limit_module

        clock = [1000]
        monkeypatch.setattr(rate_limit_module, "now_epoch", lambda: clock[0])
        rate = parse_rate("3/m")

        results = [await try_acquire("test:clock", rate, db=db) for _ in range(3)]
        token_counts = [(await _bucket_rows(db, "test:clock"))[0].tokens]
        clock[0] = 940  # one whole window back
        results.append(await try_acquire("test:clock", rate, db=db))
        token_counts.append((await _bucket_rows(db, "test:clock"))[0].tokens)
        clock[0] = 1000  # back to where it was: still inside the same window
        results.append(await try_acquire("test:clock", rate, db=db))
        token_counts.append((await _bucket_rows(db, "test:clock"))[0].tokens)

        assert results == [True, True, True, False, False]
        assert token_counts == [0.0, 0.0, 0.0]

    async def test_locked_database_denies(self, tmp_path, frozen_clock):
        """When the write lock cannot be had within the busy timeout, the answer is False."""
        path = str(tmp_path / "locked.db")
        db_main = AsyncSQLerDB.on_disk(path, timeout_ms=100)
        db_other = AsyncSQLerDB.on_disk(path)
        await db_main.connect()
        await db_other.connect()
        try:
            q = Queue(db_main)
            await q.init_db()
            rate = parse_rate("3/m")
            assert await try_acquire("test:locked", rate, db=db_main) is True
            async with db_other.transaction():
                result = await try_acquire("test:locked", rate, db=db_main)
            assert result is False
            rows = await _bucket_rows(db_main, "test:locked")
            assert [r.tokens for r in rows] == [2.0]
        finally:
            await db_main.close()
            await db_other.close()


class TestChangedRate:

    async def test_raised_limit_applies_one_window_later(self, queue, db, monkeypatch):
        """A bucket emptied at 2/m, called at 5/m a window later, gives exactly 5 tokens."""
        import qler.rate_limit as rate_limit_module

        clock = [FROZEN_NOW]
        monkeypatch.setattr(rate_limit_module, "now_epoch", lambda: clock[0])
        old = parse_rate("2/m")
        new = parse_rate("5/m")

        assert [await try_acquire("test:raise", old, db=db) for _ in range(3)] == [
            True,
            True,
            False,
        ]
        clock[0] += 60
        results = [await try_acquire("test:raise", new, db=db) for _ in range(6)]

        assert results == [True] * 5 + [False]
        rows = await _bucket_rows(db, "test:raise")
        assert [(r.max_tokens, r.refill_rate) for r in rows] == [(5, new.refill_rate)]

    async def test_lowered_limit_applies_at_once(self, queue, db, frozen_clock):
        """A bucket holding 4 tokens at 5/m, called at 2/m, is capped at 2 right away."""
        old = parse_rate("5/m")
        new = parse_rate("2/m")

        assert await try_acquire("test:lower", old, db=db) is True
        assert (await _bucket_rows(db, "test:lower"))[0].tokens == 4.0
        results = [await try_acquire("test:lower", new, db=db) for _ in range(3)]

        assert results == [True, True, False]
        rows = await _bucket_rows(db, "test:lower")
        assert [(r.max_tokens, r.refill_rate) for r in rows] == [(2, new.refill_rate)]


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
        assert len(future_etas) == 1

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
