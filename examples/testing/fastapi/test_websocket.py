"""
Example: WebSocket testing with FastAPI.

Demonstrates:
- Testing WebSocket connections
- WebSocket authentication
- Message exchange patterns
"""

import pytest
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, status
from fastapi.testclient import TestClient

# ============================================================================
# Application Code
# ============================================================================

app = FastAPI()


async def verify_websocket_token(token: str) -> bool:
    """Verify WebSocket authentication token."""
    # In real app: decode JWT, verify signature
    return token == "valid_session_token"


@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket, token: str = None):
    """WebSocket endpoint with authentication."""
    # Verify authentication
    if not token or not await verify_websocket_token(token):
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
        return

    await websocket.accept()

    try:
        while True:
            # Echo messages back
            data = await websocket.receive_text()

            if data == "ping":
                await websocket.send_text("pong")
            elif data == "close":
                await websocket.close()
                break
            else:
                await websocket.send_text(f"Echo: {data}")

    except WebSocketDisconnect:
        pass


# ============================================================================
# Tests
# ============================================================================


@pytest.fixture
def client():
    """Test client for WebSocket testing."""
    return TestClient(app)


def test_websocket_accepts_valid_token(client):
    """WebSocket connection should accept valid authentication token."""
    with client.websocket_connect("/ws?token=valid_session_token") as websocket:
        # Send message
        websocket.send_text("ping")

        # Receive response
        response = websocket.receive_text()
        assert response == "pong"


def test_websocket_rejects_invalid_token(client):
    """WebSocket connection should reject invalid token."""
    with pytest.raises(Exception) as exc_info:
        with client.websocket_connect("/ws?token=invalid_token") as websocket:
            pass

    # WebSocket closed with policy violation
    assert "1008" in str(exc_info.value)


def test_websocket_rejects_missing_token(client):
    """WebSocket connection should reject missing token."""
    with pytest.raises(Exception) as exc_info:
        with client.websocket_connect("/ws") as websocket:
            pass

    assert "1008" in str(exc_info.value)


def test_websocket_echo_functionality(client):
    """WebSocket should echo messages back."""
    with client.websocket_connect("/ws?token=valid_session_token") as websocket:
        # Send custom message
        websocket.send_text("hello world")

        # Receive echoed message
        response = websocket.receive_text()
        assert response == "Echo: hello world"


def test_websocket_graceful_close(client):
    """WebSocket should handle graceful close."""
    with client.websocket_connect("/ws?token=valid_session_token") as websocket:
        # Send close command
        websocket.send_text("close")

        # Connection should close gracefully (no exception)
        # In real app, you might verify close code/reason
