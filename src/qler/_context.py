"""Context variable for tracking the currently-executing job."""

from __future__ import annotations

from contextvars import ContextVar
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from qler.models.job import Job

_current_job: ContextVar[Optional["Job"]] = ContextVar("qler_current_job", default=None)


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


async def is_cancellation_requested() -> bool:
    """Check if the current job has cancellation requested. Queries the DB.

    Returns False (not raises) when called outside a task context.
    """
    from qler.models.job import Job
    from sqler import F

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
