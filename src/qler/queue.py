"""Queue — the central coordinator for enqueue, claim, complete, and fail operations."""

from __future__ import annotations

import asyncio
import json
import random
import traceback as tb_module
from typing import TYPE_CHECKING, Any, Optional, Union

from sqler import AsyncSQLerDB, F

from qler._context import _current_job
from qler._notify import fire as _notify_fire
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
from qler.lifecycle import emit as _lifecycle
from qler.rate_limit import RateSpec, parse_rate, try_acquire

if TYPE_CHECKING:
    from prometheus_client import CollectorRegistry

    from qler.cron import CronWrapper
    from qler.metrics import QlerMetrics
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
        dlq: Optional[str] = None,
        metrics: Union[bool, "CollectorRegistry"] = False,
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
        self._dlq: Optional[str] = dlq
        self._rate_limits: dict[str, RateSpec] = {}
        if rate_limits:
            for queue_name, spec in rate_limits.items():
                self._rate_limits[queue_name] = parse_rate(spec)
        self._initialized = False

        # Metrics (opt-in)
        self._metrics: Optional[QlerMetrics] = None
        if metrics is not False:
            try:
                from qler.metrics import QlerMetrics as _QM
            except ImportError:
                raise ConfigurationError(
                    "prometheus-client required: pip install 'qler[metrics]'"
                )
            if metrics is True:
                self._metrics = _QM()
            else:
                from prometheus_client import CollectorRegistry as _CR

                if not isinstance(metrics, _CR):
                    raise ConfigurationError(
                        f"metrics must be True or a CollectorRegistry instance, "
                        f"got {type(metrics).__name__}"
                    )
                self._metrics = _QM(registry=metrics)

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

        # Reverse dependency map for O(1) child lookups
        cur = await self._db.adapter.execute(
            "CREATE TABLE IF NOT EXISTS qler_job_deps ("
            "parent_ulid TEXT NOT NULL, "
            "child_ulid TEXT NOT NULL, "
            "PRIMARY KEY (parent_ulid, child_ulid))"
        )
        await cur.close()
        await self._db.adapter.auto_commit()

        await self._create_indexes()

        # Create archive table with same schema (no indexes — read-only)
        await self._db._ensure_table_with_promoted(
            "qler_jobs_archive", Job.__promoted__, Job.__checks__
        )
        await self._db._ensure_versioned_table("qler_jobs_archive")

        # Wire up queue depth query for Prometheus scrapes
        if self._metrics is not None:
            self._metrics.set_depth_query(self._query_queue_depth)

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
            # 6. DLQ: find failed jobs in a specific queue
            (
                "CREATE INDEX IF NOT EXISTS idx_qler_jobs_dlq "
                "ON qler_jobs (queue_name, status) "
                "WHERE status = 'failed'"
            ),
            # 7. Uniqueness: deduplicate active jobs by unique_key
            (
                "CREATE INDEX IF NOT EXISTS idx_qler_jobs_unique "
                "ON qler_jobs (json_extract(data, '$.unique_key')) "
                "WHERE json_extract(data, '$.unique_key') IS NOT NULL "
                "AND status IN ('pending', 'running')"
            ),
            # 8. Dependency resolution: find pending jobs with unresolved deps
            (
                "CREATE INDEX IF NOT EXISTS idx_qler_jobs_pending_deps "
                "ON qler_jobs (status, pending_dep_count) "
                "WHERE status = 'pending' AND pending_dep_count > 0"
            ),
            # 9. Reverse dependency map: find children by parent
            "CREATE INDEX IF NOT EXISTS idx_qler_job_deps_parent ON qler_job_deps (parent_ulid)",
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
        timeout: Optional[int] = None,
        unique_key: Optional[str] = None,
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

        # Uniqueness check: return existing active (pending/running) job with same key
        if unique_key is not None:
            existing = await Job.query().filter(
                (F("unique_key") == unique_key)
                & F("status").in_list([JobStatus.PENDING.value, JobStatus.RUNNING.value])
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
            unique_key=unique_key,
            dependencies=dep_list,
            pending_dep_count=pending_dep_count,
            timeout=timeout,
            created_at=now,
            updated_at=now,
        )
        await job.save()

        if dep_list:
            for dep_ulid in dep_list:
                cur = await self._db.adapter.execute(
                    "INSERT OR IGNORE INTO qler_job_deps (parent_ulid, child_ulid) VALUES (?, ?)",
                    [dep_ulid, job.ulid],
                )
                await cur.close()
            await self._db.adapter.auto_commit()

        _lifecycle(
            "job.enqueued", job_id=job.ulid, queue=queue_name,
            task=task_path, correlation_id=job.correlation_id,
            priority=job.priority,
        )

        if self._metrics:
            self._metrics.inc_enqueued(queue_name, task_path)

        if self.immediate:
            return await self._execute_immediate(job)

        return job

    async def enqueue_many(
        self,
        jobs: list[dict[str, Any]],
    ) -> list[Job]:
        """Enqueue multiple jobs atomically. All-or-nothing: if any job fails
        validation, none are created.

        Each dict accepts the same keys as enqueue():
            task_path (required), args, kwargs, queue_name, delay, eta,
            priority, max_retries, retry_delay, lease_duration,
            idempotency_key, correlation_id, depends_on, timeout.

        Returns list of Job instances in the same order as input.
        """
        if not jobs:
            return []

        await self._lazy_init()
        now = now_epoch()

        # Phase 1: Validate all jobs and build Job instances
        job_instances: list[Job] = []
        batch_ulids: dict[str, Job] = {}  # for intra-batch dependency resolution
        batch_idem_keys: dict[str, Job] = {}  # for intra-batch idempotency dedup

        for i, spec in enumerate(jobs):
            task_path = spec.get("task_path")
            if not task_path:
                raise ConfigurationError(
                    f"Job at index {i}: task_path is required"
                )

            args = spec.get("args", ())
            kwargs = spec.get("kwargs", None)
            queue_name = spec.get("queue_name", "default")
            delay = spec.get("delay")
            eta = spec.get("eta")
            priority = spec.get("priority", 0)
            max_retries = spec.get("max_retries")
            retry_delay = spec.get("retry_delay")
            lease_duration = spec.get("lease_duration")
            idempotency_key = spec.get("idempotency_key")
            correlation_id = spec.get("correlation_id")
            depends_on = spec.get("depends_on")
            timeout = spec.get("timeout")
            unique_key = spec.get("unique_key")

            # Serialize payload
            payload = {"args": list(args), "kwargs": kwargs or {}}
            try:
                payload_json = json.dumps(payload)
            except (TypeError, ValueError, OverflowError) as exc:
                raise PayloadNotSerializableError(
                    f"Job at index {i}: payload is not JSON-serializable: {exc}"
                ) from exc

            if len(payload_json) > self.max_payload_size:
                raise PayloadTooLargeError(
                    f"Job at index {i}: payload size {len(payload_json)} exceeds max {self.max_payload_size}",
                    size=len(payload_json),
                    max_size=self.max_payload_size,
                )

            # Idempotency check (DB then intra-batch)
            if idempotency_key is not None:
                existing = await Job.query().filter(
                    (F("idempotency_key") == idempotency_key)
                    & (F("status") != JobStatus.CANCELLED.value)
                ).first()
                if existing is not None:
                    job_instances.append(existing)
                    continue
                if idempotency_key in batch_idem_keys:
                    job_instances.append(batch_idem_keys[idempotency_key])
                    continue

            # Uniqueness check
            if unique_key is not None:
                existing = await Job.query().filter(
                    (F("unique_key") == unique_key)
                    & F("status").in_list([JobStatus.PENDING.value, JobStatus.RUNNING.value])
                ).first()
                if existing is not None:
                    job_instances.append(existing)
                    continue

            # Validate dependencies (including intra-batch)
            dep_list: list[str] = []
            pending_dep_count = 0
            if depends_on:
                # Resolve integer indices to ULIDs of earlier jobs in this batch
                resolved: list[str] = []
                for dep in depends_on:
                    if isinstance(dep, int):
                        if dep < 0 or dep >= i:
                            raise DependencyError(
                                f"Job at index {i}: integer dependency index {dep} "
                                f"out of range (must be 0..{i - 1})",
                                dependency_ulid=str(dep),
                            )
                        resolved.append(job_instances[dep].ulid)
                    else:
                        resolved.append(dep)
                dep_list = list(dict.fromkeys(resolved))
                # Check DB for existing deps
                db_dep_ulids = [u for u in dep_list if u not in batch_ulids]
                batch_dep_ulids = [u for u in dep_list if u in batch_ulids]

                if db_dep_ulids:
                    dep_jobs = await Job.query().filter(
                        F("ulid").in_list(db_dep_ulids)
                    ).all()
                    found_ulids = {j.ulid for j in dep_jobs}
                    for dep_ulid in db_dep_ulids:
                        if dep_ulid not in found_ulids:
                            raise DependencyError(
                                f"Job at index {i}: dependency {dep_ulid} not found",
                                dependency_ulid=dep_ulid,
                            )
                    for dep_job in dep_jobs:
                        if dep_job.status == JobStatus.FAILED.value:
                            raise DependencyError(
                                f"Job at index {i}: dependency {dep_job.ulid} is failed",
                                dependency_ulid=dep_job.ulid,
                            )
                        if dep_job.status == JobStatus.CANCELLED.value:
                            raise DependencyError(
                                f"Job at index {i}: dependency {dep_job.ulid} is cancelled",
                                dependency_ulid=dep_job.ulid,
                            )
                    pending_dep_count += sum(
                        1 for j in dep_jobs if j.status != JobStatus.COMPLETED.value
                    )

                # Intra-batch deps are always pending
                pending_dep_count += len(batch_dep_ulids)

            # Calculate ETA
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
                unique_key=unique_key,
                dependencies=dep_list,
                pending_dep_count=pending_dep_count,
                timeout=timeout,
                created_at=now,
                updated_at=now,
            )
            job_instances.append(job)
            batch_ulids[job.ulid] = job
            if idempotency_key is not None:
                batch_idem_keys[idempotency_key] = job

        # Phase 2: Batch-save all new jobs (skip idempotency/uniqueness-matched existing ones)
        # Deduplicate by identity — intra-batch dedup may add the same object twice
        seen_ids: set[int] = set()
        new_jobs: list[Job] = []
        for j in job_instances:
            if j._id is None and id(j) not in seen_ids:
                seen_ids.add(id(j))
                new_jobs.append(j)
        if new_jobs:
            await Job.asave_many(new_jobs)

        for job in new_jobs:
            if job.dependencies:
                for dep_ulid in job.dependencies:
                    cur = await self._db.adapter.execute(
                        "INSERT OR IGNORE INTO qler_job_deps (parent_ulid, child_ulid) VALUES (?, ?)",
                        [dep_ulid, job.ulid],
                    )
                    await cur.close()
                await self._db.adapter.auto_commit()
            _lifecycle(
                "job.enqueued", job_id=job.ulid, queue=job.queue_name,
                task=job.task, correlation_id=job.correlation_id,
                priority=job.priority, batch=True,
            )
            if self._metrics:
                self._metrics.inc_enqueued(job.queue_name, job.task)

        # Phase 3: Execute in immediate mode if applicable
        if self.immediate:
            results = []
            for job in job_instances:
                if job.status == JobStatus.PENDING.value:
                    results.append(await self._execute_immediate(job))
                else:
                    results.append(job)
            return results

        return job_instances

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
        failure_kind = FailureKind.EXCEPTION
        result: Any = None
        timeout = job.timeout
        token = _current_job.set(job)
        try:
            if task_wrapper.sync:
                coro = asyncio.to_thread(task_wrapper.fn, *args, **kwargs)
            else:
                coro = task_wrapper.fn(*args, **kwargs)
            if timeout is not None:
                if task_wrapper.sync:
                    # Sync tasks run in threads that can't be cancelled.
                    # Shield prevents wait_for from blocking on cancellation.
                    result = await asyncio.wait_for(
                        asyncio.shield(coro), timeout=timeout
                    )
                else:
                    result = await asyncio.wait_for(coro, timeout=timeout)
            else:
                result = await coro
        except asyncio.TimeoutError:
            task_exc = TimeoutError(
                f"Task {job.task} timed out after {timeout}s"
            )
            failure_kind = FailureKind.TIMEOUT
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
                    job, worker_id, task_exc, failure_kind=failure_kind
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

        _lifecycle(
            "job.claimed", job_id=job.ulid, queue=job.queue_name,
            task=job.task, correlation_id=job.correlation_id,
            worker_id=worker_id, attempt=job.attempts,
        )

        if self._metrics:
            self._metrics.inc_claimed(job.queue_name, job.task)

        return job

    async def claim_jobs(
        self,
        worker_id: str,
        queues: list[str],
        n: int = 1,
    ) -> list[Job]:
        """Atomically claim up to *n* eligible jobs. Returns list of Jobs.

        Uses batch UPDATE via ``update_n()`` to claim multiple jobs in a single
        SQL statement, then performs per-job post-processing (poison pill check,
        rate limit check, attempt record creation).

        Args:
            worker_id: Identifier of the claiming worker.
            queues: Queue names to claim from.
            n: Maximum number of jobs to claim.

        Returns:
            list[Job]: Claimed jobs (may be shorter than *n*).
        """
        await self._lazy_init()

        if not queues:
            raise ConfigurationError("queues list must not be empty")
        if n < 1:
            raise ConfigurationError("n must be >= 1")

        now_ts = now_epoch()

        # Atomic batch claim
        jobs = await Job.query().filter(
            (F("status") == JobStatus.PENDING.value)
            & F("queue_name").in_list(queues)
            & (F("eta") <= now_ts)
            & (F("pending_dep_count") == 0)
        ).order_by("-priority", "eta", "ulid").update_n(
            n,
            status=JobStatus.RUNNING.value,
            worker_id=worker_id,
            attempts=F("attempts") + 1,
            lease_expires_at=now_ts + self.default_lease_duration,
            updated_at=now_ts,
        )

        if not jobs:
            return []

        claimed: list[Job] = []
        for job in jobs:
            attempt_id = generate_ulid()

            # Set last_attempt_id per-job (unique per job, can't batch)
            await Job.query().filter(
                (F("ulid") == job.ulid)
                & (F("status") == JobStatus.RUNNING.value)
            ).update_one(
                last_attempt_id=attempt_id,
                updated_at=now_ts,
            )
            # Keep in-memory object in sync for _terminalize_attempt
            job.last_attempt_id = attempt_id

            # Poison pill check
            try:
                json.loads(job.payload_json)
            except (json.JSONDecodeError, TypeError):
                await self._poison_pill(job, worker_id, attempt_id, now_ts)
                continue

            # Rate limit check
            if not self.immediate:
                rate_limited = await self._check_rate_limits(
                    job, worker_id, now_ts,
                )
                if rate_limited:
                    continue

            # Create attempt record
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

            _lifecycle(
                "job.claimed", job_id=job.ulid, queue=job.queue_name,
                task=job.task, correlation_id=job.correlation_id,
                worker_id=worker_id, attempt=job.attempts,
            )

            if self._metrics:
                self._metrics.inc_claimed(job.queue_name, job.task)

            claimed.append(job)

        return claimed

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

        _lifecycle(
            "job.completed", job_id=job.ulid, queue=job.queue_name,
            task=job.task, correlation_id=job.correlation_id,
        )

        if self._metrics:
            self._metrics.inc_completed(job.queue_name, job.task)

        _notify_fire(job.ulid)

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
            update_fields["progress"] = None
            update_fields["progress_message"] = ""
        else:
            update_fields["status"] = JobStatus.FAILED.value
            update_fields["finished_at"] = now
            if self._dlq:
                update_fields["original_queue"] = job.queue_name
                update_fields["queue_name"] = self._dlq

        updated = await Job.query().filter(
            (F("ulid") == job.ulid)
            & (F("status") == JobStatus.RUNNING.value)
            & (F("worker_id") == worker_id)
        ).update_one(**update_fields)

        if updated is None:
            return  # Lost ownership

        if can_retry:
            _lifecycle(
                "job.retried", job_id=job.ulid, queue=job.queue_name,
                task=job.task, correlation_id=job.correlation_id,
                failure_kind=failure_kind.value, retry_count=job.retry_count + 1,
                error=error_msg or "",
            )
        else:
            _lifecycle(
                "job.failed", job_id=job.ulid, queue=job.queue_name,
                task=job.task, correlation_id=job.correlation_id,
                failure_kind=failure_kind.value, error=error_msg or "",
            )

        if self._metrics:
            if can_retry:
                self._metrics.inc_retried(job.queue_name, job.task)
            else:
                self._metrics.inc_failed(job.queue_name, job.task, failure_kind.value)

        # Cascade-cancel dependents on terminal failure
        if not can_retry:
            _notify_fire(job.ulid)
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
                progress=None,
                progress_message="",
            )
            if job is None:
                break

            _lifecycle(
                "job.lease_recovered", job_id=job.ulid, queue=job.queue_name,
                task=job.task, correlation_id=job.correlation_id,
            )

            # Terminalize the attempt as LEASE_EXPIRED
            await self._terminalize_attempt(
                job.last_attempt_id,
                AttemptStatus.LEASE_EXPIRED.value,
                now,
                failure_kind=FailureKind.LEASE_EXPIRED.value,
                error="Lease expired",
            )
            recovered += 1

        if self._metrics and recovered:
            self._metrics.inc_leases_recovered(recovered)

        return recovered

    # ------------------------------------------------------------------
    # Dependencies
    # ------------------------------------------------------------------

    async def _resolve_dependencies(self, completed_ulid: str) -> None:
        """Decrement pending_dep_count for jobs depending on the completed job."""
        cur = await self._db.adapter.execute(
            "SELECT child_ulid FROM qler_job_deps WHERE parent_ulid = ?",
            [completed_ulid],
        )
        children = await cur.fetchall()
        await cur.close()
        await self._db.adapter.auto_commit()

        for (child_ulid,) in children:
            await Job.query().filter(
                (F("ulid") == child_ulid)
                & (F("status") == JobStatus.PENDING.value)
                & (F("pending_dep_count") > 0)
            ).update_one(
                pending_dep_count=F("pending_dep_count") - 1,
                updated_at=now_epoch(),
            )

    async def _cascade_cancel_dependents(self, failed_ulid: str) -> None:
        """Cancel PENDING jobs that depend on a failed/cancelled job, recursively."""
        cur = await self._db.adapter.execute(
            "SELECT child_ulid FROM qler_job_deps WHERE parent_ulid = ?",
            [failed_ulid],
        )
        children = await cur.fetchall()
        await cur.close()
        await self._db.adapter.auto_commit()

        now = now_epoch()
        cancelled_ulids: list[str] = []
        for (child_ulid,) in children:
            updated = await Job.query().filter(
                (F("ulid") == child_ulid)
                & (F("status") == JobStatus.PENDING.value)
            ).update_one(
                status=JobStatus.CANCELLED.value,
                last_error=f"Dependency {failed_ulid} failed/cancelled",
                finished_at=now,
                updated_at=now,
            )
            if updated is not None:
                _notify_fire(child_ulid)
                cancelled_ulids.append(child_ulid)

        for ulid in cancelled_ulids:
            await self._cascade_cancel_dependents(ulid)

    async def cancel_job(self, job: Job) -> bool:
        """Cancel a job and cascade-cancel its dependents."""
        ok = await job.cancel()
        if ok and job.status == JobStatus.CANCELLED.value:
            _notify_fire(job.ulid)
            await self._cascade_cancel_dependents(job.ulid)
        return ok

    async def replay_job(
        self, job: Job, *, queue_name: Optional[str] = None
    ) -> Optional[Job]:
        """Replay a FAILED DLQ job back to its original queue.

        Resets the job to PENDING with retry_count=0 and clears the DLQ fields.
        Returns the updated Job, or None if the job wasn't FAILED.

        Args:
            job: The failed job to replay.
            queue_name: Override queue to replay into. Defaults to job.original_queue.
        """
        target_queue = queue_name or job.original_queue
        if not target_queue:
            target_queue = "default"

        now = now_epoch()
        updated = await Job.query().filter(
            (F("ulid") == job.ulid) & (F("status") == JobStatus.FAILED.value)
        ).update_one(
            status=JobStatus.PENDING.value,
            queue_name=target_queue,
            original_queue="",
            retry_count=0,
            finished_at=None,
            eta=now,
            updated_at=now,
            progress=None,
            progress_message="",
        )
        return updated

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

    async def _query_queue_depth(self) -> list[tuple[str, str, int]]:
        """Async query for queue depth — called before /metrics generation."""
        if self._db is None:
            return []
        try:
            cur = await self._db.adapter.execute(
                "SELECT queue_name, status, COUNT(*) "
                "FROM qler_jobs GROUP BY queue_name, status"
            )
            rows = await cur.fetchall()
            await cur.close()
            return rows
        except Exception:
            return []

    # ------------------------------------------------------------------
    # Archival
    # ------------------------------------------------------------------

    _TERMINAL_STATUSES = ("completed", "failed", "cancelled")

    async def archive_jobs(self, older_than_seconds: int = 300) -> int:
        """Move terminal jobs older than threshold to archive table.

        Returns number of jobs archived.
        """
        await self._lazy_init()
        threshold = now_epoch() - older_than_seconds

        _where = (
            "WHERE status IN ('completed', 'failed', 'cancelled') "
            "AND json_extract(data, '$.finished_at') IS NOT NULL "
            "AND json_extract(data, '$.finished_at') < ?"
        )

        async with self._db.transaction():
            cur = await self._db.adapter.execute(
                f"INSERT INTO qler_jobs_archive SELECT * FROM qler_jobs {_where}",
                [threshold],
            )
            await cur.close()

            # Clean up reverse dependency map for jobs being archived
            cur = await self._db.adapter.execute(
                f"DELETE FROM qler_job_deps WHERE parent_ulid IN "
                f"(SELECT ulid FROM qler_jobs {_where}) "
                f"OR child_ulid IN (SELECT ulid FROM qler_jobs {_where})",
                [threshold, threshold],
            )
            await cur.close()

            cur = await self._db.adapter.execute(
                f"DELETE FROM qler_jobs {_where}",
                [threshold],
            )
            count = cur.rowcount
            await cur.close()

        if count:
            _lifecycle(
                "job.archived",
                job_id="",
                queue="*",
                task="*",
                count=count,
                older_than_seconds=older_than_seconds,
            )
        return count

    async def archived_jobs(
        self,
        *,
        queue_name: str | None = None,
        status: str | None = None,
        limit: int = 100,
    ) -> list[Job]:
        """Query archived jobs (read-only)."""
        await self._lazy_init()
        Job.set_db(self._db, "qler_jobs_archive")
        try:
            query = Job.query()
            filters = []
            if queue_name:
                filters.append(F("queue_name") == queue_name)
            if status:
                filters.append(F("status") == status)
            if filters:
                combined = filters[0]
                for f in filters[1:]:
                    combined = combined & f
                query = query.filter(combined)
            return await query.order_by("-ulid").limit(limit).all()
        finally:
            Job.set_db(self._db, "qler_jobs")

    async def pool_health(self) -> dict[str, Any]:
        """Report connection pool state for monitoring."""
        await self._lazy_init()
        adapter = self._db.adapter
        return {
            "pool_size": adapter._pool_size,
            "available": adapter._pool.qsize(),
            "healthy": adapter._pool.qsize() == adapter._pool_size,
        }

    async def archive_count(self) -> int:
        """Return total number of archived jobs."""
        await self._lazy_init()
        cur = await self._db.adapter.execute(
            "SELECT COUNT(*) FROM qler_jobs_archive"
        )
        row = await cur.fetchone()
        await cur.close()
        return row[0] if row else 0

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
