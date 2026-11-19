"""qler logs."""

from __future__ import annotations

import contextlib
import os
import re
import sys

import click

from qler.cli.group import cli
from qler.cli.helpers import (
    _echo_json,
    _get_queue,
    _run,
)

# ---------------------------------------------------------------------------
# qler logs
# ---------------------------------------------------------------------------


@cli.command("logs")
@click.argument("ulid")
@click.option("--db", required=True, envvar="QLER_DB", help="Database file path")
@click.option("--level", help="Filter by log level (e.g. ERROR, WARN)")
@click.option("--limit", default=100, type=int, help="Max entries to return")
@click.option("--json", "output_json", is_flag=True, help="Output JSON")
def logs(ulid: str, db: str, level: str | None, limit: int, output_json: bool) -> None:
    """Show log entries for a job (requires logler)."""
    try:
        from logler.db_source import db_to_jsonl
        from logler.investigate import search
    except ImportError:
        click.echo("logler is required: uv pip install 'qler[logler]'", err=True)
        sys.exit(1)

    from sqler import F as _F

    async def _logs():
        q = await _get_queue(db)
        try:
            found = await q.jobs.filter(_F("ulid") == ulid).first()
            return found
        finally:
            await q.close()

    found = _run(_logs())
    if found is None:
        if output_json:
            _echo_json({"error": f"Job {ulid} not found"})
        else:
            click.echo(f"Job {ulid} not found.", err=True)
        sys.exit(1)

    jsonl_path = db_to_jsonl(db)
    try:
        results = search(
            files=[jsonl_path],
            query=re.escape(found.ulid),
            level=level,
            limit=limit,
        )
    finally:
        with contextlib.suppress(OSError):
            os.unlink(jsonl_path)

    if output_json:
        _echo_json(results)
    else:
        entries = results.get("results", [])
        if not entries:
            click.echo("No log entries found.")
            return
        for r in entries:
            e = r.get("entry", {})
            ts = e.get("timestamp", "-")[:19]
            lvl = e.get("level", "?")
            msg = e.get("message", "")
            click.echo(f"{ts}  {lvl:<5}  {msg}")
        click.echo(f"\n{results.get('total_matches', 0)} entries found")
        if found.correlation_id:
            click.echo(f"Correlation ID: {found.correlation_id}")
            click.echo(f"Tip: logler llm search --db {db} --correlation {found.correlation_id}")
