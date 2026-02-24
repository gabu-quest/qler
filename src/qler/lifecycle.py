"""Structured lifecycle events for job state transitions.

Emits log records on the ``qler.lifecycle`` logger with extra fields
(event, job_id, queue, task, correlation_id, …) that logler's
JsonHandler serializes into queryable JSON.

Always-on: fires via Python's ``logging`` module regardless of
Prometheus configuration.  ~200 ns overhead when no handler is attached.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger("qler.lifecycle")


def emit(
    event: str,
    *,
    job_id: str,
    queue: str,
    task: str,
    correlation_id: str = "",
    **fields: Any,
) -> None:
    """Emit a structured lifecycle event."""
    extra: dict[str, Any] = {
        "event": event,
        "job_id": job_id,
        "queue": queue,
        "task": task,
        **fields,
    }
    if correlation_id:
        extra["correlation_id"] = correlation_id
    logger.info(
        "%s: job=%s queue=%s task=%s",
        event,
        job_id,
        queue,
        task,
        extra=extra,
    )
