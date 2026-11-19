"""qler — Async-first background job queue for Python, built on SQLite via sqler."""

from qler._context import current_job, current_queue, is_cancellation_requested, set_progress
from qler._time import generate_ulid
from qler.cron import CronWrapper, cron
from qler.enums import AttemptStatus, FailureKind, JobStatus
from qler.exceptions import (
    ClaimConflictError,
    ConfigurationError,
    DependencyError,
    JobCancelledError,
    JobFailedError,
    JobNotFoundError,
    PayloadNotSerializableError,
    PayloadTooLargeError,
    QlerError,
    SignatureMismatchError,
    TaskNotFoundError,
    TaskNotRegisteredError,
    UniqueKeyConflictError,
)
from qler.metrics import QlerMetrics
from qler.models import Job, JobAttempt
from qler.queue import Queue
from qler.task import task
from qler.worker import Worker

__version__ = "0.5.1"

__all__ = [
    "AttemptStatus",
    "ClaimConflictError",
    "ConfigurationError",
    "CronWrapper",
    "DependencyError",
    "FailureKind",
    "Job",
    "JobAttempt",
    "JobCancelledError",
    "JobFailedError",
    "JobNotFoundError",
    "JobStatus",
    "PayloadNotSerializableError",
    "PayloadTooLargeError",
    "QlerError",
    "QlerMetrics",
    "Queue",
    "SignatureMismatchError",
    "TaskNotFoundError",
    "TaskNotRegisteredError",
    "UniqueKeyConflictError",
    "Worker",
    "__version__",
    "cron",
    "current_job",
    "current_queue",
    "generate_ulid",
    "is_cancellation_requested",
    "set_progress",
    "task",
]
