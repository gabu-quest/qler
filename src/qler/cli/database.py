"""qler init and qler backup."""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any

import click

from qler.cli.group import cli
from qler.cli.helpers import (
    _echo_json,
    _get_queue,
    _run,
)
from qler.queue import Queue

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
