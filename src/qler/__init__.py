"""qler — Async-first background job queue for Python, built on SQLite via sqler."""

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

__version__ = "0.1.0"

__all__ = [
    "__version__",
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
