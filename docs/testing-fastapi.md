# Testing Doctrine: FastAPI
## Modern Patterns for Testing FastAPI Applications

**Part of The Standard** | [Core Testing Doctrine](./testing.md) | **Version 1.0.0**
**Date:** 2025-12-14

---

## Prerequisites

**You MUST read [testing.md](./testing.md) first.**

This guide assumes you understand:
- Core testing principles (determinism, meaningful assertions, public API testing)
- Test taxonomy (unit/integration/feature/E2E)
- Mocking doctrine (mock boundaries, not internals)

This guide focuses ONLY on FastAPI-specific patterns.

**In case of conflict, core doctrine wins.**

---

## Prime Directive

**Dependency override > mocking. Test through ASGI, not HTTP.**

FastAPI's dependency injection system is designed for testing. Use it.

---

## 1. Framework Context

FastAPI applications have unique testing needs:
- **Async-first** - Most FastAPI apps use async handlers
- **Dependency injection** - FastAPI's DI system enables clean test isolation
- **Lifespan events** - Startup/shutdown hooks need explicit handling
- **Multiple protocols** - HTTP, WebSocket, Server-Sent Events
- **ASGI layer** - Testing through ASGI is faster than HTTP

This guide shows you how to test FastAPI apps correctly, using the framework's strengths rather than fighting them.

---

## 2. Tool Stack

### 2.1 Required tools

You MUST use:
- **`pytest`** - Test runner
- **`httpx`** - Modern async HTTP client for FastAPI testing
- **`pytest-asyncio`** or **`pytest-anyio`** - Async test support

### 2.2 Optional tools

You MAY use:
- **`asgi-lifespan`** - If your app relies on startup/shutdown events
- **`pytest-mock`** - For mocking at boundaries (external APIs, etc.)
- **`respx`** - For mocking outbound `httpx` calls

### 2.3 Version requirements

- FastAPI 0.100+
- httpx 0.24+
- pytest-asyncio 0.21+ or pytest-anyio 3.0+

---

## 3. Testing Layers

FastAPI testing has three distinct layers:

### 3.1 Unit tests (handler logic)

**Purpose:** Test business logic in isolation.

**Pattern:** Test functions directly, without HTTP layer.

**Example:**
```python
# app/services.py
async def calculate_subscription_price(user_id: str, plan: str) -> int:
    """Calculate subscription price based on user and plan."""
    # Business logic here
    return price

# tests/unit/test_services.py
async def test_subscription_price_for_premium_plan():
    """Premium plan should cost 99 for new users."""
    price = await calculate_subscription_price(user_id="new_user", plan="premium")
    assert price == 99
```

**When to use:** Always test business logic directly before wrapping in HTTP.

### 3.2 Endpoint tests (HTTP contract)

**Purpose:** Prove endpoints behave correctly (status codes, response shapes, validation).

**Pattern:** Use dependency overrides to isolate from database/external services.

**Example:**
```python
# app/main.py
from fastapi import FastAPI, Depends

app = FastAPI()

async def get_db():
    """Database dependency."""
    # Real DB connection
    pass

@app.get("/users/{user_id}")
async def get_user(user_id: str, db = Depends(get_db)):
    """Get user by ID."""
    user = await db.fetch_user(user_id)
    return {"user_id": user.id, "name": user.name}

# tests/test_endpoints.py
import httpx
import pytest
from httpx import ASGITransport

async def fake_db():
    """Fake database for testing."""
    class FakeDB:
        async def fetch_user(self, user_id: str):
            return type('User', (), {'id': user_id, 'name': 'Alice'})()
    return FakeDB()

@pytest.fixture
async def client():
    """Create test client with dependency overrides."""
    app.dependency_overrides[get_db] = fake_db
    async with httpx.AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test"
    ) as ac:
        yield ac
    app.dependency_overrides.clear()

async def test_get_user_returns_user_data(client):
    """GET /users/{id} should return user data with 200 status."""
    response = await client.get("/users/alice")
    assert response.status_code == 200
    assert response.json() == {"user_id": "alice", "name": "Alice"}
```

**When to use:** For every endpoint, to prove HTTP contract correctness.

### 3.3 Integration tests (real dependencies)

**Purpose:** Prove the full stack works together with real DB/services.

**Pattern:** No dependency overrides. Use real (but ephemeral) dependencies.

**Example:**
```python
@pytest.mark.integration
async def test_user_creation_persists_to_database(db_session, client_no_overrides):
    """POST /users should persist user to real database."""
    response = await client_no_overrides.post("/users", json={"name": "Bob"})
    assert response.status_code == 201

    # Verify persistence with real DB query
    user = await db_session.fetch_user(response.json()["user_id"])
    assert user.name == "Bob"
```

**When to use:** Fewer than endpoint tests. Focus on critical paths and boundary behavior.

---

## 4. FastAPI-Specific Patterns

### 4.1 Dependency override (the core pattern)

**Rule:** You MUST use `app.dependency_overrides` for endpoint tests, not mocking.

**Rationale:**
- FastAPI's DI is designed for this
- Cleaner than patching/mocking
- Tests the real request flow
- Type-safe and explicit

**Pattern:**
```python
# Good: Dependency override
app.dependency_overrides[get_db] = fake_db
app.dependency_overrides[get_current_user] = lambda: User(id="test_user")

# Bad: Patching internals
with patch('app.database.get_connection') as mock_db:
    # Brittle, couples test to implementation
```

**You MUST clear overrides after each test:**
```python
@pytest.fixture(autouse=True)
def clear_overrides():
    """Clear dependency overrides after each test."""
    yield
    app.dependency_overrides.clear()
```

### 4.2 Lifespan handling

**Rule:** If your app uses `@app.on_event("startup")` or lifespan context managers, tests MUST handle lifespan explicitly.

**When you DON'T need lifespan:**
- Endpoint tests with dependency overrides
- Tests that don't rely on startup/shutdown side effects

**When you DO need lifespan:**
- Integration tests using real DB connections initialized at startup
- Tests relying on background tasks started at startup
- Tests checking cleanup behavior

**Pattern with `asgi-lifespan`:**
```python
from asgi_lifespan import LifespanManager

@pytest.fixture
async def client_with_lifespan():
    """Client that runs startup/shutdown events."""
    async with LifespanManager(app) as manager:
        async with httpx.AsyncClient(
            transport=ASGITransport(app=manager.app),
            base_url="http://test"
        ) as ac:
            yield ac
```

**Pattern without `asgi-lifespan` (manual):**
```python
@pytest.fixture
async def app_with_lifespan():
    """Manually trigger lifespan events."""
    await app.router.startup()
    yield app
    await app.router.shutdown()
```

### 4.3 Auth testing (two-layer approach)

**Rule:** Test auth at both layers: mocked for endpoint tests, real for integration.

**Layer 1: Endpoint tests (override auth dependency)**
```python
async def mock_current_user():
    """Mock authenticated user."""
    return User(id="alice", role="admin")

app.dependency_overrides[get_current_user] = mock_current_user

async def test_admin_endpoint_allows_admin(client):
    """Admin endpoint should allow admin users."""
    response = await client.get("/admin/dashboard")
    assert response.status_code == 200
```

**Layer 2: Integration tests (real JWT verification)**
```python
@pytest.mark.integration
async def test_jwt_authentication_flow(client_no_overrides):
    """Full auth flow: login -> get token -> access protected resource."""
    # Login
    login_resp = await client_no_overrides.post("/auth/login", json={
        "username": "alice",
        "password": "secure_password"
    })
    token = login_resp.json()["access_token"]

    # Use token
    protected_resp = await client_no_overrides.get(
        "/protected",
        headers={"Authorization": f"Bearer {token}"}
    )
    assert protected_resp.status_code == 200
```

### 4.4 WebSocket testing

**Rule:** WebSocket tests MUST use real WebSocket connections, not HTTP mocks.

**Pattern:**
```python
import pytest
from httpx_ws import aconnect_ws

async def test_websocket_authentication(client):
    """WebSocket connection should authenticate via session token."""
    async with aconnect_ws(
        "ws://test/ws",
        client=client,
        params={"token": "valid_session_token"}
    ) as ws:
        await ws.send_text("ping")
        response = await ws.receive_text()
        assert response == "pong"

async def test_websocket_rejects_invalid_token(client):
    """WebSocket should reject connections with invalid tokens."""
    with pytest.raises(Exception) as exc_info:
        async with aconnect_ws(
            "ws://test/ws",
            client=client,
            params={"token": "invalid_token"}
        ) as ws:
            pass
    assert "403" in str(exc_info.value) or "401" in str(exc_info.value)
```

**You MUST NOT use HTTP client for WebSocket endpoints.**

### 4.5 Background task testing

**Rule:** Background tasks MUST be tested by verifying their side effects, not by waiting arbitrary times.

**Pattern (explicit task execution):**
```python
from fastapi import BackgroundTasks

async def send_email(email: str, message: str):
    """Background task: send email."""
    # Email logic here
    pass

async def test_background_task_side_effects(client, email_outbox):
    """Endpoint should trigger email sending."""
    # Trigger endpoint that schedules background task
    response = await client.post("/register", json={"email": "alice@example.com"})
    assert response.status_code == 201

    # Verify side effect (using test email backend)
    assert len(email_outbox) == 1
    assert email_outbox[0].to == "alice@example.com"
```

**You MUST NOT use `time.sleep()` or `asyncio.sleep()` to "wait for background tasks."**

**Instead:**
- Override background task system with synchronous execution in tests
- Verify side effects (DB writes, queue messages, etc.)
- Use dependency injection to replace background task executor

### 4.6 File upload/download testing

**Rule:** File operations MUST use real file-like objects, not string mocks.

**Upload pattern:**
```python
from io import BytesIO

async def test_file_upload_accepts_valid_image(client):
    """POST /upload should accept valid image files."""
    file_content = b"fake image bytes"
    files = {"file": ("test.png", BytesIO(file_content), "image/png")}

    response = await client.post("/upload", files=files)
    assert response.status_code == 201
    assert "file_id" in response.json()
```

**Download pattern:**
```python
async def test_file_download_returns_correct_content(client):
    """GET /files/{id} should return file with correct content-type."""
    response = await client.get("/files/test-file-id")
    assert response.status_code == 200
    assert response.headers["content-type"] == "image/png"
    assert len(response.content) > 0
```

### 4.7 Streaming response testing

**Rule:** Streaming responses MUST be tested by consuming the stream, not snapshotting.

**Pattern:**
```python
async def test_streaming_response_yields_chunks(client):
    """GET /stream should yield data in chunks."""
    response = await client.get("/stream/large-dataset")
    assert response.status_code == 200

    chunks = []
    async for chunk in response.aiter_bytes():
        chunks.append(chunk)

    assert len(chunks) > 1  # Verify it actually streamed
    full_content = b"".join(chunks)
    assert len(full_content) > 1000  # Verify substantial data
```

---

## 5. Async Testing Rules

### 5.1 Async test markers

**Rule:** All async FastAPI tests MUST use `@pytest.mark.anyio` or `@pytest.mark.asyncio`.

**Pattern:**
```python
import pytest

@pytest.mark.anyio
async def test_async_endpoint(client):
    """Tests async endpoints."""
    response = await client.get("/async-route")
    assert response.status_code == 200
```

**You MUST NOT:**
- Create event loops manually inside tests
- Use `asyncio.run()` inside pytest tests
- Mix sync and async test styles randomly

### 5.2 Async fixture scope

**Rule:** Async fixtures SHOULD have appropriate scope to avoid recreating expensive resources.

**Pattern:**
```python
@pytest.fixture(scope="function")
async def client():
    """Per-test client (default)."""
    # Recreated for each test
    pass

@pytest.fixture(scope="module")
async def db_engine():
    """Per-module DB engine (expensive to create)."""
    # Created once per test module
    pass
```

---

## 6. Common Pitfalls

### 6.1 Don't test with `TestClient` if you're async-first

**Problem:** `TestClient` is synchronous. Modern FastAPI is async.

**Anti-pattern:**
```python
from fastapi.testclient import TestClient

client = TestClient(app)  # Synchronous, runs in thread pool
response = client.get("/endpoint")  # Not actually async
```

**Correct pattern:**
```python
import httpx
from httpx import ASGITransport

async with httpx.AsyncClient(transport=ASGITransport(app=app)) as client:
    response = await client.get("/endpoint")  # Actually async
```

**Exception:** `TestClient` is acceptable for quick sync-only apps or legacy code.

### 6.2 Don't forget to clear dependency overrides

**Problem:** Overrides leak between tests, causing flakiness.

**Anti-pattern:**
```python
async def test_one():
    app.dependency_overrides[get_db] = fake_db
    # Test runs
    # Overrides NOT cleared

async def test_two():
    # Still using fake_db from test_one!
```

**Correct pattern:**
```python
@pytest.fixture(autouse=True)
def clear_dependency_overrides():
    """Ensure clean state between tests."""
    yield
    app.dependency_overrides.clear()
```

### 6.3 Don't bypass the framework you're testing

**Problem:** Testing FastAPI by calling internal functions defeats the purpose.

**Anti-pattern:**
```python
# Don't do this
async def test_endpoint_logic():
    result = await some_internal_function(param)
    assert result == expected
```

**Correct pattern:**
```python
# Test through the HTTP layer
async def test_endpoint_behavior(client):
    response = await client.get("/endpoint?param=value")
    assert response.status_code == 200
    assert response.json() == expected
```

Per the [core doctrine rule on bypassing abstractions](./testing.md#35-do-not-bypass-the-thing-you-are-testing), you MUST test FastAPI endpoints through the ASGI/HTTP layer.

---

## 7. Recommended Baseline Fixtures

### 7.1 Standard fixture set

Every FastAPI project SHOULD have these fixtures in `tests/conftest.py`:

```python
import pytest
import httpx
from httpx import ASGITransport
from app.main import app

@pytest.fixture(autouse=True)
def clear_dependency_overrides():
    """Clear dependency overrides between tests."""
    yield
    app.dependency_overrides.clear()

@pytest.fixture
async def client():
    """HTTP client for endpoint tests (no lifespan)."""
    async with httpx.AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test"
    ) as ac:
        yield ac

@pytest.fixture
async def client_with_lifespan():
    """HTTP client that runs startup/shutdown events."""
    from asgi_lifespan import LifespanManager
    async with LifespanManager(app) as manager:
        async with httpx.AsyncClient(
            transport=ASGITransport(app=manager.app),
            base_url="http://test"
        ) as ac:
            yield ac

@pytest.fixture
def override_deps():
    """Helper for setting dependency overrides."""
    def _override(dependency, override):
        app.dependency_overrides[dependency] = override
    return _override
```

---

## 8. Integration with Core Doctrine

### 8.1 Determinism

Per [core doctrine section 3.1](./testing.md#31-determinism-flakiness-is-a-bug):

FastAPI tests MUST:
- Freeze time with `freezegun` or `time-machine` for time-dependent logic
- Use dependency overrides to replace non-deterministic services
- Avoid `asyncio.sleep()` in test assertions

### 8.2 Meaningful assertions

Per [core doctrine section 3.2](./testing.md#32-assertions-must-be-meaningful):

FastAPI tests MUST assert:
- Status codes (explicit, not `2xx` ranges)
- Response structure (validate schema)
- Side effects (DB changes, queue messages, logs)

**Forbidden:**
```python
assert response.status_code in [200, 201]  # Too vague
assert response.json()  # Meaningless
```

**Required:**
```python
assert response.status_code == 201
assert response.json()["user_id"] is not None
assert response.json()["created_at"] is not None
```

### 8.3 Test the public API

Per [core doctrine section 3.4](./testing.md#34-test-the-public-interface-not-private-internals):

FastAPI tests MUST:
- Test documented endpoints only
- Not call private route handlers directly
- Not assert internal FastAPI state (app.state, etc.)

---

## 9. Directory Layout (Recommended)

```
tests/
├── conftest.py              # Shared fixtures
├── unit/
│   ├── test_services.py     # Business logic (no HTTP)
│   └── test_models.py       # Data models
├── endpoints/
│   ├── test_auth.py         # Auth endpoints (dependency overrides)
│   ├── test_users.py        # User endpoints
│   └── test_websocket.py    # WebSocket endpoints
└── integration/
    ├── test_full_auth_flow.py   # Real DB, real JWT
    └── test_user_lifecycle.py   # Create→Update→Delete
```

**Rationale:**
- `unit/` = No HTTP, no DB
- `endpoints/` = HTTP layer with dependency overrides
- `integration/` = Full stack with real dependencies

---

## 10. Examples

See [`examples/testing/fastapi/`](../examples/testing/fastapi/) for complete working examples:
- Dependency override patterns
- Auth testing (two layers)
- WebSocket testing
- Background task testing
- File upload/download
- Streaming responses

---

## References

- [Core Testing Doctrine](./testing.md) — Universal testing principles
- [FastAPI Testing Documentation](https://fastapi.tiangolo.com/tutorial/testing/)
- [httpx Documentation](https://www.python-httpx.org/)
- [pytest-anyio](https://github.com/agronholm/anyio/tree/master/tests)

---

**Version History:**
- **1.0.0** (2025-12-14) — Initial release, extracted from ChatGPT patterns and The Standard
