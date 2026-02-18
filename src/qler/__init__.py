"""qler — Async-first background job queue for Python, built on SQLite via sqler."""

from qler._context import current_job, is_cancellation_requested
from qler.cron import CronWrapper, cron
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
from qler.worker import Worker

__version__ = "0.2.0"

__all__ = [
    "__version__",
    # Core
    "Queue",
    "Worker",
    "task",
    "cron",
    "CronWrapper",
    "current_job",
    "is_cancellation_requested",
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
