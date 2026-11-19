"""Enums for job and attempt states."""

from enum import Enum


class JobStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


# One owner for the status sets. Tuples, so the SQL a filter makes has one order.
ACTIVE_STATUSES: tuple[str, ...] = (JobStatus.PENDING.value, JobStatus.RUNNING.value)
TERMINAL_STATUSES: tuple[str, ...] = (
    JobStatus.COMPLETED.value,
    JobStatus.FAILED.value,
    JobStatus.CANCELLED.value,
)


class AttemptStatus(str, Enum):
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    LEASE_EXPIRED = "lease_expired"


class FailureKind(str, Enum):
    EXCEPTION = "exception"
    LEASE_EXPIRED = "lease_expired"
    TASK_NOT_FOUND = "task_not_found"
    SIGNATURE_MISMATCH = "signature_mismatch"
    PAYLOAD_INVALID = "payload_invalid"
    CANCELLED = "cancelled"
    TIMEOUT = "timeout"


RETRYABLE_FAILURES: frozenset[FailureKind] = frozenset({FailureKind.EXCEPTION, FailureKind.TIMEOUT})
