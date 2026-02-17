"""Smoke tests for qler scaffold — verify package structure is sound."""

from click.testing import CliRunner


def test_import_qler():
    import qler

    assert qler.__version__ == "0.1.0"


def test_enums_importable():
    from qler import AttemptStatus, FailureKind, JobStatus

    # JobStatus values match SPEC
    assert JobStatus.PENDING == "pending"
    assert JobStatus.RUNNING == "running"
    assert JobStatus.COMPLETED == "completed"
    assert JobStatus.FAILED == "failed"
    assert JobStatus.CANCELLED == "cancelled"
    assert len(JobStatus) == 5

    # AttemptStatus values match SPEC
    assert AttemptStatus.RUNNING == "running"
    assert AttemptStatus.COMPLETED == "completed"
    assert AttemptStatus.FAILED == "failed"
    assert AttemptStatus.LEASE_EXPIRED == "lease_expired"
    assert len(AttemptStatus) == 4

    # FailureKind values match SPEC
    assert FailureKind.EXCEPTION == "exception"
    assert FailureKind.LEASE_EXPIRED == "lease_expired"
    assert FailureKind.TASK_NOT_FOUND == "task_not_found"
    assert FailureKind.SIGNATURE_MISMATCH == "signature_mismatch"
    assert FailureKind.PAYLOAD_INVALID == "payload_invalid"
    assert FailureKind.CANCELLED == "cancelled"
    assert len(FailureKind) == 6

    # RETRYABLE_FAILURES
    from qler.enums import RETRYABLE_FAILURES

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
        assert issubclass(exc_cls, Exception)

    # QlerError carries message and details
    err = QlerError("test error", details={"key": "val"})
    assert err.message == "test error"
    assert err.details == {"key": "val"}
    assert str(err) == "test error"

    # Contextual attributes
    err = JobNotFoundError("not found", ulid="01ABC")
    assert err.ulid == "01ABC"

    err = PayloadTooLargeError("too big", size=2_000_000, max_size=1_000_000)
    assert err.size == 2_000_000
    assert err.max_size == 1_000_000


def test_cli_entrypoint():
    from qler.cli import cli

    runner = CliRunner()
    result = runner.invoke(cli, ["--version"])
    assert result.exit_code == 0
    assert "0.1.0" in result.output
