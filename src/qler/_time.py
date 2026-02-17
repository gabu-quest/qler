"""Time utilities for qler."""

import time

from ulid import ULID


def now_epoch() -> int:
    """Return current UTC time as integer epoch seconds."""
    return int(time.time())


def generate_ulid() -> str:
    """Generate a new ULID as a string."""
    return str(ULID())
