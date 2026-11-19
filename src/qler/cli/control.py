"""qler retry and qler cancel."""

from __future__ import annotations

import click

from qler.cli.group import cli
from qler.cli.helpers import (
    _echo_json,
    _get_queue,
    _run,
)
from qler.enums import JobStatus

# ---------------------------------------------------------------------------
# qler retry
# ---------------------------------------------------------------------------


@cli.command("retry")
@click.argument("ulid", required=False)
@click.option("--db", required=True, envvar="QLER_DB", help="Database file path")
@click.option(
    "--status",
    "filter_status",
    default="failed",
    type=click.Choice(["failed", "cancelled"], case_sensitive=False),
)
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
                j = await q.jobs.filter(F("ulid") == ulid).first()
                if j is None:
                    raise click.ClickException(f"Job {ulid} not found")
                ok = await j.retry()
                if ok:
                    retried.append(j.ulid)
                else:
                    raise click.ClickException(f"Cannot retry job {ulid} (status: {j.status})")
            else:
                filters = [F("status") == filter_status]
                if filter_task:
                    filters.append(F("task") == filter_task)
                combined = filters[0]
                for f in filters[1:]:
                    combined = combined & f
                matching = await q.jobs.filter(combined).all()
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
@click.option(
    "--running", "include_running", is_flag=True, help="Also request cancellation of running jobs"
)
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
                j = await q.jobs.filter(F("ulid") == ulid).first()
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
                        raise click.ClickException(f"Cannot request cancellation for job {ulid}")
                else:
                    ok = await q.cancel_job(j)
                    if ok:
                        cancelled.append(j.ulid)
                    else:
                        raise click.ClickException(f"Cannot cancel job {ulid} (status: {j.status})")
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
                matching = await q.jobs.filter(combined).all()
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
                    running_jobs = await q.jobs.filter(combined).all()
                    for j in running_jobs:
                        ok = await j.request_cancel()
                        if ok:
                            requested.append(j.ulid)

            return cancelled, requested
        finally:
            await q.close()

    cancelled, requested = _run(_cancel())

    if output_json:
        _echo_json(
            {
                "cancelled": len(cancelled),
                "cancellation_requested": len(requested),
                "cancelled_ulids": cancelled,
                "requested_ulids": requested,
            }
        )
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
