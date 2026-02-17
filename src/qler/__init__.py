"""qler — Async-first background job queue for Python, built on SQLite via sqler."""

from qler._context import current_job
from qler.enums import AttemptStatus, FailureKind, JobStatus
from qler.exceptions import (
    ClaimConflictError,
    ConfigurationError,
    JobCancelledError,
    JobFailedError,
    JobNotFoundError,
    PayloadNotSerializableError,
    PayloadTooLargeError,
    QlerError,
    SignatureMismatchError,
    TaskNotFoundError,
    TaskNotRegisteredError,
)
from qler.models import Job, JobAttempt
from qler.queue import Queue
from qler.task import task

__version__ = "0.1.0"

__all__ = [
    "__version__",
    # Core
    "Queue",
    "task",
    "current_job",
    "Job",
    "JobAttempt",
    # Enums
    "AttemptStatus",
    "FailureKind",
    "JobStatus",
    # Exceptions
    "QlerError",
    "JobNotFoundError",
    "JobFailedError",
    "JobCancelledError",
    "TaskNotFoundError",
    "TaskNotRegisteredError",
    "SignatureMismatchError",
    "PayloadTooLargeError",
    "PayloadNotSerializableError",
    "ClaimConflictError",
    "ConfigurationError",
]
