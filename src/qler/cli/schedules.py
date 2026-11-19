"""qler cron and qler tasks."""

from __future__ import annotations

from typing import Any

import click

from qler.cli.group import cli
from qler.cli.helpers import (
    _echo_json,
    _echo_table,
    _import_app,
    _import_modules,
    _run,
)
from qler.enums import ACTIVE_STATUSES
from qler.queue import Queue

# ---------------------------------------------------------------------------
# qler cron
# ---------------------------------------------------------------------------


@cli.command("cron")
@click.option("--db", envvar="QLER_DB", help="Database file path")
@click.option("--app", "app_string", help="Queue instance as 'module:attribute'")
@click.option(
    "--module", "-m", "modules", multiple=True, help="Import module(s) to register cron tasks"
)
@click.option("--json", "output_json", is_flag=True, help="Output JSON")
def cron_cmd(
    db: str | None,
    app_string: str | None,
    modules: tuple[str, ...],
    output_json: bool,
) -> None:
    """List registered cron tasks with schedules and status."""

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
                active_count = await cw._active_count()

                next_run = cw.next_run()
                tasks.append(
                    {
                        "task": path,
                        "expression": cw.schedule.expression,
                        "max_running": cw.schedule.max_running,
                        "timezone": cw.schedule.timezone,
                        "next_run": next_run.strftime("%Y-%m-%dT%H:%M:%SZ"),
                        "active_jobs": active_count,
                    }
                )
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
            rows.append(
                [
                    t["task"],
                    t["expression"],
                    str(t["max_running"]),
                    t["next_run"],
                    str(t["active_jobs"]),
                ]
            )
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
                active_count = await q.jobs.filter(
                    (F("task") == path) & F("status").in_list(list(ACTIVE_STATUSES))
                ).count()

                # Check if this task has a cron schedule
                cron_expr = None
                if path in q._cron_tasks:
                    cron_expr = q._cron_tasks[path].schedule.expression

                # Format rate spec
                rate_str = None
                if tw.rate_spec is not None:
                    rate_str = f"{tw.rate_spec.limit}/{tw.rate_spec.window_seconds}s"

                tasks.append(
                    {
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
                        "unique": tw._unique or tw.unique_key_fn is not None,
                        "unique_key": tw.unique_key_fn is not None,
                        "active_jobs": active_count,
                    }
                )
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
            rows.append(
                [
                    t["task"],
                    t["queue"],
                    str(t["max_retries"]),
                    t["rate_limit"] or "-",
                    t["cron"] or "-",
                    str(t["active_jobs"]),
                ]
            )
        _echo_table(headers, rows)
