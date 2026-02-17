"""Smoke tests for qler scaffold — verify package structure is sound."""

import pytest
from click.testing import CliRunner


def test_import_qler():
    import qler

    assert qler.__version__ == "0.1.0"


def test_enums_importable():
    from qler import AttemptStatus, FailureKind, JobStatus

    # JobStatus — exhaustive set equality (not len()) so diffs are actionable
    assert set(JobStatus) == {
        JobStatus.PENDING,
        JobStatus.RUNNING,
        JobStatus.COMPLETED,
        JobStatus.FAILED,
        JobStatus.CANCELLED,
    }
    assert JobStatus.PENDING == "pending"
    assert JobStatus.RUNNING == "running"
    assert JobStatus.COMPLETED == "completed"
    assert JobStatus.FAILED == "failed"
    assert JobStatus.CANCELLED == "cancelled"

    # AttemptStatus
    assert set(AttemptStatus) == {
        AttemptStatus.RUNNING,
        AttemptStatus.COMPLETED,
        AttemptStatus.FAILED,
        AttemptStatus.LEASE_EXPIRED,
    }
    assert AttemptStatus.RUNNING == "running"
    assert AttemptStatus.COMPLETED == "completed"
    assert AttemptStatus.FAILED == "failed"
    assert AttemptStatus.LEASE_EXPIRED == "lease_expired"

    # FailureKind
    assert set(FailureKind) == {
        FailureKind.EXCEPTION,
        FailureKind.LEASE_EXPIRED,
        FailureKind.TASK_NOT_FOUND,
        FailureKind.SIGNATURE_MISMATCH,
        FailureKind.PAYLOAD_INVALID,
        FailureKind.CANCELLED,
    }

    # RETRYABLE_FAILURES — must be frozenset, only EXCEPTION is retryable
    from qler.enums import RETRYABLE_FAILURES

    assert isinstance(RETRYABLE_FAILURES, frozenset)
    assert RETRYABLE_FAILURES == frozenset({FailureKind.EXCEPTION})


def test_exceptions_hierarchy():
    from qler import (
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

    # All exceptions inherit from QlerError
    for exc_cls in (
        JobNotFoundError,
        JobFailedError,
        JobCancelledError,
        TaskNotFoundError,
        TaskNotRegisteredError,
        SignatureMismatchError,
        PayloadTooLargeError,
        PayloadNotSerializableError,
        ClaimConflictError,
        ConfigurationError,
    ):
        assert issubclass(exc_cls, QlerError), f"{exc_cls.__name__} must subclass QlerError"

    # QlerError carries message and details
    err = QlerError("test error", details={"key": "val"})
    assert err.message == "test error"
    assert err.details == {"key": "val"}
    assert str(err) == "test error"

    # Default details is empty dict
    err = QlerError("bare")
    assert err.details == {}

    # Raise/catch verifies propagation
    with pytest.raises(QlerError, match="propagation test"):
        raise QlerError("propagation test")

    # Catch subclass as QlerError
    with pytest.raises(QlerError, match="sub error"):
        raise JobNotFoundError("sub error", ulid="01ABC")

    # Contextual attributes on subclasses
    err = JobNotFoundError("not found", ulid="01ABC")
    assert err.ulid == "01ABC"

    err = JobFailedError("failed", ulid="01DEF", failure_kind="exception")
    assert err.ulid == "01DEF"
    assert err.failure_kind == "exception"

    err = TaskNotFoundError("no module", task_path="app.tasks.missing")
    assert err.task_path == "app.tasks.missing"

    err = SignatureMismatchError("wrong args", task_path="app.tasks.func")
    assert err.task_path == "app.tasks.func"

    err = PayloadTooLargeError("too big", size=2_000_000, max_size=1_000_000)
    assert err.size == 2_000_000
    assert err.max_size == 1_000_000

    err = ClaimConflictError("race", ulid="01GHI")
    assert err.ulid == "01GHI"


def test_cli_entrypoint():
    from qler.cli import cli

    runner = CliRunner()
    result = runner.invoke(cli, ["--version"])
    assert result.exit_code == 0
    assert result.output.strip() == "qler, version 0.1.0"
