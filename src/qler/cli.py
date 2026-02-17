"""qler CLI — human-first commands with --json flag."""

from __future__ import annotations

import asyncio
import importlib
import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
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
        "cancel_requested": job.cancel_requested,
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


# ---------------------------------------------------------------------------
# CLI Group
# ---------------------------------------------------------------------------

@click.group(name="qler")
@click.version_option(package_name="qler")
def cli():
    """qler — Background jobs without Redis."""


# ---------------------------------------------------------------------------
# qler init
# ---------------------------------------------------------------------------

@cli.command()
@click.option("--db", required=True, envvar="QLER_DB", help="Database file path")
@click.option("--json", "output_json", is_flag=True, help="Output JSON")
def init(db: str, output_json: bool) -> None:
    """Create the qler database and set PRAGMAs."""

    async def _init() -> dict[str, Any]:
        q = Queue(db)
        await q.init_db()
        await q.close()
        return {"db": db}

    _run(_init())

    # Update .gitignore
    gitignore_updated = False
    db_path = Path(db)
    gitignore = db_path.parent / ".gitignore"
    db_name = db_path.name.replace("\n", "").replace("\r", "").strip()

    if gitignore.exists():
        content = gitignore.read_text()
        if db_name not in content.splitlines():
            with gitignore.open("a") as f:
                if not content.endswith("\n"):
                    f.write("\n")
                f.write(f"{db_name}\n")
            gitignore_updated = True
    else:
        gitignore.write_text(f"{db_name}\n")
        gitignore_updated = True

    if output_json:
        _echo_json({"db": db, "created": True, "gitignore_updated": gitignore_updated})
    else:
        click.echo(f"Created database: {db}")
        if gitignore_updated:
            click.echo(f"Added '{db_name}' to {gitignore}")


# ---------------------------------------------------------------------------
# qler worker
# ---------------------------------------------------------------------------

@cli.command()
@click.option("--db", envvar="QLER_DB", help="Database file path")
@click.option("--app", "app_string", help="Queue instance as 'module:attribute'")
@click.option("--module", "-m", "modules", multiple=True, help="Import module(s) to register tasks")
@click.option("--queues", "-q", default="default", help="Comma-separated queue names")
@click.option("--concurrency", "-c", default=1, type=int, help="Number of concurrent jobs")
@click.option("--poll-interval", default=1.0, type=float, help="Poll interval in seconds")
@click.option("--shutdown-timeout", default=30.0, type=float, help="Shutdown timeout in seconds")
def worker(
    db: str | None,
    app_string: str | None,
    modules: tuple[str, ...],
    queues: str,
    concurrency: int,
    poll_interval: float,
    shutdown_timeout: float,
) -> None:
    """Start a worker process."""
    from qler.worker import Worker

    if app_string:
        q = _import_app(app_string)
    elif db:
        q = Queue(db)
    else:
        raise click.UsageError("Either --db or --app is required")

    _import_modules(modules)

    queue_list = [s.strip() for s in queues.split(",") if s.strip()]
    w = Worker(
        q,
        queues=queue_list,
        concurrency=concurrency,
        poll_interval=poll_interval,
        shutdown_timeout=shutdown_timeout,
    )
    click.echo(f"Worker {w.worker_id} starting (queues={queue_list}, concurrency={concurrency})")
    try:
        _run(w.run())
    except KeyboardInterrupt:
        pass
    finally:
        click.echo("Worker stopped")


# ---------------------------------------------------------------------------
# qler status
# ---------------------------------------------------------------------------

@cli.command()
@click.option("--db", required=True, envvar="QLER_DB", help="Database file path")
@click.option("--json", "output_json", is_flag=True, help="Output JSON")
def status(db: str, output_json: bool) -> None:
    """Show queue depths and job counts."""

    async def _status() -> dict[str, dict[str, int]]:
        q = await _get_queue(db)
        try:
            sql = """
                SELECT queue_name, status, COUNT(*) as cnt
                FROM qler_jobs
                GROUP BY queue_name, status
            """
            cur = await q.db.adapter.execute(sql)
            rows = await cur.fetchall()
            await cur.close()

            queues: dict[str, dict[str, int]] = {}
            for queue_name, job_status, count in rows:
                if queue_name not in queues:
                    queues[queue_name] = {
                        "pending": 0, "running": 0,
                        "completed": 0, "failed": 0, "cancelled": 0,
                    }
                queues[queue_name][job_status] = count
            return queues
        finally:
            await q.close()

    result = _run(_status())
    total = sum(sum(v.values()) for v in result.values())

    if output_json:
        _echo_json({"queues": result, "total": total})
    else:
        if not result:
            click.echo("No jobs found.")
            return
        headers = ["Queue", "Pending", "Running", "Completed", "Failed", "Cancelled"]
        rows = []
        for name, counts in sorted(result.items()):
            rows.append([
                name,
                str(counts["pending"]),
                str(counts["running"]),
                str(counts["completed"]),
                str(counts["failed"]),
                str(counts["cancelled"]),
            ])
        _echo_table(headers, rows)
        click.echo(f"\nTotal: {total} jobs")


# ---------------------------------------------------------------------------
# qler jobs
# ---------------------------------------------------------------------------

@cli.command()
@click.option("--db", required=True, envvar="QLER_DB", help="Database file path")
@click.option("--status", "filter_status", type=click.Choice(
    ["pending", "running", "completed", "failed", "cancelled"],
    case_sensitive=False,
))
@click.option("--queue", "filter_queue", help="Filter by queue name")
@click.option("--task", "filter_task", help="Filter by task path")
@click.option("--since", "since", help="Time window, e.g. '1h', '30m', '7d'")
@click.option("--limit", default=50, type=int, help="Max jobs to return")
@click.option("--json", "output_json", is_flag=True, help="Output JSON")
def jobs(
    db: str,
    filter_status: str | None,
    filter_queue: str | None,
    filter_task: str | None,
    since: str | None,
    limit: int,
    output_json: bool,
) -> None:
    """List jobs with optional filters."""
    from sqler import F

    async def _jobs() -> list[Job]:
        q = await _get_queue(db)
        try:
            query = Job.query()
            filters = []
            if filter_status:
                filters.append(F("status") == filter_status)
            if filter_queue:
                filters.append(F("queue_name") == filter_queue)
            if filter_task:
                filters.append(F("task") == filter_task)
            if since:
                threshold = _parse_since(since)
                filters.append(F("created_at") >= threshold)

            if filters:
                combined = filters[0]
                for f in filters[1:]:
                    combined = combined & f
                query = query.filter(combined)

            return await query.order_by("-ulid").limit(limit).all()
        finally:
            await q.close()

    result = _run(_jobs())

    if output_json:
        _echo_json([_job_to_dict(j) for j in result])
    else:
        if not result:
            click.echo("No jobs found.")
            return
        headers = ["ULID", "Status", "Queue", "Task", "Created", "Error"]
        rows = []
        for j in result:
            error = (j.last_error or "")[:40]
            rows.append([
                j.ulid[:12] + "...",
                j.status,
                j.queue_name,
                j.task,
                _format_ts(j.created_at),
                error,
            ])
        _echo_table(headers, rows)
        click.echo(f"\n{len(result)} jobs shown")


# ---------------------------------------------------------------------------
# qler job <id>
# ---------------------------------------------------------------------------

@cli.command()
@click.argument("ulid")
@click.option("--db", required=True, envvar="QLER_DB", help="Database file path")
@click.option("--json", "output_json", is_flag=True, help="Output JSON")
def job(ulid: str, db: str, output_json: bool) -> None:
    """Show job detail."""
    from sqler import F

    async def _job() -> Job | None:
        q = await _get_queue(db)
        try:
            return await Job.query().filter(F("ulid") == ulid).first()
        finally:
            await q.close()

    result = _run(_job())

    if result is None:
        if output_json:
            _echo_json({"error": f"Job {ulid} not found"})
        else:
            click.echo(f"Job {ulid} not found.", err=True)
        sys.exit(1)

    if output_json:
        _echo_json(_job_to_dict(result))
    else:
        click.echo(f"ULID:            {result.ulid}")
        click.echo(f"Status:          {result.status}")
        click.echo(f"Queue:           {result.queue_name}")
        click.echo(f"Task:            {result.task}")
        click.echo(f"Priority:        {result.priority}")
        click.echo(f"ETA:             {_format_ts(result.eta)}")
        click.echo(f"Created:         {_format_ts(result.created_at)}")
        click.echo(f"Updated:         {_format_ts(result.updated_at)}")
        click.echo(f"Finished:        {_format_ts(result.finished_at)}")
        click.echo(f"Attempts:        {result.attempts}")
        click.echo(f"Retry count:     {result.retry_count}/{result.max_retries}")
        click.echo(f"Lease duration:  {_format_duration(result.lease_duration)}")
        click.echo(f"Lease expires:   {_format_ts(result.lease_expires_at)}")
        click.echo(f"Worker:          {result.worker_id or '-'}")
        click.echo(f"Correlation ID:  {result.correlation_id or '-'}")
        click.echo(f"Idempotency key: {result.idempotency_key or '-'}")
        if result.cancel_requested:
            click.echo(f"Cancel req:      Yes")
        payload = _safe_json(result.payload_json)
        click.echo(f"Payload:         {json.dumps(payload, indent=2)}")
        if result.result_json:
            result_data = _safe_json(result.result_json)
            click.echo(f"Result:          {json.dumps(result_data, indent=2)}")
        if result.last_error:
            click.echo(f"Last error:      {result.last_error}")
            click.echo(f"Failure kind:    {result.last_failure_kind}")


# ---------------------------------------------------------------------------
# qler attempts <id>
# ---------------------------------------------------------------------------

@cli.command()
@click.argument("ulid")
@click.option("--db", required=True, envvar="QLER_DB", help="Database file path")
@click.option("--json", "output_json", is_flag=True, help="Output JSON")
def attempts(ulid: str, db: str, output_json: bool) -> None:
    """Show attempt history for a job."""
    from sqler import F

    async def _attempts() -> list[JobAttempt]:
        q = await _get_queue(db)
        try:
            return await JobAttempt.query().filter(
                F("job_ulid") == ulid
            ).order_by("ulid").all()
        finally:
            await q.close()

    result = _run(_attempts())

    if output_json:
        _echo_json([_attempt_to_dict(a) for a in result])
    else:
        if not result:
            click.echo(f"No attempts found for job {ulid}.")
            return
        headers = ["#", "Status", "Worker", "Started", "Finished", "Error"]
        rows = []
        for a in result:
            error = (a.error or "")[:40]
            rows.append([
                str(a.attempt_number),
                a.status,
                a.worker_id[:20] + "..." if len(a.worker_id) > 20 else a.worker_id,
                _format_ts(a.started_at),
                _format_ts(a.finished_at),
                error,
            ])
        _echo_table(headers, rows)


# ---------------------------------------------------------------------------
# qler retry
# ---------------------------------------------------------------------------

@cli.command("retry")
@click.argument("ulid", required=False)
@click.option("--db", required=True, envvar="QLER_DB", help="Database file path")
@click.option("--status", "filter_status", default="failed",
              type=click.Choice(["failed", "cancelled"], case_sensitive=False))
@click.option("--task", "filter_task", help="Filter by task path")
@click.option("--all", "retry_all", is_flag=True, help="Retry all matching jobs")
@click.option("--json", "output_json", is_flag=True, help="Output JSON")
def retry_cmd(
    ulid: str | None,
    db: str,
    filter_status: str,
    filter_task: str | None,
    retry_all: bool,
    output_json: bool,
) -> None:
    """Retry failed jobs by ID or filter."""
    from sqler import F

    if ulid is None and not retry_all:
        raise click.UsageError("Provide a job ULID or use --all for bulk retry")

    async def _retry() -> list[str]:
        q = await _get_queue(db)
        try:
            retried: list[str] = []
            if ulid:
                j = await Job.query().filter(F("ulid") == ulid).first()
                if j is None:
                    raise click.ClickException(f"Job {ulid} not found")
                ok = await j.retry()
                if ok:
                    retried.append(j.ulid)
                else:
                    raise click.ClickException(
                        f"Cannot retry job {ulid} (status: {j.status})"
                    )
            else:
                filters = [F("status") == filter_status]
                if filter_task:
                    filters.append(F("task") == filter_task)
                combined = filters[0]
                for f in filters[1:]:
                    combined = combined & f
                matching = await Job.query().filter(combined).all()
                for j in matching:
                    ok = await j.retry()
                    if ok:
                        retried.append(j.ulid)
            return retried
        finally:
            await q.close()

    result = _run(_retry())

    if output_json:
        _echo_json({"retried": len(result), "ulids": result})
    else:
        if result:
            click.echo(f"Retried {len(result)} job(s):")
            for u in result:
                click.echo(f"  {u}")
        else:
            click.echo("No jobs retried.")


# ---------------------------------------------------------------------------
# qler cancel
# ---------------------------------------------------------------------------

@cli.command("cancel")
@click.argument("ulid", required=False)
@click.option("--db", required=True, envvar="QLER_DB", help="Database file path")
@click.option("--queue", "filter_queue", help="Filter by queue name")
@click.option("--task", "filter_task", help="Filter by task path")
@click.option("--all", "cancel_all", is_flag=True, help="Cancel all matching jobs")
@click.option("--running", "include_running", is_flag=True,
              help="Also request cancellation of running jobs")
@click.option("--json", "output_json", is_flag=True, help="Output JSON")
def cancel_cmd(
    ulid: str | None,
    db: str,
    filter_queue: str | None,
    filter_task: str | None,
    cancel_all: bool,
    include_running: bool,
    output_json: bool,
) -> None:
    """Cancel pending jobs by ID or filter."""
    from sqler import F

    if ulid is None and not cancel_all:
        raise click.UsageError("Provide a job ULID or use --all for bulk cancel")

    async def _cancel() -> tuple[list[str], list[str]]:
        q = await _get_queue(db)
        try:
            cancelled: list[str] = []
            requested: list[str] = []
            if ulid:
                j = await Job.query().filter(F("ulid") == ulid).first()
                if j is None:
                    raise click.ClickException(f"Job {ulid} not found")
                if j.status == JobStatus.RUNNING.value:
                    if not include_running:
                        raise click.ClickException(
                            f"Job {ulid} is running. Use --running to request cancellation."
                        )
                    ok = await j.request_cancel()
                    if ok:
                        requested.append(j.ulid)
                    else:
                        raise click.ClickException(
                            f"Cannot request cancellation for job {ulid}"
                        )
                else:
                    ok = await j.cancel()
                    if ok:
                        cancelled.append(j.ulid)
                    else:
                        raise click.ClickException(
                            f"Cannot cancel job {ulid} (status: {j.status})"
                        )
            else:
                # Cancel PENDING jobs
                filters = [F("status") == JobStatus.PENDING.value]
                if filter_queue:
                    filters.append(F("queue_name") == filter_queue)
                if filter_task:
                    filters.append(F("task") == filter_task)
                combined = filters[0]
                for f in filters[1:]:
                    combined = combined & f
                matching = await Job.query().filter(combined).all()
                for j in matching:
                    ok = await j.cancel()
                    if ok:
                        # cancel() may have delegated to request_cancel() if
                        # the job was claimed between the query and the cancel
                        if j.status == JobStatus.RUNNING.value:
                            requested.append(j.ulid)
                        else:
                            cancelled.append(j.ulid)

                # Request cancellation for RUNNING jobs if --running
                if include_running:
                    running_filters = [F("status") == JobStatus.RUNNING.value]
                    if filter_queue:
                        running_filters.append(F("queue_name") == filter_queue)
                    if filter_task:
                        running_filters.append(F("task") == filter_task)
                    combined = running_filters[0]
                    for f in running_filters[1:]:
                        combined = combined & f
                    running_jobs = await Job.query().filter(combined).all()
                    for j in running_jobs:
                        ok = await j.request_cancel()
                        if ok:
                            requested.append(j.ulid)

            return cancelled, requested
        finally:
            await q.close()

    cancelled, requested = _run(_cancel())

    if output_json:
        _echo_json({
            "cancelled": len(cancelled),
            "cancellation_requested": len(requested),
            "cancelled_ulids": cancelled,
            "requested_ulids": requested,
        })
    else:
        if cancelled:
            click.echo(f"Cancelled {len(cancelled)} job(s):")
            for u in cancelled:
                click.echo(f"  {u}")
        if requested:
            click.echo(f"Cancellation requested for {len(requested)} running job(s):")
            for u in requested:
                click.echo(f"  {u}")
        if not cancelled and not requested:
            click.echo("No jobs cancelled.")


# ---------------------------------------------------------------------------
# qler purge
# ---------------------------------------------------------------------------

@cli.command()
@click.option("--db", required=True, envvar="QLER_DB", help="Database file path")
@click.option("--older-than", required=True, help="Age threshold, e.g. '7d', '24h'")
@click.option("--status", "filter_status",
              type=click.Choice(["completed", "failed", "cancelled"], case_sensitive=False),
              help="Only purge this status (default: all terminal)")
@click.option("--json", "output_json", is_flag=True, help="Output JSON")
def purge(db: str, older_than: str, filter_status: str | None, output_json: bool) -> None:
    """Delete terminal jobs older than threshold."""
    from sqler import F

    threshold = _parse_since(older_than)

    async def _purge() -> int:
        q = await _get_queue(db)
        try:
            if filter_status:
                statuses = [filter_status]
            else:
                statuses = list(TERMINAL_STATUSES)

            # Find matching jobs (promoted columns are used by queryset filter)
            job_filter = F("status").in_list(statuses) & (F("created_at") <= threshold)
            matching_jobs = await Job.query().filter(job_filter).all()

            if not matching_jobs:
                return 0

            # Use raw SQL for deletion because promoted columns (status) are
            # stored in real columns but cleared from the JSON blob after
            # update_one(), so queryset.delete_all() (which uses JSON_EXTRACT)
            # won't match them.
            ulids = [j.ulid for j in matching_jobs]
            adapter = q.db.adapter
            placeholders = ",".join("?" for _ in ulids)

            # Delete attempts first (cascade)
            sql = f"DELETE FROM qler_job_attempts WHERE job_ulid IN ({placeholders})"
            cur = await adapter.execute(sql, ulids)
            await cur.close()

            # Delete jobs
            sql = f"DELETE FROM qler_jobs WHERE ulid IN ({placeholders})"
            cur = await adapter.execute(sql, ulids)
            await adapter.auto_commit()
            await cur.close()

            return len(matching_jobs)
        finally:
            await q.close()

    result = _run(_purge())

    if output_json:
        _echo_json({"purged": result})
    else:
        click.echo(f"Purged {result} jobs.")


# ---------------------------------------------------------------------------
# qler doctor
# ---------------------------------------------------------------------------

@cli.command()
@click.option("--db", required=True, envvar="QLER_DB", help="Database file path")
@click.option("--module", "-m", "modules", multiple=True,
              help="Import module(s) for orphaned task check")
@click.option("--json", "output_json", is_flag=True, help="Output JSON")
def doctor(db: str, modules: tuple[str, ...], output_json: bool) -> None:
    """Run health checks on the qler database."""
    from sqler import F

    async def _doctor() -> list[dict[str, Any]]:
        checks: list[dict[str, Any]] = []

        q = await _get_queue(db)
        try:
            # 1. Schema exists
            try:
                cur = await q.db.adapter.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' "
                    "AND name IN ('qler_jobs', 'qler_job_attempts')"
                )
                tables = [row[0] for row in await cur.fetchall()]
                await cur.close()
                has_jobs = "qler_jobs" in tables
                has_attempts = "qler_job_attempts" in tables
                checks.append({
                    "check": "schema",
                    "ok": has_jobs and has_attempts,
                    "detail": f"qler_jobs={'ok' if has_jobs else 'MISSING'}, "
                              f"qler_job_attempts={'ok' if has_attempts else 'MISSING'}",
                })
            except Exception as exc:
                checks.append({"check": "schema", "ok": False, "detail": str(exc)})

            # 2. WAL mode
            try:
                cur = await q.db.adapter.execute("PRAGMA journal_mode")
                row = await cur.fetchone()
                await cur.close()
                mode = row[0] if row else "unknown"
                checks.append({
                    "check": "wal_mode",
                    "ok": mode == "wal",
                    "detail": f"journal_mode={mode}",
                })
            except Exception as exc:
                checks.append({"check": "wal_mode", "ok": False, "detail": str(exc)})

            # 3. Expired leases
            try:
                now = now_epoch()
                expired = await Job.query().filter(
                    (F("status") == JobStatus.RUNNING.value)
                    & (F("lease_expires_at") <= now)
                ).all()
                checks.append({
                    "check": "expired_leases",
                    "ok": len(expired) == 0,
                    "detail": f"{len(expired)} expired lease(s)",
                })
            except Exception as exc:
                checks.append({"check": "expired_leases", "ok": False, "detail": str(exc)})

            # 4. Orphaned tasks (requires --module)
            if modules:
                _import_modules(modules)
                try:
                    running = await Job.query().filter(
                        F("status") == JobStatus.RUNNING.value
                    ).all()
                    orphaned = [j for j in running if j.task not in q._tasks]
                    checks.append({
                        "check": "orphaned_tasks",
                        "ok": len(orphaned) == 0,
                        "detail": f"{len(orphaned)} running job(s) with unregistered tasks",
                    })
                except Exception as exc:
                    checks.append({"check": "orphaned_tasks", "ok": False, "detail": str(exc)})

            # 5. Stale pending
            try:
                stale_threshold = now_epoch() - 86400  # 24h
                stale = await Job.query().filter(
                    (F("status") == JobStatus.PENDING.value)
                    & (F("created_at") <= stale_threshold)
                ).all()
                checks.append({
                    "check": "stale_pending",
                    "ok": len(stale) == 0,
                    "detail": f"{len(stale)} pending job(s) older than 24h",
                })
            except Exception as exc:
                checks.append({"check": "stale_pending", "ok": False, "detail": str(exc)})

            # 6. Database size
            try:
                cur = await q.db.adapter.execute("SELECT COUNT(*) FROM qler_jobs")
                job_count = (await cur.fetchone())[0]
                await cur.close()
                cur = await q.db.adapter.execute("SELECT COUNT(*) FROM qler_job_attempts")
                attempt_count = (await cur.fetchone())[0]
                await cur.close()
                db_size = os.path.getsize(db) if os.path.exists(db) else 0
                checks.append({
                    "check": "database_size",
                    "ok": True,
                    "detail": f"{job_count} jobs, {attempt_count} attempts, "
                              f"{db_size / 1024:.1f} KB",
                })
            except Exception as exc:
                checks.append({"check": "database_size", "ok": False, "detail": str(exc)})

            return checks
        finally:
            await q.close()

    result = _run(_doctor())

    if output_json:
        _echo_json(result)
    else:
        all_ok = True
        for check in result:
            marker = "OK" if check["ok"] else "FAIL"
            if not check["ok"]:
                all_ok = False
            click.echo(f"  [{marker:>4}] {check['check']}: {check['detail']}")
        click.echo()
        if all_ok:
            click.echo("All checks passed.")
        else:
            click.echo("Some checks failed.")
            sys.exit(1)
