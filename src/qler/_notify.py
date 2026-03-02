"""In-process notification registry for instant job.wait() wakeup.

Leaf module — no qler imports, no circular dependency risk.

When a Worker completes/fails/cancels a job in the same process, it fires
the Event immediately. job.wait() wakes up without polling. Cross-process
callers (or jobs completed before wait() starts) fall back to the existing
DB poll loop transparently.
"""

from __future__ import annotations

import asyncio

_registry: dict[str, asyncio.Event] = {}


def register(ulid: str) -> asyncio.Event:
    """Get-or-create an Event for a job ULID.

    Called by wait() BEFORE the first DB check so fire() can never
    race ahead of the registration.
    """
    if ulid not in _registry:
        _registry[ulid] = asyncio.Event()
    return _registry[ulid]


def fire(ulid: str) -> None:
    """Signal that a job reached a terminal state.

    No-op if nobody is waiting (cross-process completion, or wait()
    never called). Setting an already-set Event is also a no-op,
    so multiple fire() calls for the same ULID are safe.
    """
    ev = _registry.get(ulid)
    if ev is not None:
        ev.set()


def clear(ulid: str) -> None:
    """Remove a ULID from the registry (leak prevention).

    Called in wait()'s finally block. No-op if already cleared.
    """
    _registry.pop(ulid, None)
