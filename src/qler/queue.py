"""Queue — the central coordinator for enqueue, claim, complete, and fail operations."""

from __future__ import annotations

import asyncio
import json
import random
import traceback as tb_module
from typing import TYPE_CHECKING, Any, Optional

from sqler import AsyncSQLerDB, F

from qler._context import _current_job
from qler._time import generate_ulid, now_epoch
from qler.enums import RETRYABLE_FAILURES, AttemptStatus, FailureKind, JobStatus
from qler.exceptions import (
    ConfigurationError,
    DependencyError,
    PayloadNotSerializableError,
    PayloadTooLargeError,
    TaskNotRegisteredError,
)
from qler.models.attempt import JobAttempt
from qler.models.bucket import RateLimitBucket
from qler.models.job import Job
from qler.rate_limit import RateSpec, parse_rate, try_acquire

if TYPE_CHECKING:
    from qler.cron import CronWrapper
    from qler.task import TaskWrapper

_MAX_TRACEBACK_LENGTH = 8_192
_MAX_ERROR_LENGTH = 1_024
_MAX_RETRY_DELAY = 86_400  # 24 hours


class Queue:
    """Async job queue backed by SQLite via sqler.

    Can be initialized with a file path (Queue owns the DB) or an existing
    AsyncSQLerDB instance (caller owns the DB).
    """

    def __init__(
        self,
        db: str | AsyncSQLerDB,
        *,
        immediate: bool = False,
        default_lease_duration: int = 300,
        default_max_retries: int = 0,
        default_retry_delay: int = 60,
        max_payload_size: int = 1_000_000,
        rate_limits: Optional[dict[str, str]] = None,
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

        self.immediate = immediate
        self.default_lease_duration = default_lease_duration
        self.default_max_retries = default_max_retries
        self.default_retry_delay = default_retry_delay
        self.max_payload_size = max_payload_size
        self._tasks: dict[str, TaskWrapper] = {}
        self._cron_tasks: dict[str, CronWrapper] = {}
        self._rate_limits: dict[str, RateSpec] = {}
        if rate_limits:
            for queue_name, spec in rate_limits.items():
                self._rate_limits[queue_name] = parse_rate(spec)
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
        RateLimitBucket.set_db(self._db, "qler_rate_limit_buckets")

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
        await self._db._ensure_table_with_promoted(
            "qler_rate_limit_buckets",
            RateLimitBucket.__promoted__,
            RateLimitBucket.__checks__,
        )

        await self._db._ensure_versioned_table("qler_jobs")
        await self._db._ensure_versioned_table("qler_job_attempts")
        await self._db._ensure_versioned_table("qler_rate_limit_buckets")

        # Schema migration: add pending_dep_count column (idempotent)
        try:
            cur = await self._db.adapter.execute(
                "ALTER TABLE qler_jobs ADD COLUMN pending_dep_count INTEGER NOT NULL DEFAULT 0"
            )
            await cur.close()
            await self._db.adapter.auto_commit()
        except Exception:
            pass  # Column already exists

        await self._create_indexes()
        self._initialized = True

    async def _create_indexes(self) -> None:
        """Create all required indexes (idempotent)."""
        adapter = self._db.adapter
        indexes = [
            # 1. Claim: pick highest-priority pending job with earliest ETA and no pending deps
            "DROP INDEX IF EXISTS idx_qler_jobs_claim",
            (
                "CREATE INDEX IF NOT EXISTS idx_qler_jobs_claim "
                "ON qler_jobs (queue_name, priority DESC, eta, ulid) "
                "WHERE status = 'pending' AND pending_dep_count = 0"
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
        depends_on: Optional[list[str]] = None,
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

        # Validate dependencies
        dep_list: list[str] = []
        pending_dep_count = 0
        if depends_on:
            dep_list = list(dict.fromkeys(depends_on))  # deduplicate, preserve order
            dep_jobs = await Job.query().filter(
                F("ulid").in_list(dep_list)
            ).all()
            found_ulids = {j.ulid for j in dep_jobs}
            for dep_ulid in dep_list:
                if dep_ulid not in found_ulids:
                    raise DependencyError(
                        f"Dependency job {dep_ulid} not found",
                        dependency_ulid=dep_ulid,
                    )
            for dep_job in dep_jobs:
                if dep_job.status == JobStatus.FAILED.value:
                    raise DependencyError(
                        f"Dependency job {dep_job.ulid} is failed",
                        dependency_ulid=dep_job.ulid,
                    )
                if dep_job.status == JobStatus.CANCELLED.value:
                    raise DependencyError(
                        f"Dependency job {dep_job.ulid} is cancelled",
                        dependency_ulid=dep_job.ulid,
                    )
            pending_dep_count = sum(
                1 for j in dep_jobs if j.status != JobStatus.COMPLETED.value
            )

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
            dependencies=dep_list,
            pending_dep_count=pending_dep_count,
            created_at=now,
            updated_at=now,
        )
        await job.save()

        if self.immediate:
            return await self._execute_immediate(job)

        return job

    # ------------------------------------------------------------------
    # Immediate execution
    # ------------------------------------------------------------------

    async def _execute_immediate(self, job: Job) -> Job:
        """Execute a job synchronously within enqueue(). Used by immediate mode.

        Transitions the job PENDING → RUNNING → COMPLETED/FAILED, creates an
        attempt record, and returns the updated Job.
        """
        worker_id = "__immediate__"
        now = now_epoch()
        attempt_id = generate_ulid()

        # Transition PENDING → RUNNING
        claimed = await Job.query().filter(
            (F("ulid") == job.ulid) & (F("status") == JobStatus.PENDING.value)
        ).update_one(
            status=JobStatus.RUNNING.value,
            worker_id=worker_id,
            attempts=F("attempts") + 1,
            last_attempt_id=attempt_id,
            updated_at=now,
        )
        if claimed is None:
            # Job was already claimed (e.g. idempotency returned existing)
            return job
        job = claimed

        # Create attempt record
        attempt = JobAttempt(
            ulid=attempt_id,
            job_ulid=job.ulid,
            status=AttemptStatus.RUNNING.value,
            attempt_number=job.attempts,
            worker_id=worker_id,
            started_at=now,
        )
        await attempt.save()

        # Resolve task
        task_wrapper = self._tasks.get(job.task)
        if task_wrapper is None:
            # Fail the job cleanly instead of leaving it RUNNING
            await self.fail_job(
                job,
                worker_id,
                RuntimeError(
                    f"Task '{job.task}' is not registered on this queue. "
                    "Register it with @task(queue) before enqueuing."
                ),
                failure_kind=FailureKind.TASK_NOT_FOUND,
            )
            updated = await Job.query().filter(F("ulid") == job.ulid).first()
            raise TaskNotRegisteredError(
                f"Task '{job.task}' is not registered on this queue. "
                "Register it with @task(queue) before enqueuing.",
                task_path=job.task,
            )

        # Parse payload and execute
        payload = json.loads(job.payload_json)
        args = payload.get("args", [])
        kwargs = payload.get("kwargs", {})

        task_exc: BaseException | None = None
        result: Any = None
        token = _current_job.set(job)
        try:
            if task_wrapper.sync:
                result = await asyncio.to_thread(task_wrapper.fn, *args, **kwargs)
            else:
                result = await task_wrapper.fn(*args, **kwargs)
        except asyncio.CancelledError:
            await self.fail_job(job, worker_id, failure_kind=FailureKind.CANCELLED)
            raise
        except Exception as exc:
            task_exc = exc
        finally:
            _current_job.reset(token)

        if task_exc is not None:
            try:
                await self.fail_job(
                    job, worker_id, task_exc, failure_kind=FailureKind.EXCEPTION
                )
            except Exception:
                # Best-effort: terminalize attempt directly if fail_job raises
                await self._terminalize_attempt(
                    attempt_id, AttemptStatus.FAILED.value, now_epoch(), error="Internal error"
                )
        else:
            await self.complete_job(job, worker_id, result=result)

        updated = await Job.query().filter(F("ulid") == job.ulid).first()
        return updated if updated is not None else job

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
            & (F("pending_dep_count") == 0)
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

        # Rate limit checks (queue-level, then task-level)
        if not self.immediate:
            rate_limited = await self._check_rate_limits(job, worker_id, now_ts)
            if rate_limited:
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

            if len(result_json) > self.max_payload_size:
                await self.fail_job(
                    job,
                    worker_id,
                    PayloadTooLargeError(
                        f"Result size {len(result_json)} exceeds max {self.max_payload_size}",
                        size=len(result_json),
                        max_size=self.max_payload_size,
                    ),
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

        # Resolve dependencies: unblock dependent jobs
        await self._resolve_dependencies(job.ulid)

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

        error_msg = str(exc)[:_MAX_ERROR_LENGTH] if exc else None
        error_tb = tb_module.format_exception(exc) if exc else None
        error_tb_str = "".join(error_tb)[-_MAX_TRACEBACK_LENGTH:] if error_tb else None

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

        # Cascade-cancel dependents on terminal failure
        if not can_retry:
            await self._cascade_cancel_dependents(job.ulid)

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
    # Lease Recovery
    # ------------------------------------------------------------------

    async def recover_expired_leases(self, *, max_per_tick: int = 100) -> int:
        """Recover jobs with expired leases back to PENDING.

        Atomically finds RUNNING jobs whose lease has expired and resets them.
        Lease expiry does NOT increment retry_count — it's infrastructure failure,
        not a task failure.

        Returns:
            Number of jobs recovered.
        """
        await self._lazy_init()
        now = now_epoch()
        recovered = 0

        for _ in range(max_per_tick):
            job = await Job.query().filter(
                (F("status") == JobStatus.RUNNING.value)
                & (F("lease_expires_at") <= now)
            ).order_by("lease_expires_at").update_one(
                status=JobStatus.PENDING.value,
                worker_id="",
                lease_expires_at=None,
                updated_at=now,
            )
            if job is None:
                break

            # Terminalize the attempt as LEASE_EXPIRED
            await self._terminalize_attempt(
                job.last_attempt_id,
                AttemptStatus.LEASE_EXPIRED.value,
                now,
                failure_kind=FailureKind.LEASE_EXPIRED.value,
                error="Lease expired",
            )
            recovered += 1

        return recovered

    # ------------------------------------------------------------------
    # Dependencies
    # ------------------------------------------------------------------

    async def _resolve_dependencies(self, completed_ulid: str) -> None:
        """Decrement pending_dep_count for jobs depending on the completed job."""
        # Find PENDING jobs that still have pending deps
        candidates = await Job.query().filter(
            (F("status") == JobStatus.PENDING.value)
            & (F("pending_dep_count") > 0)
        ).all()

        for candidate in candidates:
            if completed_ulid in candidate.dependencies:
                await Job.query().filter(
                    (F("ulid") == candidate.ulid)
                    & (F("status") == JobStatus.PENDING.value)
                    & (F("pending_dep_count") > 0)
                ).update_one(
                    pending_dep_count=F("pending_dep_count") - 1,
                    updated_at=now_epoch(),
                )

    async def _cascade_cancel_dependents(self, failed_ulid: str) -> None:
        """Cancel PENDING jobs that depend on a failed/cancelled job, recursively."""
        candidates = await Job.query().filter(
            F("status") == JobStatus.PENDING.value
        ).all()

        now = now_epoch()
        cancelled_ulids: list[str] = []
        for candidate in candidates:
            if failed_ulid in candidate.dependencies:
                updated = await Job.query().filter(
                    (F("ulid") == candidate.ulid)
                    & (F("status") == JobStatus.PENDING.value)
                ).update_one(
                    status=JobStatus.CANCELLED.value,
                    last_error=f"Dependency {failed_ulid} failed/cancelled",
                    finished_at=now,
                    updated_at=now,
                )
                if updated is not None:
                    cancelled_ulids.append(candidate.ulid)

        # Recurse for transitive dependents
        for ulid in cancelled_ulids:
            await self._cascade_cancel_dependents(ulid)

    async def cancel_job(self, job: Job) -> bool:
        """Cancel a job and cascade-cancel its dependents."""
        ok = await job.cancel()
        if ok and job.status == JobStatus.CANCELLED.value:
            await self._cascade_cancel_dependents(job.ulid)
        return ok

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    async def _check_rate_limits(
        self, job: Job, worker_id: str, now_ts: int
    ) -> bool:
        """Check rate limits for a claimed job. Returns True if rate-limited.

        Checks queue-level rate limit first, then task-level. If either limit
        is exceeded, requeues the job with a delayed ETA.
        """
        # Queue-level rate limit
        queue_rate = self._rate_limits.get(job.queue_name)
        if queue_rate is not None:
            allowed = await try_acquire(f"queue:{job.queue_name}", queue_rate)
            if not allowed:
                await self._requeue_rate_limited(job, worker_id, queue_rate, now_ts)
                return True

        # Task-level rate limit
        task_wrapper = self._tasks.get(job.task)
        if task_wrapper is not None and task_wrapper.rate_spec is not None:
            allowed = await try_acquire(f"task:{job.task}", task_wrapper.rate_spec)
            if not allowed:
                await self._requeue_rate_limited(
                    job, worker_id, task_wrapper.rate_spec, now_ts
                )
                return True

        return False

    async def _requeue_rate_limited(
        self, job: Job, worker_id: str, rate: RateSpec, now_ts: int
    ) -> None:
        """Requeue a rate-limited job back to PENDING with a delayed ETA."""
        delay = max(1, int(1.0 / rate.refill_rate)) if rate.refill_rate > 0 else 1
        await Job.query().filter(
            (F("ulid") == job.ulid)
            & (F("status") == JobStatus.RUNNING.value)
            & (F("worker_id") == worker_id)
        ).update_one(
            status=JobStatus.PENDING.value,
            eta=now_ts + delay,
            worker_id="",
            lease_expires_at=None,
            # Don't increment retry_count — rate limiting is not a failure
            attempts=F("attempts") - 1,  # Undo the increment from claim
            updated_at=now_ts,
        )

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
    delay = min(base_delay * (2 ** job.retry_count), _MAX_RETRY_DELAY)
    # Add 10% jitter (non-cryptographic, intentional)
    jitter = delay * 0.1 * random.random()
    return now_epoch() + int(delay + jitter)
