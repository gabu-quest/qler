"""Shared fixtures for qler tests."""

import asyncio
import contextlib
import os
import sqlite3
import threading

import pytest
import pytest_asyncio


def pytest_sessionfinish(session, exitstatus):
    """Force-exit after test session to avoid asyncio event loop cleanup hang.

    pytest-asyncio's event loop teardown can deadlock on CI runners.
    All tests have passed/failed by this point; a 5-second grace period
    lets pytest write its summary before we force-exit.
    """
    threading.Timer(5.0, os._exit, args=[exitstatus]).start()


from sqler import AsyncSQLerDB
from sqler.models.async_queryset import AsyncSQLerQuerySet

from qler.queue import Queue
from qler.worker import Worker


def make_worker(queue, **overrides):
    """A Worker with fast test defaults: queue "default", one slot, 10 ms polling."""
    defaults = {
        "queues": ["default"],
        "concurrency": 1,
        "poll_interval": 0.01,
        "lease_recovery_interval": 60.0,
        "shutdown_timeout": 2.0,
    }
    defaults.update(overrides)
    return Worker(queue, **defaults)


async def run_worker_until_done(worker, job, timeout=5.0):
    """Run worker in background, wait for job to reach terminal state, stop worker."""
    worker_task = asyncio.create_task(worker.run())
    try:
        result = await job.wait(timeout=timeout, poll_interval=0.02)
        return result
    finally:
        worker._running = False
        await asyncio.sleep(0.05)
        worker_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await worker_task


@pytest_asyncio.fixture
async def db():
    """In-memory sqler database for testing."""
    _db = AsyncSQLerDB.in_memory(shared=False)
    await _db.connect()
    yield _db
    await _db.close()


@pytest_asyncio.fixture
async def queue(db):
    """Initialized Queue bound to the in-memory database."""
    q = Queue(db)
    await q.init_db()
    yield q


# ---------------------------------------------------------------------------
# On-disk DB fixtures (pool_size=4, exercises connection pool)
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def disk_db(tmp_path):
    """On-disk sqler database with pool_size=4 for pool-sensitive tests."""
    db_path = str(tmp_path / "test.db")
    _db = AsyncSQLerDB.on_disk(db_path)
    await _db.connect()
    yield _db
    await _db.close()


@pytest_asyncio.fixture
async def disk_queue(disk_db):
    """Queue bound to an on-disk database (pool_size=4)."""
    q = Queue(disk_db, default_lease_duration=5, default_max_retries=0)
    await q.init_db()
    yield q


# ---------------------------------------------------------------------------
# Pool health assertion helper
# ---------------------------------------------------------------------------


async def assert_pool_healthy(db):
    """Assert all connections are returned to the pool (no leaks).

    Call this after Worker shutdown in pool-sensitive tests.
    """
    stats = db.pool_stats()
    assert stats.available_connections == stats.max_connections, (
        f"Pool leak: {stats.available_connections}/{stats.max_connections} connections returned"
    )
    assert stats.in_use_connections == 0, "Connection still checked out"


@pytest.fixture
def raise_on_nth_update(monkeypatch):
    """Return ``arm(match, nth=1)``: make the nth ``update_one`` whose fields satisfy
    ``match`` raise ``database is locked`` once. Every other call runs as usual.
    """

    def arm(match, nth=1):
        original = AsyncSQLerQuerySet.update_one
        seen = {"n": 0}

        async def update_one(self, **fields):
            if match(fields):
                seen["n"] += 1
                if seen["n"] == nth:
                    raise sqlite3.OperationalError("database is locked")
            return await original(self, **fields)

        monkeypatch.setattr(AsyncSQLerQuerySet, "update_one", update_one)
        return seen

    return arm
