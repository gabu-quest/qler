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
