"""
Example: Two-layer auth testing pattern.

Layer 1: Endpoint tests with mocked auth (fast, focused on business logic)
Layer 2: Integration tests with real JWT verification
"""

import pytest
import httpx
from httpx import ASGITransport
from fastapi import FastAPI, Depends, HTTPException, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials

# ============================================================================
# Application Code
# ============================================================================

app = FastAPI()
security = HTTPBearer()


class User:
    """User model."""

    def __init__(self, id: str, role: str):
        self.id = id
        self.role = role


async def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(security),
) -> User:
    """Dependency: Get current authenticated user from JWT token."""
    token = credentials.credentials

    # In real app: decode JWT, verify signature, extract user
    if token == "valid_token":
        return User(id="alice", role="admin")
    elif token == "user_token":
        return User(id="bob", role="user")
    else:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token"
        )


@app.get("/admin/dashboard")
async def admin_dashboard(current_user: User = Depends(get_current_user)):
    """Admin-only endpoint."""
    if current_user.role != "admin":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Admin only")

    return {"message": "Admin dashboard", "user": current_user.id}


@app.get("/profile")
async def get_profile(current_user: User = Depends(get_current_user)):
    """Get current user's profile (any authenticated user)."""
    return {"user_id": current_user.id, "role": current_user.role}


# ============================================================================
# Layer 1: Endpoint Tests (Mock Auth Dependency)
# ============================================================================


@pytest.fixture(autouse=True)
def clear_overrides():
    """Clear dependency overrides after each test."""
    yield
    app.dependency_overrides.clear()


@pytest.fixture
async def client_as_admin():
    """Client authenticated as admin user."""

    async def mock_admin_user():
        return User(id="admin_user", role="admin")

    app.dependency_overrides[get_current_user] = mock_admin_user

    async with httpx.AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as ac:
        yield ac


@pytest.fixture
async def client_as_regular_user():
    """Client authenticated as regular user."""

    async def mock_regular_user():
        return User(id="regular_user", role="user")

    app.dependency_overrides[get_current_user] = mock_regular_user

    async with httpx.AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as ac:
        yield ac


@pytest.mark.asyncio
async def test_admin_endpoint_allows_admin(client_as_admin):
    """Admin endpoint should allow admin users (Layer 1: mocked auth)."""
    response = await client_as_admin.get("/admin/dashboard")

    assert response.status_code == 200
    assert response.json()["message"] == "Admin dashboard"


@pytest.mark.asyncio
async def test_admin_endpoint_rejects_regular_user(client_as_regular_user):
    """Admin endpoint should reject regular users (Layer 1: mocked auth)."""
    response = await client_as_regular_user.get("/admin/dashboard")

    assert response.status_code == 403
    assert "Admin only" in response.json()["detail"]


@pytest.mark.asyncio
async def test_profile_endpoint_allows_any_authenticated_user(client_as_regular_user):
    """Profile endpoint should allow any authenticated user."""
    response = await client_as_regular_user.get("/profile")

    assert response.status_code == 200
    assert response.json()["user_id"] == "regular_user"
    assert response.json()["role"] == "user"


# ============================================================================
# Layer 2: Integration Tests (Real JWT Verification)
# ============================================================================


@pytest.fixture
async def client_no_overrides():
    """Client without dependency overrides (uses real auth)."""
    async with httpx.AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as ac:
        yield ac


@pytest.mark.asyncio
@pytest.mark.integration
async def test_real_jwt_authentication_flow(client_no_overrides):
    """Full auth flow with real JWT verification (Layer 2: integration)."""
    # Valid token
    response = await client_no_overrides.get(
        "/profile", headers={"Authorization": "Bearer valid_token"}
    )

    assert response.status_code == 200
    assert response.json()["user_id"] == "alice"
    assert response.json()["role"] == "admin"


@pytest.mark.asyncio
@pytest.mark.integration
async def test_invalid_token_is_rejected(client_no_overrides):
    """Invalid token should be rejected (Layer 2: integration)."""
    response = await client_no_overrides.get(
        "/profile", headers={"Authorization": "Bearer invalid_token"}
    )

    assert response.status_code == 401
    assert "Invalid token" in response.json()["detail"]


@pytest.mark.asyncio
@pytest.mark.integration
async def test_missing_token_is_rejected(client_no_overrides):
    """Request without token should be rejected (Layer 2: integration)."""
    response = await client_no_overrides.get("/profile")

    assert response.status_code == 403  # HTTPBearer returns 403 for missing credentials
