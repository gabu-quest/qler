"""Shared helpers and constants for the qler commands."""

from __future__ import annotations

import asyncio
import importlib
import json
import re
from datetime import datetime, timezone
from typing import Any

import click

from qler._time import now_epoch
from qler.enums import JobStatus
from qler.models.attempt import JobAttempt
from qler.models.job import Job
from qler.queue import Queue

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_SINCE_PATTERN = re.compile(r"(\d+)([smhd])")
_SINCE_UNITS = {"s": 1, "m": 60, "h": 3600, "d": 86400}
_MAX_SINCE_SECONDS = 10 * 365 * 86400  # 10 years
_MODULE_PATTERN = re.compile(r"^[a-zA-Z_][a-zA-Z0-9_.]*$")

TERMINAL_STATUSES = frozenset({
    JobStatus.COMPLETED.value,
    JobStatus.FAILED.value,
    JobStatus.CANCELLED.value,
})


def _run(coro: Any) -> Any:
    """Bridge async → sync for Click commands."""
    return asyncio.run(coro)


async def _get_queue(db_path: str) -> Queue:
    """Create and initialize a Queue from a file path."""
    q = Queue(db_path)
    await q.init_db()
    return q


def _safe_json(raw: str | None) -> Any:
    """Parse JSON safely. Returns {"_invalid": true, ...} on failure."""
    if raw is None:
        return None
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, TypeError) as exc:
        return {"_invalid": True, "raw": raw[:200], "parse_error": str(exc)}


def _format_ts(epoch: int | None) -> str:
    """Epoch seconds → ISO 8601 UTC string."""
    if epoch is None:
        return "-"
    return datetime.fromtimestamp(epoch, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _format_duration(seconds: int) -> str:
    """Seconds → human readable (e.g., '5m 30s')."""
    if seconds < 0:
        return "-"
    parts = []
    if seconds >= 86400:
        days = seconds // 86400
        seconds %= 86400
        parts.append(f"{days}d")
    if seconds >= 3600:
        hours = seconds // 3600
        seconds %= 3600
        parts.append(f"{hours}h")
    if seconds >= 60:
        minutes = seconds // 60
        seconds %= 60
        parts.append(f"{minutes}m")
    if seconds > 0 or not parts:
        parts.append(f"{seconds}s")
    return " ".join(parts)


def _parse_since(since: str) -> int:
    """Parse '1h', '30m', '7d' → epoch seconds threshold."""
    total = 0
    for amount, unit in _SINCE_PATTERN.findall(since):
        total += int(amount) * _SINCE_UNITS[unit]
    if total == 0:
        raise click.BadParameter(f"Invalid duration: {since}")
    if total > _MAX_SINCE_SECONDS:
        raise click.BadParameter(f"Duration exceeds maximum of 10 years: {since}")
    return now_epoch() - total


def _parse_since_to_seconds(since: str) -> int:
    """Parse '1h', '30m', '7d' → total seconds."""
    total = 0
    for amount, unit in _SINCE_PATTERN.findall(since):
        total += int(amount) * _SINCE_UNITS[unit]
    if total == 0:
        raise click.BadParameter(f"Invalid duration: {since}")
    if total > _MAX_SINCE_SECONDS:
        raise click.BadParameter(f"Duration exceeds maximum of 10 years: {since}")
    return total


def _validate_module_path(mod_path: str) -> None:
    """Validate a module path is a valid dotted Python identifier."""
    if not _MODULE_PATTERN.fullmatch(mod_path):
        raise click.BadParameter(
            f"Invalid module path '{mod_path}'. Only dotted Python identifiers are allowed."
        )


def _import_app(app_string: str) -> Queue:
    """Import 'module:attribute' → Queue instance."""
    module_path, _, attr = app_string.partition(":")
    if not attr:
        raise click.BadParameter(f"Expected 'module:attribute', got '{app_string}'")
    _validate_module_path(module_path)
    try:
        mod = importlib.import_module(module_path)
    except ImportError as exc:
        raise click.BadParameter(f"Cannot import module '{module_path}': {exc}") from exc
    try:
        obj = getattr(mod, attr)
    except AttributeError as exc:
        raise click.BadParameter(
            f"Module '{module_path}' has no attribute '{attr}'"
        ) from exc
    if not isinstance(obj, Queue):
        raise click.BadParameter(f"'{app_string}' is not a Queue instance")
    return obj


def _import_modules(modules: tuple[str, ...]) -> None:
    """Import modules to trigger @task registration."""
    for mod_path in modules:
        _validate_module_path(mod_path)
        try:
            importlib.import_module(mod_path)
        except ImportError as exc:
            raise click.BadParameter(f"Cannot import module '{mod_path}': {exc}") from exc


def _job_to_dict(job: Job) -> dict[str, Any]:
    """Convert a Job to a JSON-friendly dict."""
    return {
        "ulid": job.ulid,
        "status": job.status,
        "queue_name": job.queue_name,
        "task": job.task,
        "priority": job.priority,
        "eta": job.eta,
        "lease_expires_at": job.lease_expires_at,
        "worker_id": job.worker_id,
        "lease_duration": job.lease_duration,
        "payload": _safe_json(job.payload_json),
        "result": _safe_json(job.result_json),
        "last_error": job.last_error,
        "last_failure_kind": job.last_failure_kind,
        "attempts": job.attempts,
        "retry_count": job.retry_count,
        "max_retries": job.max_retries,
        "retry_delay": job.retry_delay,
        "last_attempt_id": job.last_attempt_id,
        "correlation_id": job.correlation_id,
        "idempotency_key": job.idempotency_key,
        "unique_key": job.unique_key,
        "timeout": job.timeout,
        "progress": job.progress,
        "progress_message": job.progress_message,
        "cancel_requested": job.cancel_requested,
        "original_queue": job.original_queue,
        "dependencies": job.dependencies,
        "pending_dep_count": job.pending_dep_count,
        "created_at": job.created_at,
        "updated_at": job.updated_at,
        "finished_at": job.finished_at,
    }


def _attempt_to_dict(attempt: JobAttempt) -> dict[str, Any]:
    """Convert a JobAttempt to a JSON-friendly dict."""
    return {
        "ulid": attempt.ulid,
        "job_ulid": attempt.job_ulid,
        "status": attempt.status,
        "attempt_number": attempt.attempt_number,
        "worker_id": attempt.worker_id,
        "started_at": attempt.started_at,
        "finished_at": attempt.finished_at,
        "failure_kind": attempt.failure_kind,
        "error": attempt.error,
        "traceback": attempt.traceback,
        "lease_expires_at": attempt.lease_expires_at,
    }


def _echo_json(data: Any) -> None:
    """Write compact JSON to stdout."""
    click.echo(json.dumps(data, default=str))


def _echo_table(headers: list[str], rows: list[list[str]]) -> None:
    """Print a simple aligned text table."""
    if not rows:
        return
    widths = [len(h) for h in headers]
    for row in rows:
        for i, cell in enumerate(row):
            widths[i] = max(widths[i], len(cell))
    fmt = "  ".join(f"{{:<{w}}}" for w in widths)
    click.echo(fmt.format(*headers))
    click.echo(fmt.format(*["-" * w for w in widths]))
    for row in rows:
        click.echo(fmt.format(*row))
