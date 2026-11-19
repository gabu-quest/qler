"""Job model — primary queue item with state machine and lifecycle methods."""

from __future__ import annotations

import asyncio
import json
import time
from typing import Any, ClassVar, NamedTuple

from pydantic import Field
from sqler import NO_REBASE_CONFIG, AsyncSQLerSafeModel, F, RebaseConfig
from sqler.exceptions import UniqueConstraintError

from qler._notify import clear as _notify_clear
from qler._notify import fire as _notify_fire
from qler._notify import register as _notify_register
from qler._time import now_epoch
from qler.enums import ACTIVE_STATUSES, JobStatus
from qler.exceptions import JobCancelledError, JobFailedError, UniqueKeyConflictError

# The second table of Job rows: archived jobs, read through Queue.archive.
ARCHIVE_TABLE = "qler_jobs_archive"


class Job(AsyncSQLerSafeModel):
    """A queued job with optimistic locking and promoted hot columns."""

    __tablename__: ClassVar[str] = "qler_jobs"
    __promoted__: ClassVar[dict[str, str]] = {
        "ulid": "TEXT NOT NULL",
        "status": "TEXT NOT NULL DEFAULT 'pending'",
        "queue_name": "TEXT NOT NULL DEFAULT 'default'",
        "priority": "INTEGER NOT NULL DEFAULT 0",
        "eta": "INTEGER NOT NULL DEFAULT 0",
        "lease_expires_at": "INTEGER",
        "pending_dep_count": "INTEGER NOT NULL DEFAULT 0",
    }
    __checks__: ClassVar[dict[str, str]] = {
        "status": "status IN ('pending','running','completed','failed','cancelled')",
    }
    _rebase_config: ClassVar[RebaseConfig] = NO_REBASE_CONFIG

    # Promoted columns (also real columns in SQLite)
    ulid: str = ""
    status: str = JobStatus.PENDING.value
    queue_name: str = "default"
    priority: int = 0
    eta: int = 0
    lease_expires_at: int | None = None
    pending_dep_count: int = 0

    # JSON fields
    task: str = ""
    worker_id: str = ""
    lease_duration: int = 300
    payload_json: str = "{}"
    result_json: str | None = None
    last_error: str | None = None
    last_failure_kind: str | None = None
    attempts: int = 0
    retry_count: int = 0
    max_retries: int = 0
    retry_delay: int = 60
    last_attempt_id: str | None = None
    correlation_id: str = ""
    idempotency_key: str | None = None
    unique_key: str | None = None
    timeout: int | None = None
    progress: int | None = None
    progress_message: str = ""
    cancel_requested: bool = False
    original_queue: str = ""
    dependencies: list[str] = Field(default_factory=list)
    created_at: int = 0
    updated_at: int = 0
    finished_at: int | None = None

    @property
    def result(self) -> Any:
        """Parse and return the result JSON, or None if no result."""
        if self.result_json is None:
            return None
        return json.loads(self.result_json)

    @property
    def payload(self) -> dict:
        """Parse and return the payload JSON."""
        return json.loads(self.payload_json)

    async def cancel(self) -> bool:
        """Cancel a job. PENDING jobs are cancelled immediately; RUNNING jobs
        get a cooperative cancellation request.

        Returns True if cancelled or cancellation was requested, False otherwise.
        """
        # Try immediate cancellation (PENDING → CANCELLED)
        now = now_epoch()
        updated = (
            await Job.query()
            .filter((F("ulid") == self.ulid) & (F("status") == JobStatus.PENDING.value))
            .update_one(
                status=JobStatus.CANCELLED.value,
                finished_at=now,
                updated_at=now,
            )
        )
        if updated is not None:
            self._sync_from(updated)
            _notify_fire(self.ulid)
            return True
        # If RUNNING, request cooperative cancellation instead
        return await self.request_cancel()

    async def request_cancel(self) -> bool:
        """Request cancellation of a RUNNING job. Returns True if request was recorded."""
        updated = (
            await Job.query()
            .filter((F("ulid") == self.ulid) & (F("status") == JobStatus.RUNNING.value))
            .update_one(cancel_requested=True, updated_at=now_epoch())
        )
        if updated is None:
            return False
        self._sync_from(updated)
        return True

    async def wait(
        self,
        timeout: float | None = None,
        poll_interval: float = 0.5,
        max_interval: float = 5.0,
        backoff: float = 1.5,
    ) -> Job:
        """Wait until the job reaches a terminal state.

        Uses an in-process asyncio.Event for instant wakeup when the Worker
        completes/fails/cancels the job in the same process. Falls back to
        DB polling (with exponential backoff) for cross-process completion
        or if the Event times out.

        Returns:
            self on COMPLETED.

        Raises:
            JobFailedError: If the job enters FAILED state.
            JobCancelledError: If the job enters CANCELLED state.
            TimeoutError: If timeout is reached before a terminal state.
        """
        event = _notify_register(self.ulid)
        try:
            start = time.monotonic()
            interval = poll_interval
            while True:
                await self.refresh()
                if self.status == JobStatus.COMPLETED.value:
                    return self
                if self.status == JobStatus.FAILED.value:
                    raise JobFailedError(
                        f"Job {self.ulid} failed: {self.last_error}",
                        ulid=self.ulid,
                        failure_kind=self.last_failure_kind,
                    )
                if self.status == JobStatus.CANCELLED.value:
                    raise JobCancelledError(
                        f"Job {self.ulid} was cancelled",
                        ulid=self.ulid,
                    )
                if timeout is not None and time.monotonic() - start >= timeout:
                    raise TimeoutError(f"Job {self.ulid} did not complete within {timeout}s")
                # Wait for Event (instant wakeup) or fall back to poll
                wait_duration = min(interval, max_interval)
                if timeout is not None:
                    remaining = timeout - (time.monotonic() - start)
                    if remaining <= 0:
                        raise TimeoutError(f"Job {self.ulid} did not complete within {timeout}s")
                    wait_duration = min(wait_duration, remaining)
                try:
                    await asyncio.wait_for(event.wait(), timeout=wait_duration)
                except TimeoutError:
                    pass  # Event didn't fire — poll DB on next iteration
                else:
                    event.clear()  # Reset for next iteration's refresh
                interval *= backoff
        finally:
            _notify_clear(self.ulid)

    async def wait_for_dependencies(
        self,
        timeout: float | None = None,
        poll_interval: float = 0.5,
        max_interval: float = 5.0,
        backoff: float = 1.5,
    ) -> None:
        """Poll until all dependencies reach a terminal state.

        Returns immediately if there are no dependencies.

        Raises:
            JobFailedError: If any dependency enters FAILED state.
            JobCancelledError: If any dependency enters CANCELLED state.
            TimeoutError: If timeout is reached before all deps complete.
        """
        if not self.dependencies:
            return

        start = time.monotonic()
        interval = poll_interval
        while True:
            deps = await Job.query().filter(F("ulid").in_list(self.dependencies)).all()

            # Check for missing deps (deleted)
            found_ulids = {d.ulid for d in deps}
            for dep_ulid in self.dependencies:
                if dep_ulid not in found_ulids:
                    raise JobFailedError(
                        f"Dependency {dep_ulid} not found",
                        ulid=dep_ulid,
                    )

            all_complete = True
            for dep in deps:
                if dep.status == JobStatus.FAILED.value:
                    raise JobFailedError(
                        f"Dependency {dep.ulid} failed: {dep.last_error}",
                        ulid=dep.ulid,
                        failure_kind=dep.last_failure_kind,
                    )
                if dep.status == JobStatus.CANCELLED.value:
                    raise JobCancelledError(
                        f"Dependency {dep.ulid} was cancelled",
                        ulid=dep.ulid,
                    )
                if dep.status != JobStatus.COMPLETED.value:
                    all_complete = False

            if all_complete:
                return

            if timeout is not None and time.monotonic() - start >= timeout:
                raise TimeoutError(f"Dependencies did not complete within {timeout}s")
            await asyncio.sleep(min(interval, max_interval))
            interval *= backoff

    async def retry(self) -> bool:
        """Reset a FAILED job back to PENDING. Returns True if reset, False otherwise.

        A dead-lettered job also leaves the dead-letter queue: it goes back to
        the queue it came from (``original_queue``). A job with no recorded
        original queue keeps its ``queue_name``.

        One atomic update_one(), guarded on status and on the original_queue
        that was read, so the reset and the move cannot be separated.

        Raises:
            UniqueKeyConflictError: another job with this job's ``unique_key`` is
                pending or running; the job stays FAILED.
        """
        current = await Job.query().filter(F("ulid") == self.ulid).first()
        if current is None:
            return False
        now = now_epoch()
        try:
            updated = (
                await Job.query()
                .filter(
                    (F("ulid") == self.ulid)
                    & (F("status") == JobStatus.FAILED.value)
                    & (F("original_queue") == current.original_queue)
                )
                .update_one(**failed_to_pending_fields(now, target_queue=current.original_queue))
            )
        except UniqueConstraintError as err:
            raise await unique_key_conflict(current, err) from err
        if updated is None:
            return False
        self._sync_from(updated)
        return True

    async def renew_lease(self, duration: int | None = None) -> bool:
        """Extend this job's lease atomically.

        Uses update_one() with ownership guard (ulid + status=RUNNING + worker_id)
        to ensure only the current owner can renew.

        Args:
            duration: Lease extension in seconds. Defaults to self.lease_duration.

        Returns:
            True if renewed, False if ownership was lost.
        """
        lease_seconds = duration if duration is not None else self.lease_duration
        now = now_epoch()
        updated = (
            await Job.query()
            .filter(
                (F("ulid") == self.ulid)
                & (F("status") == JobStatus.RUNNING.value)
                & (F("worker_id") == self.worker_id)
            )
            .update_one(
                lease_expires_at=now + lease_seconds,
                updated_at=now,
            )
        )
        if updated is None:
            return False
        self._sync_from(updated)
        return True

    def _sync_from(self, other: Job) -> None:
        """Update this instance's fields from another Job instance."""
        for fname in self.__class__.model_fields:
            setattr(self, fname, getattr(other, fname))
        self._id = other._id
        self._version = other._version


# The UNIQUE index over the unique key of pending and running jobs, made from
# KEY_INDEXES by qler._schema._create_key_index.
UNIQUE_KEY_INDEX = "idx_qler_jobs_unique_uq"


class KeyIndex(NamedTuple):
    """The UNIQUE partial index over one job key, and the non-unique one it replaces."""

    field: str
    label: str  # how the error names the key
    scope: str  # how the error names the jobs the index covers
    name: str
    where: str
    active: Any  # the same set of jobs as ``where``, as a filter
    legacy_name: str
    legacy_where: str
    # A plain index for the look-up, when the UNIQUE one cannot serve it: SQLite does not
    # take a partial index whose WHERE has ``status IN (...)`` for a query that binds the
    # statuses as parameters, so the look-up needs an index whose WHERE its ``key = ?`` implies.
    lookup_name: str | None = None
    lookup_where: str | None = None


_UNIQUE_KEY_WHERE = (
    "json_extract(data, '$.unique_key') IS NOT NULL AND status IN ('pending', 'running')"
)

KEY_INDEXES = (
    # An idempotency key names one job that is not cancelled.
    KeyIndex(
        field="idempotency_key",
        label="idempotency key",
        scope="non-cancelled",
        name="idx_qler_jobs_idempotency_uq",
        where=("json_extract(data, '$.idempotency_key') IS NOT NULL AND status != 'cancelled'"),
        active=F("status") != JobStatus.CANCELLED.value,
        legacy_name="idx_qler_jobs_idempotency",
        legacy_where="json_extract(data, '$.idempotency_key') IS NOT NULL",
    ),
    # A unique key names one job that is pending or running.
    KeyIndex(
        field="unique_key",
        label="unique key",
        scope="pending or running",
        name=UNIQUE_KEY_INDEX,
        where=_UNIQUE_KEY_WHERE,
        active=F("status").in_list(list(ACTIVE_STATUSES)),
        legacy_name="idx_qler_jobs_unique",
        legacy_where=_UNIQUE_KEY_WHERE,
        lookup_name="idx_qler_jobs_unique_key",
        lookup_where="json_extract(data, '$.unique_key') IS NOT NULL",
    ),
)


def key_active(field: str) -> Any:
    """The filter for the jobs that hold the ``field`` key, from its ``KEY_INDEXES`` entry."""
    for spec in KEY_INDEXES:
        if spec.field == field:
            return spec.active
    raise KeyError(field)


async def unique_key_conflict(job: Job, err: UniqueConstraintError) -> UniqueKeyConflictError:
    """The error for a move of ``job`` back to pending that the unique-key index refused.

    Shared by ``Job.retry`` and ``Queue.replay_job``. Any other refusal is not this
    one and goes on as it came.
    """
    if err.index != UNIQUE_KEY_INDEX:
        raise err
    active = (
        await Job.query()
        .filter((F("unique_key") == job.unique_key) & key_active("unique_key"))
        .first()
    )
    active_ulid = active.ulid if active is not None else None
    return UniqueKeyConflictError(
        f"Job {job.ulid} stays failed: job {active_ulid} with unique key "
        f"{job.unique_key!r} is pending or running",
        ulid=job.ulid,
        unique_key=job.unique_key,
        active_ulid=active_ulid,
    )


def failed_to_pending_fields(now: int, *, target_queue: str | None) -> dict:
    """Row fields for a FAILED job leaving the dead-letter queue as PENDING.

    The one rule shared by ``Job.retry`` and ``Queue.replay_job``. With a
    ``target_queue`` the job moves there and ``original_queue`` is cleared;
    without one the queue fields are left alone.
    """
    fields: dict = {
        "status": JobStatus.PENDING.value,
        "retry_count": 0,
        "finished_at": None,
        "eta": now,
        "updated_at": now,
        "progress": None,
        "progress_message": "",
    }
    if target_queue:
        fields["queue_name"] = target_queue
        fields["original_queue"] = ""
    return fields
