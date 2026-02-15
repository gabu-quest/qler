---
description: "Write tests in persistent files, not one-off bash commands"
paths:
  - "**/*test*"
  - "**/*spec*"
  - "**/tests/**"
  - "**/test/**"
  - "**/__tests__/**"
---

# No Ephemeral Tests

Write tests in files, not one-off bash commands.

## The Problem

**Bad:** `curl localhost:8000/api/health` in bash
- Requires user approval each time
- Vanishes after the session
- Can't be audited or extended
- No regression protection

## The Solution

**Good:** Test file that can be rerun, audited, and extended.

For **unit tests**, use the project's native test framework:
- Python projects: pytest
- TypeScript/Vue projects: Vitest
- Node.js projects: Jest or Vitest

For **integration/scripted tests** (API testing, multi-service coordination), Python is recommended even in non-Python projects because:
- `requests` library is cleaner than curl one-liners
- Works cross-platform (Windows/Linux/macOS)
- Easy to parameterize and loop
- Persisted test files > ephemeral commands

```python
# scripts/test_api.py - works in ANY project
import requests

def test_health_endpoint():
    response = requests.get("http://localhost:8000/api/health")
    assert response.status_code == 200

def test_auth_flow():
    # Login
    r = requests.post("http://localhost:8000/auth/login", json={
        "username": "test",
        "password": "test123"
    })
    assert r.status_code == 200
    token = r.json()["token"]

    # Use token
    r = requests.get("http://localhost:8000/api/me", headers={
        "Authorization": f"Bearer {token}"
    })
    assert r.status_code == 200
```

## The Principle

Keep tests in files. Don't rely on session memory for verification.
