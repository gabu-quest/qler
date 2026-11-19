"""JobAttempt model — audit record for each execution attempt."""

from __future__ import annotations

from typing import ClassVar

from sqler import NO_REBASE_CONFIG, AsyncSQLerSafeModel, RebaseConfig


class JobAttempt(AsyncSQLerSafeModel):
    """Tracks a single execution attempt of a job."""

    __promoted__: ClassVar[dict[str, str]] = {
        "ulid": "TEXT NOT NULL",
        "job_ulid": "TEXT NOT NULL",
        "status": "TEXT NOT NULL DEFAULT 'running'",
    }
    __checks__: ClassVar[dict[str, str]] = {
        "status": "status IN ('running','completed','failed','lease_expired')",
    }
    _rebase_config: ClassVar[RebaseConfig] = NO_REBASE_CONFIG

    ulid: str = ""
    job_ulid: str = ""
    status: str = "running"
    attempt_number: int = 0
    worker_id: str = ""
    started_at: int = 0
    finished_at: int | None = None
    failure_kind: str | None = None
    error: str | None = None
    traceback: str | None = None
    lease_expires_at: int | None = None
