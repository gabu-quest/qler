"""qler worker and qler health."""

from __future__ import annotations

import json
import socket as socket_mod
import sys

import click

from qler.cli.group import cli
from qler.cli.helpers import (
    _echo_json,
    _format_duration,
    _format_ts,
    _import_app,
    _import_modules,
    _run,
)
from qler.queue import Queue

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
@click.option(
    "--health-socket", type=str, default=None, help="Unix socket path for health endpoint"
)
@click.option(
    "--archive-interval",
    type=float,
    default=None,
    help="Archival sweep interval in seconds (disabled by default)",
)
@click.option(
    "--archive-after",
    type=int,
    default=300,
    help="Archive jobs older than N seconds (default: 300)",
)
@click.option(
    "--memory-limit-mb",
    type=int,
    default=None,
    help="RSS limit in MB — triggers emergency archival (disabled by default)",
)
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
    archive_interval: float | None,
    archive_after: int,
    memory_limit_mb: int | None,
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
        archive_interval=archive_interval,
        archive_after=archive_after,
        memory_limit_mb=memory_limit_mb,
    )
    click.echo(f"Worker {w.worker_id} starting (queues={queue_list}, concurrency={concurrency})")
    if health_port is not None:
        click.echo(f"Health endpoint listening on 127.0.0.1:{health_port}")
    elif health_socket is not None:
        click.echo(f"Health endpoint listening on unix:{health_socket}")
    if archive_interval is not None:
        click.echo(f"Archival enabled: every {archive_interval}s, jobs older than {archive_after}s")
    if memory_limit_mb is not None:
        click.echo(f"Memory watchdog enabled: limit {memory_limit_mb} MB")
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
@click.option(
    "--socket", "socket_path", type=str, default=None, help="Unix socket path of health endpoint"
)
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
