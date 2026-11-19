"""qler dlq: the dead letter queue commands."""

from __future__ import annotations

import json
import sys
from typing import Any

import click

from qler.cli.group import cli
from qler.cli.helpers import (
    _echo_table,
    _get_queue,
    _job_to_dict,
    _parse_since,
    _run,
)
from qler.enums import JobStatus
from qler.models.job import Job
from qler.queue import Queue

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

            return await q.jobs.filter(combined).order_by("-ulid").limit(limit).all()
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
            return await q.jobs.filter(
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
            return await q.jobs.filter(
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
                j = await q.jobs.filter(
                    (F("ulid") == ulid)
                    & (F("queue_name") == dlq_name)
                    & (F("status") == JobStatus.FAILED.value)
                ).first()
                if j is None:
                    raise click.ClickException(f"Job {ulid} not found in DLQ '{dlq_name}'")
                updated = await q.replay_job(j, queue_name=target_queue)
                if updated:
                    replayed.append(
                        {
                            "ulid": updated.ulid,
                            "queue_name": updated.queue_name,
                            "status": updated.status,
                        }
                    )
            else:
                jobs = await q.jobs.filter(
                    (F("queue_name") == dlq_name) & (F("status") == JobStatus.FAILED.value)
                ).all()
                for j in jobs:
                    updated = await q.replay_job(j, queue_name=target_queue)
                    if updated:
                        replayed.append(
                            {
                                "ulid": updated.ulid,
                                "queue_name": updated.queue_name,
                            }
                        )
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
            matching_jobs = await q.jobs.filter(combined).all()
            if not matching_jobs:
                return 0

            ulids = [j.ulid for j in matching_jobs]
            await q.attempts.filter(F("job_ulid").in_list(ulids)).delete_all()

            return await q.jobs.filter(combined).delete_all()
        finally:
            await q.close()

    if not confirm and not older_than:
        if human:
            click.echo("Use --confirm to purge all DLQ jobs, or --older-than to filter.", err=True)
        else:
            _echo_result(
                ok=False, error="Use --confirm to purge all DLQ jobs, or --older-than to filter"
            )
        sys.exit(1)

    result = _run(_purge())
    _echo_result(count=result, human=human)
