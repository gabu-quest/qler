"""Shared fixtures for qler tests."""

import pytest_asyncio
from qler.queue import Queue
from sqler import AsyncSQLerDB


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
    adapter = db.adapter
    assert adapter._pool.qsize() == adapter._pool_size, (
        f"Pool leak: {adapter._pool.qsize()}/{adapter._pool_size} connections returned"
    )
    assert adapter._task_conn.get(None) is None, "Connection still pinned to task"
