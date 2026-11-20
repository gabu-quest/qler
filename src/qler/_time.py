"""Time utilities for qler."""

import os
import threading
import time

from ulid import ULID

_RANDOM_BYTES = 10
_RANDOM_END = 1 << (8 * _RANDOM_BYTES)

_ids_lock = threading.Lock()
_last_ms = 0
_last_random = 0


def now_epoch() -> int:
    """Return current UTC time as integer epoch seconds."""
    return int(time.time())


def generate_ulid() -> str:
    """Generate a new ULID as a string.

    Within one process each id sorts after the one made before it, so jobs of equal
    priority and eta are claimed in the order they were enqueued. An id made in the
    millisecond of the one before it, or after the clock stepped back, is that id plus
    one. `ULID()` does not keep this order: it reads the clock once for the timestamp
    and once more to decide whether to count on, so an id made as the millisecond turns
    gets the old timestamp with new random bytes.
    """
    global _last_ms, _last_random
    with _ids_lock:
        ms = time.time_ns() // 1_000_000
        if ms > _last_ms:
            _last_ms = ms
            _last_random = int.from_bytes(os.urandom(_RANDOM_BYTES), "big")
        else:
            _last_random += 1
            if _last_random == _RANDOM_END:
                _last_ms += 1
                _last_random = int.from_bytes(os.urandom(_RANDOM_BYTES), "big")
        return str(
            ULID.from_bytes(
                _last_ms.to_bytes(6, "big") + _last_random.to_bytes(_RANDOM_BYTES, "big")
            )
        )


def _start_a_new_id_run() -> None:
    """In a forked child: forget the parent's last id, so the child cannot make the parent's next one."""
    global _ids_lock, _last_ms, _last_random
    _ids_lock = threading.Lock()
    _last_ms = 0
    _last_random = 0


if hasattr(os, "register_at_fork"):
    os.register_at_fork(after_in_child=_start_a_new_id_run)
