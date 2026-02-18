"""Unified exception hierarchy for qler."""

from typing import Any, Optional


class QlerError(Exception):
    """Base exception for all qler errors."""

    def __init__(self, message: str, *, details: Optional[dict[str, Any]] = None):
        super().__init__(message)
        self.message = message
        self.details = details or {}


# Job lifecycle errors


class JobNotFoundError(QlerError):
    """Raised when a job with the given ULID does not exist."""

    def __init__(
        self,
        message: str,
        *,
        ulid: Optional[str] = None,
        details: Optional[dict[str, Any]] = None,
    ):
        super().__init__(message, details=details)
        self.ulid = ulid


class JobFailedError(QlerError):
    """Raised when waiting on a job that has permanently failed."""

    def __init__(
        self,
        message: str,
        *,
        ulid: Optional[str] = None,
        failure_kind: Optional[str] = None,
        details: Optional[dict[str, Any]] = None,
    ):
        super().__init__(message, details=details)
        self.ulid = ulid
        self.failure_kind = failure_kind


class JobCancelledError(QlerError):
    """Raised when waiting on a job that was cancelled."""

    def __init__(
        self,
        message: str,
        *,
        ulid: Optional[str] = None,
        details: Optional[dict[str, Any]] = None,
    ):
        super().__init__(message, details=details)
        self.ulid = ulid


# Task resolution errors


class TaskNotFoundError(QlerError):
    """Raised when the task module/function cannot be imported."""

    def __init__(
        self,
        message: str,
        *,
        task_path: Optional[str] = None,
        details: Optional[dict[str, Any]] = None,
    ):
        super().__init__(message, details=details)
        self.task_path = task_path


class TaskNotRegisteredError(QlerError):
    """Raised when a task path exists but is not decorated with @task."""

    def __init__(
        self,
        message: str,
        *,
        task_path: Optional[str] = None,
        details: Optional[dict[str, Any]] = None,
    ):
        super().__init__(message, details=details)
        self.task_path = task_path


class SignatureMismatchError(QlerError):
    """Raised when job payload args don't match the task's signature."""

    def __init__(
        self,
        message: str,
        *,
        task_path: Optional[str] = None,
        details: Optional[dict[str, Any]] = None,
    ):
        super().__init__(message, details=details)
        self.task_path = task_path


# Payload errors


class PayloadTooLargeError(QlerError):
    """Raised when the serialized payload exceeds max_payload_size."""

    def __init__(
        self,
        message: str,
        *,
        size: Optional[int] = None,
        max_size: Optional[int] = None,
        details: Optional[dict[str, Any]] = None,
    ):
        super().__init__(message, details=details)
        self.size = size
        self.max_size = max_size


class PayloadNotSerializableError(QlerError):
    """Raised when the payload cannot be serialized to JSON."""

    def __init__(
        self,
        message: str,
        *,
        details: Optional[dict[str, Any]] = None,
    ):
        super().__init__(message, details=details)


# Concurrency errors


class ClaimConflictError(QlerError):
    """Raised when a job claim fails due to concurrent modification."""

    def __init__(
        self,
        message: str,
        *,
        ulid: Optional[str] = None,
        details: Optional[dict[str, Any]] = None,
    ):
        super().__init__(message, details=details)
        self.ulid = ulid


# Dependency errors


class DependencyError(QlerError):
    """Raised when a job dependency is invalid (missing, failed, or cancelled)."""

    def __init__(
        self,
        message: str,
        *,
        dependency_ulid: Optional[str] = None,
        details: Optional[dict[str, Any]] = None,
    ):
        super().__init__(message, details=details)
        self.dependency_ulid = dependency_ulid


# Configuration errors


class ConfigurationError(QlerError):
    """Raised when queue or task configuration is invalid."""

    pass
