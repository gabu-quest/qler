"""qler status, jobs, job and attempts."""

from __future__ import annotations

import json
import sys
from typing import Any

import click

from qler._sqler import grouped_count
from qler.cli.group import cli
from qler.cli.helpers import (
    _attempt_to_dict,
    _echo_json,
    _echo_table,
    _format_duration,
    _format_ts,
    _get_queue,
    _job_to_dict,
    _parse_since,
    _run,
    _safe_json,
)
from qler.models.attempt import JobAttempt
from qler.models.job import Job

# ---------------------------------------------------------------------------
# qler status
# ---------------------------------------------------------------------------


@cli.command()
@click.option("--db", required=True, envvar="QLER_DB", help="Database file path")
@click.option("--include-archive", is_flag=True, help="Include archived jobs in counts")
@click.option("--json", "output_json", is_flag=True, help="Output JSON")
def status(db: str, include_archive: bool, output_json: bool) -> None:
    """Show queue depths and job counts."""

    async def _status() -> tuple[dict[str, dict[str, int]], dict[str, dict[str, int]]]:
        q = await _get_queue(db)
        try:
            rows = await grouped_count(q.jobs.group_by("queue_name", "status"))

            queues: dict[str, dict[str, int]] = {}
            for row in rows:
                queue_name, job_status = row["queue_name"], row["status"]
                if queue_name not in queues:
                    queues[queue_name] = {
                        "pending": 0,
                        "running": 0,
                        "completed": 0,
                        "failed": 0,
                        "cancelled": 0,
                    }
                queues[queue_name][job_status] = row["count"]

            archived: dict[str, dict[str, int]] = {}
            if include_archive:
                rows = await grouped_count(q.archive.group_by("queue_name", "status"))
                for row in rows:
                    queue_name, job_status = row["queue_name"], row["status"]
                    if queue_name not in archived:
                        archived[queue_name] = {
                            "completed": 0,
                            "failed": 0,
                            "cancelled": 0,
                        }
                    archived[queue_name][job_status] = row["count"]

            return queues, archived
        finally:
            await q.close()

    active, archived = _run(_status())
    total = sum(sum(v.values()) for v in active.values())
    archived_total = sum(sum(v.values()) for v in archived.values())

    if output_json:
        data: dict[str, Any] = {"queues": active, "total": total}
        if include_archive:
            data["archived"] = archived
            data["archived_total"] = archived_total
        _echo_json(data)
    else:
        if not active and not archived:
            click.echo("No jobs found.")
            return
        headers = ["Queue", "Pending", "Running", "Completed", "Failed", "Cancelled"]
        rows = []
        for name, counts in sorted(active.items()):
            rows.append(
                [
                    name,
                    str(counts["pending"]),
                    str(counts["running"]),
                    str(counts["completed"]),
                    str(counts["failed"]),
                    str(counts["cancelled"]),
                ]
            )
        _echo_table(headers, rows)
        click.echo(f"\nTotal: {total} jobs")
        if include_archive and archived:
            click.echo(f"\nArchived: {archived_total} jobs")
            headers = ["Queue", "Completed", "Failed", "Cancelled"]
            rows = []
            for name, counts in sorted(archived.items()):
                rows.append(
                    [
                        name,
                        str(counts.get("completed", 0)),
                        str(counts.get("failed", 0)),
                        str(counts.get("cancelled", 0)),
                    ]
                )
            _echo_table(headers, rows)


# ---------------------------------------------------------------------------
# qler jobs
# ---------------------------------------------------------------------------


@cli.command()
@click.option("--db", required=True, envvar="QLER_DB", help="Database file path")
@click.option(
    "--status",
    "filter_status",
    type=click.Choice(
        ["pending", "running", "completed", "failed", "cancelled"],
        case_sensitive=False,
    ),
)
@click.option("--queue", "filter_queue", help="Filter by queue name")
@click.option("--task", "filter_task", help="Filter by task path")
@click.option("--since", "since", help="Time window, e.g. '1h', '30m', '7d'")
@click.option("--limit", default=50, type=int, help="Max jobs to return")
@click.option("--include-archive", is_flag=True, help="Also search archived jobs")
@click.option("--json", "output_json", is_flag=True, help="Output JSON")
def jobs(
    db: str,
    filter_status: str | None,
    filter_queue: str | None,
    filter_task: str | None,
    since: str | None,
    limit: int,
    include_archive: bool,
    output_json: bool,
) -> None:
    """List jobs with optional filters."""
    from sqler import F

    async def _jobs() -> tuple[list[Job], list[Job]]:
        q = await _get_queue(db)
        try:
            query = q.jobs
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

            active = await query.order_by("-ulid").limit(limit).all()

            archived: list[Job] = []
            if include_archive:
                archived = await q.archived_jobs(
                    queue_name=filter_queue,
                    status=filter_status,
                    limit=limit,
                )

            return active, archived
        finally:
            await q.close()

    active, archived = _run(_jobs())

    if output_json:
        data = [_job_to_dict(j) for j in active]
        if include_archive:
            for j in archived:
                d = _job_to_dict(j)
                d["archived"] = True
                data.append(d)
        _echo_json(data)
    else:
        all_jobs = active + archived
        if not all_jobs:
            click.echo("No jobs found.")
            return
        headers = ["ULID", "Status", "Queue", "Task", "Created", "Error"]
        rows = []
        for j in active:
            error = (j.last_error or "")[:40]
            rows.append(
                [
                    j.ulid[:12] + "...",
                    j.status,
                    j.queue_name,
                    j.task,
                    _format_ts(j.created_at),
                    error,
                ]
            )
        for j in archived:
            error = (j.last_error or "")[:40]
            rows.append(
                [
                    j.ulid[:12] + "...",
                    j.status + " (archived)",
                    j.queue_name,
                    j.task,
                    _format_ts(j.created_at),
                    error,
                ]
            )
        _echo_table(headers, rows)
        shown = len(all_jobs)
        msg = f"\n{shown} jobs shown"
        if archived:
            msg += f" ({len(archived)} archived)"
        click.echo(msg)


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
            return await q.jobs.filter(F("ulid") == ulid).first()
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
            click.echo("Cancel req:      Yes")
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
            return await q.attempts.filter(F("job_ulid") == ulid).order_by("ulid").all()
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
            rows.append(
                [
                    str(a.attempt_number),
                    a.status,
                    a.worker_id[:20] + "..." if len(a.worker_id) > 20 else a.worker_id,
                    _format_ts(a.started_at),
                    _format_ts(a.finished_at),
                    error,
                ]
            )
        _echo_table(headers, rows)
