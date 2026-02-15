# Quick Reference: FastAPI Testing

**One-page summary of [`testing-fastapi.md`](../testing-fastapi.md) — Print this for quick reference**

---

## Prime Directive

**Dependency override > mocking. Test through ASGI, not HTTP.**

---

## Testing Layers

| Layer | Purpose | Pattern | When |
|-------|---------|---------|------|
| **Unit** | Business logic | Test functions directly | Always first |
| **Endpoint** | HTTP contract | Dependency overrides | Every endpoint |
| **Integration** | Full stack | Real DB, no overrides | Critical paths |

---

## Core Pattern: Dependency Override

```python
import pytest
import httpx
from httpx import ASGITransport

@pytest.fixture(autouse=True)
def clear_dependency_overrides():
    """Clear overrides after each test."""
    yield
    app.dependency_overrides.clear()

@pytest.fixture
async def client():
    """Test client with overrides."""
    app.dependency_overrides[get_db] = fake_db
    async with httpx.AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test"
    ) as ac:
        yield ac
```

---

## Must-Have Fixtures

```python
# Clear overrides (autouse)
@pytest.fixture(autouse=True)
def clear_dependency_overrides():
    yield
    app.dependency_overrides.clear()

# Basic client
@pytest.fixture
async def client():
    """HTTP client for endpoint tests."""
    async with httpx.AsyncClient(...) as ac:
        yield ac

# Client with lifespan (if needed)
@pytest.fixture
async def client_with_lifespan():
    """Client that runs startup/shutdown events."""
    from asgi_lifespan import LifespanManager
    async with LifespanManager(app) as manager:
        ...
```

---

## Auth Testing (Two Layers)

### Layer 1: Mocked (Fast)
```python
async def mock_user():
    return User(id="alice", role="admin")

app.dependency_overrides[get_current_user] = mock_user
```

### Layer 2: Real (Integration)
```python
@pytest.mark.integration
async def test_jwt_flow(client_no_overrides):
    response = await client_no_overrides.get(
        "/protected",
        headers={"Authorization": f"Bearer {token}"}
    )
```

---

## WebSocket Testing

```python
from fastapi.testclient import TestClient

def test_websocket(client):
    with client.websocket_connect("/ws?token=valid") as ws:
        ws.send_text("ping")
        response = ws.receive_text()
        assert response == "pong"
```

---

## Background Tasks

**Don't sleep! Verify side effects:**
```python
async def test_background_task(client, email_outbox):
    await client.post("/register", json={"email": "alice@example.com"})

    # Verify side effect
    assert len(email_outbox) == 1
```

---

## File Upload

```python
from io import BytesIO

async def test_file_upload(client):
    files = {"file": ("test.png", BytesIO(b"..."), "image/png")}
    response = await client.post("/upload", files=files)
    assert response.status_code == 201
```

---

## Async Test Markers

**MUST use:**
```python
@pytest.mark.anyio  # or @pytest.mark.asyncio
async def test_endpoint(client):
    ...
```

---

## Common Pitfalls

❌ **Don't** use `TestClient` for async apps
✅ **Do** use `httpx.AsyncClient` with `ASGITransport`

❌ **Don't** forget to clear overrides
✅ **Do** use `autouse=True` fixture

❌ **Don't** bypass the framework
✅ **Do** test through HTTP layer

---

## Directory Layout

```
tests/
├── conftest.py              # Shared fixtures
├── unit/
│   └── test_services.py     # Business logic
├── endpoints/
│   └── test_users.py        # Endpoint tests (overrides)
└── integration/
    └── test_auth_flow.py    # Full stack (no overrides)
```

---

## Quick Checklist

- [ ] Read [testing.md](../testing.md) first (core principles)
- [ ] Use dependency overrides, not mocks
- [ ] Clear overrides after each test
- [ ] Use httpx.AsyncClient for async apps
- [ ] Test auth at both layers (mocked + real)
- [ ] Mark async tests with `@pytest.mark.anyio`
- [ ] Test WebSockets with real connections
- [ ] Verify background task side effects

---

**Full Docs:** [testing-fastapi.md](../testing-fastapi.md) | **Examples:** [examples/testing/fastapi/](../../examples/testing/fastapi/)
