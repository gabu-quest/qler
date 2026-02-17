"""Shared fixtures for qler tests."""

import pytest
import pytest_asyncio
from sqler import AsyncSQLerDB

from qler.queue import Queue


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
