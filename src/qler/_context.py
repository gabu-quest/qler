"""Context variable for tracking the currently-executing job."""

from __future__ import annotations

from contextvars import ContextVar
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from qler.models.job import Job
    from qler.queue import Queue

_current_job: ContextVar[Optional["Job"]] = ContextVar("qler_current_job", default=None)
_current_queue: ContextVar[Optional["Queue"]] = ContextVar("qler_current_queue", default=None)


def current_job() -> "Job":
    """Return the currently-executing Job.

    Raises:
        RuntimeError: If called outside a task execution context.
    """
    job = _current_job.get()
    if job is None:
        raise RuntimeError(
            "current_job() called outside of a task execution context. "
            "This function can only be used inside a @task-decorated function."
        )
    return job


def current_queue() -> "Queue":
    """Return the Queue that owns the currently-executing job.

    Raises:
        RuntimeError: If called outside a task execution context.
    """
    queue = _current_queue.get()
    if queue is None:
        raise RuntimeError(
            "current_queue() called outside of a task execution context. "
            "This function can only be used inside a @task-decorated function."
        )
    return queue


async def set_progress(percent: int, message: str = "") -> None:
    """Update the current job's progress (0–100) with optional message.

    Writes directly to the database so callers can poll for updates.

    Raises:
        RuntimeError: If called outside a task execution context.
        ValueError: If percent is not in 0–100 range.
    """
    from sqler import F

    from qler._time import now_epoch
    from qler.models.job import Job

    if not isinstance(percent, int) or percent < 0 or percent > 100:
        raise ValueError(f"progress must be an integer 0–100, got {percent!r}")

    job = _current_job.get()
    if job is None:
        raise RuntimeError(
            "set_progress() called outside of a task execution context. "
            "This function can only be used inside a @task-decorated function."
        )

    await Job.query().filter(
        (F("ulid") == job.ulid) & (F("worker_id") == job.worker_id)
    ).update_one(
        progress=percent,
        progress_message=message,
        updated_at=now_epoch(),
    )
    # Update local instance too
    job.progress = percent
    job.progress_message = message


async def is_cancellation_requested() -> bool:
    """Check if the current job has cancellation requested. Queries the DB.

    Returns False (not raises) when called outside a task context.
    """
    from sqler import F

    from qler.models.job import Job

    job = _current_job.get()
    if job is None:
        return False
    refreshed = await Job.query().filter(F("ulid") == job.ulid).first()
    if refreshed is None:
        return False
    # Only trust the flag if this worker still owns the job
    if refreshed.worker_id and refreshed.worker_id != job.worker_id:
        return False
    return refreshed.cancel_requested
