"""qler CLI — human-first commands with --json flag."""

from __future__ import annotations

import asyncio
import importlib
import json
import os
import re
import socket as socket_mod
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
from qler.cron import CronWrapper

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
# qler backup
# ---------------------------------------------------------------------------

@cli.command()
@click.option("--db", required=True, envvar="QLER_DB", help="Database file path")
@click.option("--to", "destination", required=True, type=click.Path(), help="Backup destination path")
@click.option("--json", "output_json", is_flag=True, help="Output JSON")
def backup(db: str, destination: str, output_json: bool) -> None:
    """Create a safe online backup of the qler database."""
    from sqler import async_backup

    if os.path.exists(destination):
        if output_json:
            _echo_json({"error": f"Destination already exists: {destination}"})
        else:
            click.echo(f"Destination already exists: {destination}", err=True)
        sys.exit(1)

    async def _backup():
        q = await _get_queue(db)
        try:
            return await async_backup(q.db, destination)
        finally:
            await q.close()

    result = _run(_backup())

    if output_json:
        _echo_json(result.to_dict())
    else:
        if result.success:
            click.echo(f"Backup complete: {result.destination_path}")
            click.echo(f"  Source: {result.source_path}")
            click.echo(f"  Size:   {result.size_bytes / 1024:.1f} KB")
            click.echo(f"  Time:   {result.duration_ms:.0f} ms")
        else:
            click.echo(f"Backup failed: {result.error}", err=True)
            sys.exit(1)


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
@click.option("--health-port", type=int, default=None, help="TCP port for health endpoint")
@click.option("--health-socket", type=str, default=None, help="Unix socket path for health endpoint")
def worker(
    db: str | None,
    app_string: str | None,
    modules: tuple[str, ...],
    queues: str,
    concurrency: int,
    poll_interval: float,
    shutdown_timeout: float,
    health_port: int | None,
    health_socket: str | None,
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
        health_port=health_port,
        health_socket=health_socket,
    )
    click.echo(f"Worker {w.worker_id} starting (queues={queue_list}, concurrency={concurrency})")
    if health_port is not None:
        click.echo(f"Health endpoint listening on 127.0.0.1:{health_port}")
    elif health_socket is not None:
        click.echo(f"Health endpoint listening on unix:{health_socket}")
    try:
        _run(w.run())
    except KeyboardInterrupt:
        pass
    finally:
        click.echo("Worker stopped")


# ---------------------------------------------------------------------------
# qler health
# ---------------------------------------------------------------------------

@cli.command()
@click.option("--port", type=int, default=None, help="TCP port of health endpoint")
@click.option("--socket", "socket_path", type=str, default=None, help="Unix socket path of health endpoint")
@click.option("--host", default="127.0.0.1", help="Host for TCP health endpoint")
@click.option("--timeout", default=5.0, type=float, help="Connection timeout in seconds")
@click.option("--json", "output_json", is_flag=True, help="Output JSON")
def health(
    port: int | None,
    socket_path: str | None,
    host: str,
    timeout: float,
    output_json: bool,
) -> None:
    """Query a running worker's health endpoint."""
    if port is None and socket_path is None:
        raise click.UsageError("Either --port or --socket is required")
    if port is not None and socket_path is not None:
        raise click.UsageError("--port and --socket are mutually exclusive")

    try:
        if socket_path is not None:
            s = socket_mod.socket(socket_mod.AF_UNIX, socket_mod.SOCK_STREAM)
            s.settimeout(timeout)
            s.connect(socket_path)
        else:
            s = socket_mod.create_connection((host, port), timeout=timeout)

        safe_host = host.replace("\r", "").replace("\n", "")
        request = f"GET /health HTTP/1.0\r\nHost: {safe_host}\r\n\r\n"
        s.sendall(request.encode())

        chunks = []
        while True:
            chunk = s.recv(4096)
            if not chunk:
                break
            chunks.append(chunk)
        s.close()

        raw = b"".join(chunks).decode("utf-8", errors="replace")
        # Split headers from body
        if "\r\n\r\n" in raw:
            body = raw.split("\r\n\r\n", 1)[1]
        elif "\n\n" in raw:
            body = raw.split("\n\n", 1)[1]
        else:
            body = raw

        data = json.loads(body)

        if output_json:
            _echo_json(data)
        else:
            click.echo(f"Status:       {data['status']}")
            click.echo(f"Worker ID:    {data['worker_id']}")
            click.echo(f"Uptime:       {_format_duration(data['uptime_seconds'])}")
            click.echo(f"Active jobs:  {data['active_jobs']}/{data['concurrency']}")
            click.echo(f"Queues:       {', '.join(data['queues'])}")
            click.echo(f"Started:      {_format_ts(data['started_at'])}")

    except (ConnectionRefusedError, FileNotFoundError, OSError) as exc:
        if output_json:
            _echo_json({"error": str(exc)})
        else:
            click.echo(f"Health check failed: {exc}", err=True)
        sys.exit(1)
    except (json.JSONDecodeError, KeyError) as exc:
        if output_json:
            _echo_json({"error": f"Invalid response: {exc}"})
        else:
            click.echo(f"Invalid health response: {exc}", err=True)
        sys.exit(1)


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
        if result.progress is not None:
            progress_str = f"{result.progress}%"
            if result.progress_message:
                progress_str += f" — {result.progress_message}"
            click.echo(f"Progress:        {progress_str}")
        if result.cancel_requested:
            click.echo(f"Cancel req:      Yes")
        if result.dependencies:
            click.echo(f"Dependencies:    {len(result.dependencies)} job(s)")
            click.echo(f"  Pending deps:  {result.pending_dep_count}")
            for dep_ulid in result.dependencies:
                click.echo(f"  - {dep_ulid}")
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
                    ok = await q.cancel_job(j)
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
                    ok = await q.cancel_job(j)
                    if ok:
                        # cancel_job() may have delegated to request_cancel() if
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

            job_filter = F("status").in_list(statuses) & (F("created_at") <= threshold)
            matching_jobs = await Job.query().filter(job_filter).all()

            if not matching_jobs:
                return 0

            # Delete attempts first (cascade)
            ulids = [j.ulid for j in matching_jobs]
            await JobAttempt.query().filter(
                F("job_ulid").in_list(ulids)
            ).delete_all()

            # Delete jobs
            deleted = await Job.query().filter(job_filter).delete_all()
            return deleted
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
                job_count = await Job.query().count()
                attempt_count = await JobAttempt.query().count()
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


# ---------------------------------------------------------------------------
# qler cron
# ---------------------------------------------------------------------------

@cli.command("cron")
@click.option("--db", envvar="QLER_DB", help="Database file path")
@click.option("--app", "app_string", help="Queue instance as 'module:attribute'")
@click.option("--module", "-m", "modules", multiple=True, help="Import module(s) to register cron tasks")
@click.option("--json", "output_json", is_flag=True, help="Output JSON")
def cron_cmd(
    db: str | None,
    app_string: str | None,
    modules: tuple[str, ...],
    output_json: bool,
) -> None:
    """List registered cron tasks with schedules and status."""
    from sqler import F

    if app_string:
        q = _import_app(app_string)
    elif db:
        q = Queue(db)
    else:
        raise click.UsageError("Either --db or --app is required")

    _import_modules(modules)

    async def _cron() -> list[dict[str, Any]]:
        await q.init_db()
        try:
            tasks = []
            for path, cw in q._cron_tasks.items():
                # Count active jobs for this cron task
                active_count = await Job.query().filter(
                    (F("task") == path)
                    & F("status").in_list(["pending", "running"])
                ).count()

                next_run = cw.next_run()
                tasks.append({
                    "task": path,
                    "expression": cw.schedule.expression,
                    "max_running": cw.schedule.max_running,
                    "timezone": cw.schedule.timezone,
                    "next_run": next_run.strftime("%Y-%m-%dT%H:%M:%SZ"),
                    "active_jobs": active_count,
                })
            return tasks
        finally:
            await q.close()

    result = _run(_cron())

    if output_json:
        _echo_json(result)
    else:
        if not result:
            click.echo("No cron tasks registered.")
            click.echo("Use --app or --module to load tasks.")
            return
        headers = ["Task", "Schedule", "Max Running", "Next Run", "Active"]
        rows = []
        for t in result:
            rows.append([
                t["task"],
                t["expression"],
                str(t["max_running"]),
                t["next_run"],
                str(t["active_jobs"]),
            ])
        _echo_table(headers, rows)


# ---------------------------------------------------------------------------
# qler tasks
# ---------------------------------------------------------------------------

@cli.command("tasks")
@click.option("--db", envvar="QLER_DB", help="Database file path")
@click.option("--app", "app_string", help="Queue instance as 'module:attribute'")
@click.option("--module", "-m", "modules", multiple=True, help="Import module(s) to register tasks")
@click.option("--queue", "filter_queue", help="Filter by queue name")
@click.option("--json", "output_json", is_flag=True, help="Output JSON")
def tasks_cmd(
    db: str | None,
    app_string: str | None,
    modules: tuple[str, ...],
    filter_queue: str | None,
    output_json: bool,
) -> None:
    """List registered tasks with their configuration."""
    from sqler import F

    if app_string:
        q = _import_app(app_string)
    elif db:
        q = Queue(db)
    else:
        raise click.UsageError("Either --db or --app is required")

    _import_modules(modules)

    async def _tasks() -> list[dict[str, Any]]:
        await q.init_db()
        try:
            tasks = []
            for path, tw in q._tasks.items():
                if filter_queue and tw.queue_name != filter_queue:
                    continue

                # Count active jobs (pending + running)
                active_count = await Job.query().filter(
                    (F("task") == path)
                    & F("status").in_list(["pending", "running"])
                ).count()

                # Check if this task has a cron schedule
                cron_expr = None
                if path in q._cron_tasks:
                    cron_expr = q._cron_tasks[path].schedule.expression

                # Format rate spec
                rate_str = None
                if tw.rate_spec is not None:
                    rate_str = f"{tw.rate_spec.limit}/{tw.rate_spec.window_seconds}s"

                tasks.append({
                    "task": path,
                    "queue": tw.queue_name,
                    "sync": tw.sync,
                    "max_retries": tw.max_retries,
                    "retry_delay": tw.retry_delay,
                    "priority": tw.priority,
                    "lease_duration": tw.lease_duration,
                    "rate_limit": rate_str,
                    "cron": cron_expr,
                    "idempotency_key": tw.idempotency_key_fn is not None,
                    "timeout": tw._timeout,
                    "active_jobs": active_count,
                })
            return tasks
        finally:
            await q.close()

    result = _run(_tasks())

    if output_json:
        _echo_json(result)
    else:
        if not result:
            click.echo("No tasks registered.")
            click.echo("Use --app or --module to load tasks.")
            return
        headers = ["Task", "Queue", "Retries", "Rate", "Cron", "Active"]
        rows = []
        for t in result:
            rows.append([
                t["task"],
                t["queue"],
                str(t["max_retries"]),
                t["rate_limit"] or "-",
                t["cron"] or "-",
                str(t["active_jobs"]),
            ])
        _echo_table(headers, rows)


# ---------------------------------------------------------------------------
# DLQ helpers
# ---------------------------------------------------------------------------

def _echo_result(
    data: Any = None,
    *,
    ok: bool = True,
    count: int | None = None,
    error: str | None = None,
    human: bool = False,
    headers: list[str] | None = None,
    rows: list[list[str]] | None = None,
) -> None:
    """Emit a JSON envelope (default) or human table.

    JSON envelope: {"ok": true, "data": ..., "count": N}
    Error envelope: {"ok": false, "error": "message"}
    """
    if human:
        if error:
            click.echo(f"Error: {error}", err=True)
        elif headers and rows is not None:
            if not rows:
                click.echo("No results.")
            else:
                _echo_table(headers, rows)
                if count is not None:
                    click.echo(f"\n{count} item(s)")
        elif count is not None and data is None:
            click.echo(f"Count: {count}")
        elif data is not None:
            click.echo(json.dumps(data, indent=2, default=str))
    else:
        envelope: dict[str, Any] = {"ok": ok}
        if error:
            envelope["error"] = error
        if data is not None:
            envelope["data"] = data
        if count is not None:
            envelope["count"] = count
        click.echo(json.dumps(envelope, separators=(",", ":"), default=str))


# ---------------------------------------------------------------------------
# qler dlq (Dead Letter Queue)
# ---------------------------------------------------------------------------

@cli.group()
@click.option("--db", required=True, envvar="QLER_DB", help="Database file path")
@click.option("--dlq", default="dead_letters", help="DLQ queue name (default: dead_letters)")
@click.option("--human", is_flag=True, help="Human-readable table output (default is JSON)")
@click.pass_context
def dlq(ctx: click.Context, db: str, dlq: str, human: bool) -> None:
    """Dead letter queue operations."""
    ctx.ensure_object(dict)
    ctx.obj["db"] = db
    ctx.obj["dlq"] = dlq
    ctx.obj["human"] = human


@dlq.command("list")
@click.option("--limit", default=50, type=int, help="Max jobs to return")
@click.option("--since", help="Time window, e.g. '1h', '30m', '7d'")
@click.option("--task", "filter_task", help="Filter by task path")
@click.pass_context
def dlq_list(ctx: click.Context, limit: int, since: str | None, filter_task: str | None) -> None:
    """List jobs in the dead letter queue."""
    from sqler import F

    db_path = ctx.obj["db"]
    dlq_name = ctx.obj["dlq"]
    human = ctx.obj["human"]

    async def _list() -> list[Job]:
        q = await _get_queue(db_path)
        try:
            filters = [
                F("queue_name") == dlq_name,
                F("status") == JobStatus.FAILED.value,
            ]
            if filter_task:
                filters.append(F("task") == filter_task)
            if since:
                threshold = _parse_since(since)
                filters.append(F("created_at") >= threshold)

            combined = filters[0]
            for f in filters[1:]:
                combined = combined & f

            return await Job.query().filter(combined).order_by("-ulid").limit(limit).all()
        finally:
            await q.close()

    result = _run(_list())

    data = [
        {
            "ulid": j.ulid,
            "task": j.task,
            "error": j.last_error,
            "attempts": j.attempts,
            "original_queue": j.original_queue,
            "created_at": j.created_at,
        }
        for j in result
    ]

    if human:
        headers = ["ULID", "Task", "Original Queue", "Attempts", "Error"]
        rows = [
            [
                j.ulid[:12] + "...",
                j.task,
                j.original_queue or "-",
                str(j.attempts),
                (j.last_error or "")[:40],
            ]
            for j in result
        ]
        _echo_result(human=True, headers=headers, rows=rows, count=len(result))
    else:
        _echo_result(data=data, count=len(result))


@dlq.command("count")
@click.pass_context
def dlq_count(ctx: click.Context) -> None:
    """Count jobs in the dead letter queue."""
    from sqler import F

    db_path = ctx.obj["db"]
    dlq_name = ctx.obj["dlq"]
    human = ctx.obj["human"]

    async def _count() -> int:
        q = await _get_queue(db_path)
        try:
            return await Job.query().filter(
                (F("queue_name") == dlq_name) & (F("status") == JobStatus.FAILED.value)
            ).count()
        finally:
            await q.close()

    result = _run(_count())
    _echo_result(count=result, human=human)


@dlq.command("job")
@click.argument("ulid")
@click.pass_context
def dlq_job(ctx: click.Context, ulid: str) -> None:
    """Show full details of a DLQ job."""
    from sqler import F

    db_path = ctx.obj["db"]
    dlq_name = ctx.obj["dlq"]
    human = ctx.obj["human"]

    async def _job() -> Job | None:
        q = await _get_queue(db_path)
        try:
            return await Job.query().filter(
                (F("ulid") == ulid)
                & (F("queue_name") == dlq_name)
                & (F("status") == JobStatus.FAILED.value)
            ).first()
        finally:
            await q.close()

    result = _run(_job())

    if result is None:
        _echo_result(ok=False, error=f"Job {ulid} not found in DLQ '{dlq_name}'", human=human)
        sys.exit(1)

    _echo_result(data=_job_to_dict(result), human=human)


@dlq.command("replay")
@click.argument("ulid", required=False)
@click.option("--all", "replay_all", is_flag=True, help="Replay all DLQ jobs")
@click.option("--queue", "target_queue", help="Override target queue for replay")
@click.pass_context
def dlq_replay(
    ctx: click.Context,
    ulid: str | None,
    replay_all: bool,
    target_queue: str | None,
) -> None:
    """Replay a failed job from the DLQ back to its original queue."""
    from sqler import F

    if ulid is None and not replay_all:
        raise click.UsageError("Provide a job ULID or use --all for bulk replay")

    db_path = ctx.obj["db"]
    dlq_name = ctx.obj["dlq"]
    human = ctx.obj["human"]

    async def _replay() -> list[dict[str, str]]:
        q = Queue(db_path, dlq=dlq_name)
        await q.init_db()
        try:
            replayed: list[dict[str, str]] = []
            if ulid:
                j = await Job.query().filter(
                    (F("ulid") == ulid)
                    & (F("queue_name") == dlq_name)
                    & (F("status") == JobStatus.FAILED.value)
                ).first()
                if j is None:
                    raise click.ClickException(
                        f"Job {ulid} not found in DLQ '{dlq_name}'"
                    )
                updated = await q.replay_job(j, queue_name=target_queue)
                if updated:
                    replayed.append({
                        "ulid": updated.ulid,
                        "queue_name": updated.queue_name,
                        "status": updated.status,
                    })
            else:
                jobs = await Job.query().filter(
                    (F("queue_name") == dlq_name)
                    & (F("status") == JobStatus.FAILED.value)
                ).all()
                for j in jobs:
                    updated = await q.replay_job(j, queue_name=target_queue)
                    if updated:
                        replayed.append({
                            "ulid": updated.ulid,
                            "queue_name": updated.queue_name,
                        })
            return replayed
        finally:
            await q.close()

    result = _run(_replay())

    if human:
        if result:
            click.echo(f"Replayed {len(result)} job(s):")
            for r in result:
                click.echo(f"  {r['ulid']} → {r.get('queue_name', '?')}")
        else:
            click.echo("No jobs replayed.")
    else:
        _echo_result(data=result, count=len(result))


@dlq.command("purge")
@click.option("--older-than", help="Age threshold, e.g. '7d', '24h'")
@click.option("--confirm", is_flag=True, help="Skip confirmation prompt")
@click.pass_context
def dlq_purge(ctx: click.Context, older_than: str | None, confirm: bool) -> None:
    """Permanently delete jobs from the dead letter queue."""
    from sqler import F

    db_path = ctx.obj["db"]
    dlq_name = ctx.obj["dlq"]
    human = ctx.obj["human"]

    async def _purge() -> int:
        q = await _get_queue(db_path)
        try:
            filters = [
                F("queue_name") == dlq_name,
                F("status") == JobStatus.FAILED.value,
            ]
            if older_than:
                threshold = _parse_since(older_than)
                filters.append(F("created_at") <= threshold)

            combined = filters[0]
            for f in filters[1:]:
                combined = combined & f

            # Delete associated attempts first
            matching_jobs = await Job.query().filter(combined).all()
            if not matching_jobs:
                return 0

            ulids = [j.ulid for j in matching_jobs]
            await JobAttempt.query().filter(
                F("job_ulid").in_list(ulids)
            ).delete_all()

            return await Job.query().filter(combined).delete_all()
        finally:
            await q.close()

    if not confirm and not older_than:
        if human:
            click.echo("Use --confirm to purge all DLQ jobs, or --older-than to filter.", err=True)
        else:
            _echo_result(ok=False, error="Use --confirm to purge all DLQ jobs, or --older-than to filter")
        sys.exit(1)

    result = _run(_purge())
    _echo_result(count=result, human=human)
