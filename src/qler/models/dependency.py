"""JobDependency model — the reverse map "which jobs wait on this one"."""

from __future__ import annotations

from typing import ClassVar

from pydantic import ConfigDict
from sqler import AsyncSQLerModel


class JobDependency(AsyncSQLerModel):
    """One edge: the job ``child_ulid`` waits on the job ``parent_ulid``.

    The edges live in the table ``qler_job_dependencies``, one row per (parent, child)
    pair (a UNIQUE index over the two columns). A child's own list of parents stays in
    ``Job.dependencies``; this table answers the other direction, read when a parent
    completes (its children count down) or fails (its children are cancelled).
    """

    __tablename__: ClassVar[str] = "qler_job_dependencies"
    __promoted__: ClassVar[dict[str, str]] = {
        "parent_ulid": "TEXT NOT NULL",
        "child_ulid": "TEXT NOT NULL",
    }

    parent_ulid: str
    child_ulid: str

    model_config = ConfigDict(extra="ignore", frozen=False)
