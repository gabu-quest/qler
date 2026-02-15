"""
Example: FastAPI dependency override pattern.

This demonstrates the core pattern for testing FastAPI endpoints:
- Override dependencies instead of mocking internals
- Test through HTTP layer with httpx.AsyncClient
- Clear overrides after each test
"""

import pytest
import httpx
from httpx import ASGITransport
from fastapi import FastAPI, Depends

# ============================================================================
# Application Code (normally in app/)
# ============================================================================

app = FastAPI()


class Database:
    """Simulated database connection."""

    async def fetch_user(self, user_id: str):
        """Fetch user from database."""
        # In real app: SELECT * FROM users WHERE id = ?
        raise NotImplementedError("Real database connection")


async def get_db() -> Database:
    """Dependency: database connection."""
    return Database()


@app.get("/users/{user_id}")
async def get_user(user_id: str, db: Database = Depends(get_db)):
    """Get user by ID."""
    user = await db.fetch_user(user_id)
    return {"user_id": user.id, "name": user.name, "email": user.email}


# ============================================================================
# Test Code
# ============================================================================


class FakeDatabase:
    """Fake database for testing."""

    async def fetch_user(self, user_id: str):
        """Return fake user data."""
        # Simulated user data - no real database
        return type(
            "User",
            (),
            {"id": user_id, "name": "Alice Smith", "email": "alice@example.com"},
        )()


async def fake_db_dependency():
    """Fake database dependency."""
    return FakeDatabase()


@pytest.fixture(autouse=True)
def clear_dependency_overrides():
    """Clear dependency overrides after each test."""
    yield
    app.dependency_overrides.clear()


@pytest.fixture
async def client():
    """HTTP client for testing."""
    # Override database dependency
    app.dependency_overrides[get_db] = fake_db_dependency

    async with httpx.AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as ac:
        yield ac


# ============================================================================
# Tests
# ============================================================================


@pytest.mark.asyncio
async def test_get_user_returns_user_data(client):
    """GET /users/{id} should return user data with 200 status."""
    response = await client.get("/users/alice")

    assert response.status_code == 200
    assert response.json() == {
        "user_id": "alice",
        "name": "Alice Smith",
        "email": "alice@example.com",
    }


@pytest.mark.asyncio
async def test_get_user_with_different_id(client):
    """GET /users/{id} should return user with matching ID."""
    response = await client.get("/users/bob")

    assert response.status_code == 200
    json = response.json()
    assert json["user_id"] == "bob"
    assert json["name"] is not None


@pytest.mark.asyncio
async def test_dependency_override_is_isolated():
    """Each test gets clean dependency overrides."""
    # First test: override with fake
    app.dependency_overrides[get_db] = fake_db_dependency

    async with httpx.AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.get("/users/test")
        assert response.status_code == 200

    # Clear overrides (normally done by fixture)
    app.dependency_overrides.clear()

    # Verify overrides were cleared
    assert len(app.dependency_overrides) == 0
