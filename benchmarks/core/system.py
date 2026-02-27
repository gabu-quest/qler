"""System information collection for benchmark reports."""

from __future__ import annotations

import platform
import sqlite3
import sys
from dataclasses import asdict, dataclass
from datetime import datetime, timezone


@dataclass(frozen=True, slots=True)
class SystemInfo:
    """System information captured at benchmark run time."""

    python_version: str
    qler_version: str
    sqlite_version: str
    platform_system: str
    platform_machine: str
    platform_release: str
    cpu_count: int
    timestamp: str

    @classmethod
    def collect(cls) -> SystemInfo:
        from importlib.metadata import version as pkg_version

        try:
            version = pkg_version("qler")
        except Exception:
            version = "dev"

        import os

        return cls(
            python_version=sys.version.split()[0],
            qler_version=version,
            sqlite_version=sqlite3.sqlite_version,
            platform_system=platform.system(),
            platform_machine=platform.machine(),
            platform_release=platform.release(),
            cpu_count=os.cpu_count() or 1,
            timestamp=datetime.now(timezone.utc).isoformat(),
        )

    def to_dict(self) -> dict:
        return asdict(self)

    def summary_line(self) -> str:
        return (
            f"Python {self.python_version} | "
            f"qler {self.qler_version} | "
            f"SQLite {self.sqlite_version} | "
            f"{self.platform_system} {self.platform_machine} ({self.cpu_count} cores)"
        )
