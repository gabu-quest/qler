# FastAPI Testing Examples

This directory contains complete, working examples of testing patterns from [`docs/testing-fastapi.md`](../../../docs/testing-fastapi.md).

## Examples Included

1. **`test_dependency_override.py`** — Dependency override pattern for endpoint tests
2. **`test_auth_layers.py`** — Two-layer auth testing (mocked + real JWT)
3. **`test_websocket.py`** — WebSocket connection and authentication testing
4. **`test_background_tasks.py`** — Background task side effect verification
5. **`test_file_upload.py`** — File upload/download testing

## Running These Examples

These are standalone examples for educational purposes. To run them in a real project:

1. Install dependencies:
   ```bash
   pip install fastapi httpx pytest pytest-asyncio
   ```

2. Run tests:
   ```bash
   pytest examples/testing/fastapi/
   ```

## Structure

Each example follows The Standard's testing principles:
- ✅ Deterministic (no flaky tests)
- ✅ Meaningful assertions (tests behavior, not trivia)
- ✅ Realistic names (no foo/bar)
- ✅ Tests the public API (HTTP layer)
- ✅ Clear comments explaining what and why

## Reference

See [`docs/testing-fastapi.md`](../../../docs/testing-fastapi.md) for complete FastAPI testing doctrine.
