"""qler CLI — human-first commands with --json flag."""

from __future__ import annotations

from qler.cli import (  # noqa: F401  (importing a module registers its commands)
    control,
    database,
    deadletter,
    logs,
    maintenance,
    reports,
    schedules,
    workers,
)
from qler.cli.group import cli

__all__ = ["cli"]
