"""qler archive, purge and doctor."""

from __future__ import annotations

import os
import sys
from typing import Any

import click

from qler._time import now_epoch
from qler.cli.group import cli
from qler.cli.helpers import (
    TERMINAL_STATUSES,
    _echo_json,
    _get_queue,
    _import_modules,
    _parse_since,
    _parse_since_to_seconds,
    _run,
)
from qler.enums import JobStatus

# ---------------------------------------------------------------------------
# qler archive
# ---------------------------------------------------------------------------

@cli.command()
@click.option("--db", required=True, envvar="QLER_DB", help="Database file path")
@click.option("--older-than", default="5m", help="Age threshold, e.g. '5m', '1h', '7d' (default: 5m)")
@click.option("--json", "output_json", is_flag=True, help="Output JSON")
def archive(db: str, older_than: str, output_json: bool) -> None:
    """Move terminal jobs to archive table."""
    seconds = _parse_since_to_seconds(older_than)

    async def _archive() -> int:
        q = await _get_queue(db)
        try:
            return await q.archive_jobs(older_than_seconds=seconds)
        finally:
            await q.close()

    count = _run(_archive())

    if output_json:
        _echo_json({"archived": count})
    else:
        click.echo(f"Archived {count} jobs.")


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
            statuses = [filter_status] if filter_status else list(TERMINAL_STATUSES)

            job_filter = F("status").in_list(statuses) & (F("created_at") <= threshold)
            matching_jobs = await q.jobs.filter(job_filter).all()

            if not matching_jobs:
                return 0

            # Delete attempts first (cascade)
            ulids = [j.ulid for j in matching_jobs]
            await q.attempts.filter(
                F("job_ulid").in_list(ulids)
            ).delete_all()

            # Delete jobs
            deleted = await q.jobs.filter(job_filter).delete_all()
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
                tables = [
                    row["name"]
                    for row in await q.db.execute_sql(
                        "SELECT name FROM sqlite_master WHERE type='table' "
                        "AND name IN ('qler_jobs', 'qler_job_attempts')"
                    )
                ]
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
                rows = await q.db.execute_sql("PRAGMA journal_mode")
                mode = rows[0]["journal_mode"] if rows else "unknown"
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
                expired = await q.jobs.filter(
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
                    running = await q.jobs.filter(
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
                stale = await q.jobs.filter(
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
                job_count = await q.jobs.count()
                attempt_count = await q.attempts.count()
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
