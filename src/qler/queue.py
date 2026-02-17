"""Queue — the central coordinator for enqueue, claim, complete, and fail operations."""

from __future__ import annotations

import json
import random
import traceback as tb_module
from typing import TYPE_CHECKING, Any, Optional

from sqler import AsyncSQLerDB, F

from qler._time import generate_ulid, now_epoch
from qler.enums import RETRYABLE_FAILURES, AttemptStatus, FailureKind, JobStatus
from qler.exceptions import (
    ConfigurationError,
    PayloadNotSerializableError,
    PayloadTooLargeError,
)
from qler.models.attempt import JobAttempt
from qler.models.job import Job

if TYPE_CHECKING:
    from qler.task import TaskWrapper


class Queue:
    """Async job queue backed by SQLite via sqler.

    Can be initialized with a file path (Queue owns the DB) or an existing
    AsyncSQLerDB instance (caller owns the DB).
    """

    def __init__(
        self,
        db: str | AsyncSQLerDB,
        *,
        default_lease_duration: int = 300,
        default_max_retries: int = 0,
        default_retry_delay: int = 60,
        max_payload_size: int = 1_000_000,
    ) -> None:
        if isinstance(db, str):
            self._db_path: Optional[str] = db
            self._db: Optional[AsyncSQLerDB] = None
            self._owns_db = True
        else:
            self._db_path = None
            self._db = db
            self._owns_db = False

        if default_retry_delay < 1:
            raise ConfigurationError(
                f"default_retry_delay must be >= 1, got {default_retry_delay}"
            )

        self.default_lease_duration = default_lease_duration
        self.default_max_retries = default_max_retries
        self.default_retry_delay = default_retry_delay
        self.max_payload_size = max_payload_size
        self._tasks: dict[str, TaskWrapper] = {}
        self._initialized = False

    @property
    def db(self) -> AsyncSQLerDB:
        """Return the bound database. Raises if not initialized."""
        if self._db is None:
            raise RuntimeError("Queue not initialized. Call init_db() first.")
        return self._db

    async def init_db(self) -> None:
        """Initialize the database schema. Lazy and idempotent."""
        if self._initialized:
            return

        if self._db is None:
            self._db = AsyncSQLerDB.on_disk(self._db_path)
            await self._db.connect()
            # Set SQLite PRAGMAs for performance
            for pragma in [
                "PRAGMA journal_mode=WAL",
                "PRAGMA busy_timeout=5000",
                "PRAGMA synchronous=NORMAL",
                "PRAGMA wal_autocheckpoint=1000",
            ]:
                cur = await self._db.adapter.execute(pragma)
                await cur.close()

        Job.set_db(self._db, "qler_jobs")
        JobAttempt.set_db(self._db, "qler_job_attempts")

        # Create tables with promoted columns + CHECK constraints.
        # We call the db methods directly instead of Model._ensure_schema()
        # because Pydantic's metaclass intercepts underscore-prefixed class
        # attribute access.
        await self._db._ensure_table_with_promoted(
            "qler_jobs", Job.__promoted__, Job.__checks__
        )
        await self._db._ensure_table_with_promoted(
            "qler_job_attempts", JobAttempt.__promoted__, JobAttempt.__checks__
        )

        await self._db._ensure_versioned_table("qler_jobs")
        await self._db._ensure_versioned_table("qler_job_attempts")

        await self._create_indexes()
        self._initialized = True

    async def _create_indexes(self) -> None:
        """Create all required indexes (idempotent)."""
        adapter = self._db.adapter
        indexes = [
            # 1. Claim: pick highest-priority pending job with earliest ETA
            (
                "CREATE INDEX IF NOT EXISTS idx_qler_jobs_claim "
                "ON qler_jobs (queue_name, priority DESC, eta, ulid) "
                "WHERE status = 'pending'"
            ),
            # 2. Lease expiry: find running jobs with expired leases
            (
                "CREATE INDEX IF NOT EXISTS idx_qler_jobs_lease "
                "ON qler_jobs (lease_expires_at) "
                "WHERE status = 'running'"
            ),
            # 3. Correlation: look up jobs by correlation ID
            (
                "CREATE INDEX IF NOT EXISTS idx_qler_jobs_correlation "
                "ON qler_jobs (json_extract(data, '$.correlation_id')) "
                "WHERE json_extract(data, '$.correlation_id') != ''"
            ),
            # 4. Attempt lookup: find attempts for a job
            "CREATE INDEX IF NOT EXISTS idx_qler_attempts_job ON qler_job_attempts (job_ulid)",
            # 5. Idempotency: deduplicate by idempotency key
            (
                "CREATE INDEX IF NOT EXISTS idx_qler_jobs_idempotency "
                "ON qler_jobs (json_extract(data, '$.idempotency_key')) "
                "WHERE json_extract(data, '$.idempotency_key') IS NOT NULL"
            ),
        ]
        for ddl in indexes:
            cur = await adapter.execute(ddl)
            await cur.close()
        await adapter.auto_commit()

    # ------------------------------------------------------------------
    # Enqueue
    # ------------------------------------------------------------------

    async def enqueue(
        self,
        task_path: str,
        args: tuple = (),
        kwargs: Optional[dict[str, Any]] = None,
        *,
        queue_name: str = "default",
        delay: Optional[int] = None,
        eta: Optional[int] = None,
        priority: int = 0,
        max_retries: Optional[int] = None,
        retry_delay: Optional[int] = None,
        lease_duration: Optional[int] = None,
        idempotency_key: Optional[str] = None,
        correlation_id: Optional[str] = None,
    ) -> Job:
        """Enqueue a new job. Returns the created Job instance."""
        await self._lazy_init()

        # Serialize payload
        payload = {"args": list(args), "kwargs": kwargs or {}}
        try:
            payload_json = json.dumps(payload)
        except (TypeError, ValueError, OverflowError) as exc:
            raise PayloadNotSerializableError(
                f"Payload is not JSON-serializable: {exc}"
            ) from exc

        if len(payload_json) > self.max_payload_size:
            raise PayloadTooLargeError(
                f"Payload size {len(payload_json)} exceeds max {self.max_payload_size}",
                size=len(payload_json),
                max_size=self.max_payload_size,
            )

        # Idempotency check: return existing non-cancelled job with same key
        if idempotency_key is not None:
            existing = await Job.query().filter(
                (F("idempotency_key") == idempotency_key)
                & (F("status") != JobStatus.CANCELLED.value)
            ).first()
            if existing is not None:
                return existing

        # Calculate ETA
        now = now_epoch()
        if eta is not None:
            job_eta = eta
        elif delay is not None:
            job_eta = now + delay
        else:
            job_eta = now

        job = Job(
            ulid=generate_ulid(),
            status=JobStatus.PENDING.value,
            queue_name=queue_name,
            priority=priority,
            eta=job_eta,
            task=task_path,
            payload_json=payload_json,
            max_retries=max_retries if max_retries is not None else self.default_max_retries,
            retry_delay=retry_delay if retry_delay is not None else self.default_retry_delay,
            lease_duration=lease_duration if lease_duration is not None else self.default_lease_duration,
            correlation_id=correlation_id or "",
            idempotency_key=idempotency_key,
            created_at=now,
            updated_at=now,
        )
        await job.save()
        return job

    # ------------------------------------------------------------------
    # Claim
    # ------------------------------------------------------------------

    async def claim_job(
        self,
        worker_id: str,
        queues: list[str],
    ) -> Optional[Job]:
        """Atomically claim the next eligible job. Returns Job or None."""
        await self._lazy_init()

        if not queues:
            raise ConfigurationError("queues list must not be empty")

        now_ts = now_epoch()
        attempt_id = generate_ulid()

        # Atomic claim via update_one
        job = await Job.query().filter(
            (F("status") == JobStatus.PENDING.value)
            & F("queue_name").in_list(queues)
            & (F("eta") <= now_ts)
        ).order_by("-priority", "eta", "ulid").update_one(
            status=JobStatus.RUNNING.value,
            worker_id=worker_id,
            attempts=F("attempts") + 1,
            lease_expires_at=now_ts + self.default_lease_duration,
            last_attempt_id=attempt_id,
            updated_at=now_ts,
        )

        if job is None:
            return None

        # Poison pill check: validate payload is parseable
        try:
            json.loads(job.payload_json)
        except (json.JSONDecodeError, TypeError):
            # Mark as permanently failed — bad payload
            await self._poison_pill(job, worker_id, attempt_id, now_ts)
            return None

        # Create the running attempt record
        attempt = JobAttempt(
            ulid=attempt_id,
            job_ulid=job.ulid,
            status=AttemptStatus.RUNNING.value,
            attempt_number=job.attempts,
            worker_id=worker_id,
            started_at=now_ts,
            lease_expires_at=now_ts + self.default_lease_duration,
        )
        await attempt.save()

        return job

    async def _poison_pill(
        self, job: Job, worker_id: str, attempt_id: str, now_ts: int
    ) -> None:
        """Handle a job with an unparseable payload."""
        await Job.query().filter(
            (F("ulid") == job.ulid) & (F("status") == JobStatus.RUNNING.value)
        ).update_one(
            status=JobStatus.FAILED.value,
            last_error="Payload is not valid JSON",
            last_failure_kind=FailureKind.PAYLOAD_INVALID.value,
            finished_at=now_ts,
            updated_at=now_ts,
            worker_id="",
            lease_expires_at=None,
        )

        attempt = JobAttempt(
            ulid=attempt_id,
            job_ulid=job.ulid,
            status=AttemptStatus.FAILED.value,
            attempt_number=job.attempts,
            worker_id=worker_id,
            started_at=now_ts,
            finished_at=now_ts,
            failure_kind=FailureKind.PAYLOAD_INVALID.value,
            error="Payload is not valid JSON",
        )
        await attempt.save()

    # ------------------------------------------------------------------
    # Complete
    # ------------------------------------------------------------------

    async def complete_job(
        self,
        job: Job,
        worker_id: str,
        result: Any = None,
    ) -> None:
        """Mark a job as completed.

        Uses atomic update_one() with ownership guard instead of
        refresh+save to avoid version mismatch after claim's update_one().
        """
        # Serialize result
        result_json: Optional[str] = None
        if result is not None:
            try:
                result_json = json.dumps(result)
            except (TypeError, ValueError, OverflowError) as exc:
                # Result not serializable — fail the job instead
                await self.fail_job(
                    job,
                    worker_id,
                    exc,
                    failure_kind=FailureKind.EXCEPTION,
                )
                return

        now = now_epoch()
        updated = await Job.query().filter(
            (F("ulid") == job.ulid)
            & (F("status") == JobStatus.RUNNING.value)
            & (F("worker_id") == worker_id)
        ).update_one(
            status=JobStatus.COMPLETED.value,
            result_json=result_json,
            finished_at=now,
            updated_at=now,
            worker_id="",
            lease_expires_at=None,
        )

        if updated is None:
            return  # Lost ownership

        # Terminalize attempt (best effort)
        await self._terminalize_attempt(
            job.last_attempt_id, AttemptStatus.COMPLETED.value, now
        )

    # ------------------------------------------------------------------
    # Fail
    # ------------------------------------------------------------------

    async def fail_job(
        self,
        job: Job,
        worker_id: str,
        exc: Optional[BaseException] = None,
        failure_kind: FailureKind = FailureKind.EXCEPTION,
    ) -> None:
        """Mark a job as failed, with optional retry.

        Uses atomic update_one() with ownership guard.
        """
        now = now_epoch()
        can_retry = (
            failure_kind in RETRYABLE_FAILURES
            and job.retry_count < job.max_retries
        )

        error_msg = str(exc) if exc else None
        error_tb = tb_module.format_exception(exc) if exc else None
        error_tb_str = "".join(error_tb) if error_tb else None

        update_fields: dict[str, Any] = {
            "last_error": error_msg,
            "last_failure_kind": failure_kind.value,
            "updated_at": now,
            "worker_id": "",
            "lease_expires_at": None,
        }

        if can_retry:
            update_fields["status"] = JobStatus.PENDING.value
            update_fields["eta"] = _calculate_retry_eta(job)
            update_fields["retry_count"] = job.retry_count + 1
        else:
            update_fields["status"] = JobStatus.FAILED.value
            update_fields["finished_at"] = now

        updated = await Job.query().filter(
            (F("ulid") == job.ulid)
            & (F("status") == JobStatus.RUNNING.value)
            & (F("worker_id") == worker_id)
        ).update_one(**update_fields)

        if updated is None:
            return  # Lost ownership

        # Terminalize attempt (best effort)
        await self._terminalize_attempt(
            job.last_attempt_id,
            AttemptStatus.FAILED.value,
            now,
            error=error_msg,
            traceback=error_tb_str,
            failure_kind=failure_kind.value,
        )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    async def _terminalize_attempt(
        self,
        attempt_id: Optional[str],
        status: str,
        finished_at: int,
        *,
        error: Optional[str] = None,
        traceback: Optional[str] = None,
        failure_kind: Optional[str] = None,
    ) -> None:
        """Update the attempt record to its terminal state via atomic update_one."""
        if attempt_id is None:
            return

        update_fields: dict[str, Any] = {
            "status": status,
            "finished_at": finished_at,
        }
        if error is not None:
            update_fields["error"] = error
        if traceback is not None:
            update_fields["traceback"] = traceback
        if failure_kind is not None:
            update_fields["failure_kind"] = failure_kind

        await JobAttempt.query().filter(
            (F("ulid") == attempt_id) & (F("status") == AttemptStatus.RUNNING.value)
        ).update_one(**update_fields)

    async def _lazy_init(self) -> None:
        """Ensure init_db has been called."""
        if not self._initialized:
            await self.init_db()

    async def close(self) -> None:
        """Close the database connection if we own it."""
        if self._owns_db and self._db is not None:
            await self._db.close()
            self._db = None
            self._initialized = False


def _calculate_retry_eta(job: Job) -> int:
    """Calculate the next retry ETA with exponential backoff + jitter."""
    base_delay = job.retry_delay
    delay = base_delay * (2 ** job.retry_count)
    # Add 10% jitter
    jitter = delay * 0.1 * random.random()
    return now_epoch() + int(delay + jitter)
