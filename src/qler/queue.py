"""Queue — the central coordinator for enqueue, claim, complete, and fail operations."""

from __future__ import annotations

import asyncio
import json
import logging
import random
import traceback as tb_module
from typing import TYPE_CHECKING, Any

from sqler import AsyncSQLerDB, AsyncSQLerQuerySet, F, SQLerExpression
from sqler.exceptions import UniqueConstraintError

from qler._context import _current_job
from qler._notify import fire as _notify_fire
from qler._schema import ensure_schema
from qler._sqler import grouped_count
from qler._time import generate_ulid, now_epoch
from qler.enums import (
    RETRYABLE_FAILURES,
    TERMINAL_STATUSES,
    AttemptStatus,
    FailureKind,
    JobStatus,
)
from qler.exceptions import (
    ConfigurationError,
    DependencyError,
    PayloadNotSerializableError,
    PayloadTooLargeError,
    TaskNotRegisteredError,
)
from qler.lifecycle import emit as _lifecycle
from qler.models.attempt import JobAttempt
from qler.models.bucket import RateLimitBucket
from qler.models.dependency import JobDependency
from qler.models.job import (
    ARCHIVE_TABLE,
    Job,
    failed_to_pending_fields,
    key_active,
    unique_key_conflict,
)
from qler.rate_limit import RateSpec, parse_rate, try_acquire

if TYPE_CHECKING:
    from prometheus_client import CollectorRegistry

    from qler.cron import CronWrapper
    from qler.metrics import QlerMetrics
    from qler.task import TaskWrapper

_MAX_TRACEBACK_LENGTH = 8_192
_MAX_ERROR_LENGTH = 1_024
_MAX_RETRY_DELAY = 86_400  # 24 hours

logger = logging.getLogger(__name__)


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
        rate_limits: dict[str, str] | None = None,
        dlq: str | None = None,
        metrics: bool | CollectorRegistry = False,
    ) -> None:
        if isinstance(db, str):
            self._db_path: str | None = db
            self._db: AsyncSQLerDB | None = None
            self._owns_db = True
        else:
            self._db_path = None
            self._db = db
            self._owns_db = False

        if default_retry_delay < 1:
            raise ConfigurationError(f"default_retry_delay must be >= 1, got {default_retry_delay}")

        self.immediate = immediate
        self.default_lease_duration = default_lease_duration
        self.default_max_retries = default_max_retries
        self.default_retry_delay = default_retry_delay
        self.max_payload_size = max_payload_size
        self._tasks: dict[str, TaskWrapper] = {}
        self._cron_tasks: dict[str, CronWrapper] = {}
        self._dlq: str | None = dlq
        self._rate_limits: dict[str, RateSpec] = {}
        if rate_limits:
            for queue_name, spec in rate_limits.items():
                self._rate_limits[queue_name] = parse_rate(spec)
        self._initialized = False

        # Metrics (opt-in)
        self._metrics: QlerMetrics | None = None
        if metrics is not False:
            try:
                from qler.metrics import QlerMetrics as _QM  # noqa: N814
            except ImportError as exc:
                raise ConfigurationError(
                    "prometheus-client required: pip install 'qler[metrics]'"
                ) from exc
            if metrics is True:
                self._metrics = _QM()
            else:
                from prometheus_client import CollectorRegistry as _CR  # noqa: N814

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

    @property
    def jobs(self) -> AsyncSQLerQuerySet[Job]:
        """This queue's jobs table, as a sqler queryset bound to its own database."""
        return Job.using(self.db, Job.__tablename__)

    @property
    def attempts(self) -> AsyncSQLerQuerySet[JobAttempt]:
        """This queue's job-attempts table, as a sqler queryset bound to its own database."""
        return JobAttempt.using(self.db, JobAttempt.__tablename__)

    @property
    def archive(self) -> AsyncSQLerQuerySet[Job]:
        """This queue's archived-jobs table, as a sqler queryset bound to its own database."""
        return Job.using(self.db, ARCHIVE_TABLE)

    @property
    def _dependency_edges(self) -> AsyncSQLerQuerySet[JobDependency]:
        """The (parent, child) edges, as a sqler queryset bound to this queue's database."""
        return JobDependency.using(self.db)

    async def init_db(self) -> None:
        """Initialize the database schema. Lazy and idempotent."""
        if self._initialized:
            return

        if self._db is None:
            # on_disk sets the PRAGMAs: WAL, busy_timeout 5000, synchronous NORMAL, wal_autocheckpoint 1000.
            if self._db_path is None:
                raise RuntimeError("Queue has neither a database nor a path to open one.")
            self._db = AsyncSQLerDB.on_disk(self._db_path)
            await self._db.connect()

        db = self.db

        Job.set_db(db, Job.__tablename__)
        JobAttempt.set_db(db, JobAttempt.__tablename__)
        RateLimitBucket.set_db(db, RateLimitBucket.__tablename__)

        await ensure_schema(db)

        # Wire up queue depth query for Prometheus scrapes
        if self._metrics is not None:
            self._metrics.set_depth_query(self._query_queue_depth)

        self._initialized = True

    # ------------------------------------------------------------------
    # Enqueue
    # ------------------------------------------------------------------

    async def enqueue(
        self,
        task_path: str,
        args: tuple = (),
        kwargs: dict[str, Any] | None = None,
        *,
        queue_name: str = "default",
        delay: int | None = None,
        eta: int | None = None,
        priority: int = 0,
        max_retries: int | None = None,
        retry_delay: int | None = None,
        lease_duration: int | None = None,
        idempotency_key: str | None = None,
        correlation_id: str | None = None,
        depends_on: list[str] | None = None,
        timeout: int | None = None,
        unique_key: str | None = None,
    ) -> Job:
        """Enqueue a new job. Returns the created Job instance.

        A job already active under ``idempotency_key`` or ``unique_key`` is returned as
        it is instead, and is not run in immediate mode.
        """
        spec = {
            "task_path": task_path,
            "args": args,
            "kwargs": kwargs,
            "queue_name": queue_name,
            "delay": delay,
            "eta": eta,
            "priority": priority,
            "max_retries": max_retries,
            "retry_delay": retry_delay,
            "lease_duration": lease_duration,
            "idempotency_key": idempotency_key,
            "correlation_id": correlation_id,
            "depends_on": depends_on,
            "timeout": timeout,
            "unique_key": unique_key,
        }
        return (await self._enqueue_specs([spec], batch=False))[0]

    async def enqueue_many(
        self,
        jobs: list[dict[str, Any]],
    ) -> list[Job]:
        """Enqueue multiple jobs atomically. All-or-nothing: if any job fails
        validation, none are created.

        Each dict accepts the same keys as enqueue():
            task_path (required), args, kwargs, queue_name, delay, eta,
            priority, max_retries, retry_delay, lease_duration,
            idempotency_key, unique_key, correlation_id, depends_on, timeout;
        and two of its own: ``ulid`` (the new job's ulid) and, in ``depends_on``, an
        int (the index of an earlier entry of this batch).

        Returns list of Job instances in the same order as input. Entries with one key
        share one Job object; in immediate mode only the jobs this call created run.
        """
        if not jobs:
            return []
        return await self._enqueue_specs(jobs, batch=True)

    async def _active_by_key(
        self, jobs_q: AsyncSQLerQuerySet[Job], field: str, specs: list[dict[str, Any]]
    ) -> dict[str, Job]:
        """The active job of each ``field`` key the specs name, chunked under SQLite's
        999-variable limit."""
        keys = list(dict.fromkeys(s[field] for s in specs if s.get(field) is not None))
        found: dict[str, Job] = {}
        for off in range(0, len(keys), 900):
            for j in await jobs_q.filter(
                F(field).in_list(keys[off : off + 900]) & key_active(field)
            ).all():
                # Two active rows with one key exist only in a file from before the
                # UNIQUE index: the first row read is the match, as .first() gives.
                found.setdefault(getattr(j, field), j)
        return found

    async def _enqueue_specs(self, specs: list[dict[str, Any]], *, batch: bool) -> list[Job]:
        """The one enqueue path, behind enqueue() (``batch=False``) and enqueue_many().

        Returns one Job per spec, in order. A batch's errors start "Job at index i: ";
        the single call's carry no prefix, and an int in its ``depends_on`` is refused.
        """
        await self._lazy_init()
        now = now_epoch()

        # What needs no database is checked first, for every spec, before the write lock
        # is taken: a spec that cannot be stored is refused without waiting for it.
        payloads: list[str] = []
        for i, spec in enumerate(specs):
            where = f"Job at index {i}: " if batch else ""
            payload_word = f"{where}payload" if batch else "Payload"
            if not spec.get("task_path"):
                raise ConfigurationError(f"{where}task_path is required")
            payload = {"args": list(spec.get("args", ())), "kwargs": spec.get("kwargs") or {}}
            try:
                payload_json = json.dumps(payload)
            except (TypeError, ValueError, OverflowError) as exc:
                raise PayloadNotSerializableError(
                    f"{payload_word} is not JSON-serializable: {exc}"
                ) from exc
            if len(payload_json) > self.max_payload_size:
                raise PayloadTooLargeError(
                    f"{payload_word} size {len(payload_json)} exceeds max {self.max_payload_size}",
                    size=len(payload_json),
                    max_size=self.max_payload_size,
                )
            payloads.append(payload_json)

        # One write: the key look-ups, the parents' statuses and every row with its edges
        # run in one transaction that holds the write lock from its start, so no other
        # enqueue (task, connection or process) can write the same key between the check
        # and the save. The UNIQUE indexes on the keys are the database's own guard.
        jobs_q = self.jobs
        async with self.db.transaction():
            idem_existing = await self._active_by_key(jobs_q, "idempotency_key", specs)
            unique_existing = await self._active_by_key(jobs_q, "unique_key", specs)

            job_instances: list[Job] = []  # one per spec, in order
            new_jobs: list[Job] = []  # the jobs this call creates, each once
            batch_ulids: set[str] = set()
            batch_idem: dict[str, Job] = {}
            batch_unique: dict[str, Job] = {}

            for i, spec in enumerate(specs):
                where = f"Job at index {i}: " if batch else ""
                dep_word = f"{where}dependency" if batch else "Dependency job"

                # A key already active (in the database, then earlier in this call) is
                # that job: idempotency key first, then unique key.
                idempotency_key = spec.get("idempotency_key")
                unique_key = spec.get("unique_key")
                matched: Job | None = None
                if idempotency_key is not None:
                    matched = idem_existing.get(idempotency_key, batch_idem.get(idempotency_key))
                if matched is None and unique_key is not None:
                    matched = unique_existing.get(unique_key, batch_unique.get(unique_key))
                if matched is not None:
                    job_instances.append(matched)
                    continue

                # Dependencies: an int is the index of an earlier entry of the batch
                dep_list: list[str] = []
                pending_dep_count = 0
                if spec.get("depends_on"):
                    resolved: list[str] = []
                    for dep in spec["depends_on"]:
                        if not isinstance(dep, int):
                            resolved.append(dep)
                        elif not batch:
                            raise DependencyError(
                                f"Integer dependency index {dep} is only meaningful in "
                                "enqueue_many; enqueue takes job ULIDs",
                                dependency_ulid=str(dep),
                            )
                        elif 0 <= dep < i:
                            resolved.append(job_instances[dep].ulid)
                        else:
                            raise DependencyError(
                                f"{where}integer dependency index {dep} "
                                f"out of range (must be 0..{i - 1})",
                                dependency_ulid=str(dep),
                            )
                    dep_list = list(dict.fromkeys(resolved))  # each parent once, in order
                    db_deps = [u for u in dep_list if u not in batch_ulids]
                    if db_deps:
                        dep_jobs = await jobs_q.filter(F("ulid").in_list(db_deps)).all()
                        found_ulids = {j.ulid for j in dep_jobs}
                        for dep_ulid in db_deps:
                            if dep_ulid not in found_ulids:
                                raise DependencyError(
                                    f"{dep_word} {dep_ulid} not found",
                                    dependency_ulid=dep_ulid,
                                )
                        for dep_job in dep_jobs:
                            if dep_job.status in (
                                JobStatus.FAILED.value,
                                JobStatus.CANCELLED.value,
                            ):
                                raise DependencyError(
                                    f"{dep_word} {dep_job.ulid} is {dep_job.status}",
                                    dependency_ulid=dep_job.ulid,
                                )
                        pending_dep_count += sum(
                            1 for j in dep_jobs if j.status != JobStatus.COMPLETED.value
                        )
                    # A parent created by this call is pending
                    pending_dep_count += len(dep_list) - len(db_deps)

                eta = spec.get("eta")
                delay = spec.get("delay")
                max_retries = spec.get("max_retries")
                retry_delay = spec.get("retry_delay")
                lease_duration = spec.get("lease_duration")
                job = Job(
                    ulid=spec.get("ulid") or generate_ulid(),
                    status=JobStatus.PENDING.value,
                    queue_name=spec.get("queue_name", "default"),
                    priority=spec.get("priority", 0),
                    eta=eta if eta is not None else now + delay if delay is not None else now,
                    task=spec["task_path"],
                    payload_json=payloads[i],
                    max_retries=max_retries
                    if max_retries is not None
                    else self.default_max_retries,
                    retry_delay=retry_delay
                    if retry_delay is not None
                    else self.default_retry_delay,
                    lease_duration=lease_duration
                    if lease_duration is not None
                    else self.default_lease_duration,
                    correlation_id=spec.get("correlation_id") or "",
                    idempotency_key=idempotency_key,
                    unique_key=unique_key,
                    dependencies=dep_list,
                    pending_dep_count=pending_dep_count,
                    timeout=spec.get("timeout"),
                    created_at=now,
                    updated_at=now,
                )
                job_instances.append(job)
                new_jobs.append(job)
                batch_ulids.add(job.ulid)
                if idempotency_key is not None:
                    batch_idem[idempotency_key] = job
                if unique_key is not None:
                    batch_unique[unique_key] = job

            if len(new_jobs) == 1:
                # One row is one INSERT; save_many would wrap it in a savepoint of its own
                await new_jobs[0].save(db=self.db)
            elif new_jobs:
                await Job.save_many(new_jobs, db=self.db)
            # Each job's dependencies list holds a parent once: one edge per (parent, child)
            edges = [
                JobDependency(parent_ulid=dep_ulid, child_ulid=job.ulid)
                for job in new_jobs
                for dep_ulid in job.dependencies
            ]
            if edges:
                await JobDependency.save_many(edges, db=self.db)

        # Told only after the commit, and only of the jobs this call created
        extra = {"batch": True} if batch else {}
        for job in new_jobs:
            _lifecycle(
                "job.enqueued",
                job_id=job.ulid,
                queue=job.queue_name,
                task=job.task,
                correlation_id=job.correlation_id,
                priority=job.priority,
                **extra,
            )
            if self._metrics:
                self._metrics.inc_enqueued(job.queue_name, job.task)

        if self.immediate:
            # Run each job this call created once; a matched job is returned as it is.
            ran = {job.ulid: await self._execute_immediate(job) for job in new_jobs}
            return [ran.get(j.ulid, j) for j in job_instances]

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
        claimed = await self.jobs.filter(
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
            updated = await self.jobs.filter(F("ulid") == job.ulid).first()
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
                    result = await asyncio.wait_for(asyncio.shield(coro), timeout=timeout)
                else:
                    result = await asyncio.wait_for(coro, timeout=timeout)
            else:
                result = await coro
        except TimeoutError:
            task_exc = TimeoutError(f"Task {job.task} timed out after {timeout}s")
            failure_kind = FailureKind.TIMEOUT
        except asyncio.CancelledError:
            await self.fail_job(job, worker_id, failure_kind=FailureKind.CANCELLED)
            raise
        except Exception as exc:
            task_exc = exc
        finally:
            _current_job.reset(token)

        if task_exc is not None:
            # fail_job is one write: if it raises, nothing was written and the error
            # reaches the caller, as it does for complete_job below.
            await self.fail_job(job, worker_id, task_exc, failure_kind=failure_kind)
        else:
            await self.complete_job(job, worker_id, result=result)

        updated = await self.jobs.filter(F("ulid") == job.ulid).first()
        return updated if updated is not None else job

    # ------------------------------------------------------------------
    # Claim
    # ------------------------------------------------------------------

    async def claim_job(
        self,
        worker_id: str,
        queues: list[str],
    ) -> Job | None:
        """Atomically claim the next eligible job. Returns Job or None.

        The first element of ``claim_jobs(worker_id, queues, 1)``, or None.
        """
        jobs = await self.claim_jobs(worker_id, queues, 1)
        return jobs[0] if jobs else None

    async def claim_jobs(
        self,
        worker_id: str,
        queues: list[str],
        n: int = 1,
    ) -> list[Job]:
        """Atomically claim up to *n* eligible jobs. Returns list of Jobs.

        One claiming UPDATE (``update_one`` for one job, ``update_n()`` for more), then
        per-job post-processing (poison pill check, rate limit check, attempt record).
        A poison pill or a rate-limited job is not returned.

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
        jobs_table = self.jobs
        # The lease is each job's own lease_duration, read from its row by the update.
        eligible = jobs_table.filter(
            (F("status") == JobStatus.PENDING.value)
            & F("queue_name").in_list(queues)
            & (F("eta") <= now_ts)
            & (F("pending_dep_count") == 0)
        ).order_by("-priority", "eta", "ulid")
        claim_fields: dict[str, Any] = {
            "status": JobStatus.RUNNING.value,
            "worker_id": worker_id,
            "attempts": F("attempts") + 1,
            "lease_expires_at": F("lease_duration") + now_ts,
            "updated_at": now_ts,
        }

        # One write: the claiming update, each job's attempt id, the poison-pill and
        # rate-limit writes and every attempt record commit together or not at all, so a
        # failure at any job leaves every job of the claim pending as before it.
        claimed: list[Job] = []
        async with self.db.transaction():
            if n == 1:
                # One job: its attempt id goes into the claiming update itself.
                attempt_id = generate_ulid()
                job = await eligible.update_one(last_attempt_id=attempt_id, **claim_fields)
                rows = [] if job is None else [(job, attempt_id)]
            else:
                rows = []
                for job in await eligible.update_n(n, **claim_fields):
                    # update_n writes one value to every row: each job's own attempt id
                    # takes one more update.
                    attempt_id = generate_ulid()
                    await jobs_table.filter(
                        (F("ulid") == job.ulid) & (F("status") == JobStatus.RUNNING.value)
                    ).update_one(last_attempt_id=attempt_id, updated_at=now_ts)
                    # Keep in-memory object in sync for _terminalize_attempt
                    job.last_attempt_id = attempt_id
                    rows.append((job, attempt_id))

            for job, attempt_id in rows:
                # Poison pill check: validate payload is parseable
                try:
                    json.loads(job.payload_json)
                except (json.JSONDecodeError, TypeError):
                    await self._poison_pill(job, worker_id, attempt_id, now_ts)
                    continue

                # Rate limit checks (queue-level, then task-level)
                if not self.immediate and await self._check_rate_limits(job, worker_id, now_ts):
                    continue

                await JobAttempt(
                    ulid=attempt_id,
                    job_ulid=job.ulid,
                    status=AttemptStatus.RUNNING.value,
                    attempt_number=job.attempts,
                    worker_id=worker_id,
                    started_at=now_ts,
                    lease_expires_at=job.lease_expires_at,
                ).save(db=self.db)
                claimed.append(job)

        # Events and metrics only for the jobs the committed claim returns.
        for job in claimed:
            _lifecycle(
                "job.claimed",
                job_id=job.ulid,
                queue=job.queue_name,
                task=job.task,
                correlation_id=job.correlation_id,
                worker_id=worker_id,
                attempt=job.attempts,
            )

            if self._metrics:
                self._metrics.inc_claimed(job.queue_name, job.task)

        return claimed

    async def _poison_pill(self, job: Job, worker_id: str, attempt_id: str, now_ts: int) -> None:
        """Handle a job with an unparseable payload (inside the claim's transaction)."""
        await self.jobs.filter(
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
        await attempt.save(db=self.db)

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

        One transaction: the ownership-guarded status update, the dependents' counts and
        the attempt's end. The lifecycle event, metric and wake-up follow the commit.
        """
        # Serialize result
        result_json: str | None = None
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

        # One write: the guarded status update, the dependents' counts and the attempt's
        # end commit together or not at all, so a stop between them (a locked database,
        # a dying process) cannot leave a completed parent above children whose count
        # nothing will lower again. The outside world hears only of a committed completion.
        now = now_epoch()
        async with self.db.transaction():
            updated = await self.jobs.filter(
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
                return  # Lost ownership: nothing written, nothing told

            # Resolve dependencies: unblock dependent jobs
            await self._resolve_dependencies(job.ulid)

            await self._terminalize_attempt(job.last_attempt_id, AttemptStatus.COMPLETED.value, now)

        _lifecycle(
            "job.completed",
            job_id=job.ulid,
            queue=job.queue_name,
            task=job.task,
            correlation_id=job.correlation_id,
        )

        if self._metrics:
            self._metrics.inc_completed(job.queue_name, job.task)

        _notify_fire(job.ulid)

    # ------------------------------------------------------------------
    # Fail
    # ------------------------------------------------------------------

    async def fail_job(
        self,
        job: Job,
        worker_id: str,
        exc: BaseException | None = None,
        failure_kind: FailureKind = FailureKind.EXCEPTION,
    ) -> None:
        """Mark a job as failed, with optional retry.

        One transaction: the ownership-guarded status update, the dependents'
        cancellations and the attempt's end. The lifecycle event, metric and wake-ups
        follow the commit.
        """
        now = now_epoch()
        can_retry = failure_kind in RETRYABLE_FAILURES and job.retry_count < job.max_retries

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

        # One write: the guarded status update (with the dead-letter move), the dependents'
        # cancellations and the attempt's end commit together or not at all, so a stop
        # between them cannot leave a failed parent above pending children that will
        # neither run nor be cancelled. The outside world hears only of what committed.
        cancelled: list[str] = []
        async with self.db.transaction():
            updated = await self.jobs.filter(
                (F("ulid") == job.ulid)
                & (F("status") == JobStatus.RUNNING.value)
                & (F("worker_id") == worker_id)
            ).update_one(**update_fields)

            if updated is None:
                return  # Lost ownership: nothing written, nothing told

            # Cascade-cancel dependents on terminal failure
            if not can_retry:
                cancelled = await self._cascade_cancel_dependents(job.ulid)

            await self._terminalize_attempt(
                job.last_attempt_id,
                AttemptStatus.FAILED.value,
                now,
                error=error_msg,
                traceback=error_tb_str,
                failure_kind=failure_kind.value,
            )

        if can_retry:
            _lifecycle(
                "job.retried",
                job_id=job.ulid,
                queue=job.queue_name,
                task=job.task,
                correlation_id=job.correlation_id,
                failure_kind=failure_kind.value,
                retry_count=job.retry_count + 1,
                error=error_msg or "",
            )
        else:
            _lifecycle(
                "job.failed",
                job_id=job.ulid,
                queue=job.queue_name,
                task=job.task,
                correlation_id=job.correlation_id,
                failure_kind=failure_kind.value,
                error=error_msg or "",
            )

        if self._metrics:
            if can_retry:
                self._metrics.inc_retried(job.queue_name, job.task)
            else:
                self._metrics.inc_failed(job.queue_name, job.task, failure_kind.value)

        if not can_retry:
            _notify_fire(job.ulid)
            for child_ulid in cancelled:
                _notify_fire(child_ulid)

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
            expired = (F("status") == JobStatus.RUNNING.value) & (F("lease_expires_at") <= now)
            if await self._recover_one(expired, now) is None:
                break
            recovered += 1

        if self._metrics and recovered:
            self._metrics.inc_leases_recovered(recovered)

        return recovered

    async def _release_lease(self, job_ulid: str, worker_id: str) -> bool:
        """Recover one job at once, before its lease ends, if `worker_id` still runs it.

        For a worker whose shutdown cut the job and whose task has ended: the job
        becomes what lease recovery makes it. The write is guarded, so a job that
        lease recovery, a cancel or another worker already moved is left alone.
        Returns True if the job was released.
        """
        now = now_epoch()
        still_ours = (
            (F("ulid") == job_ulid)
            & (F("status") == JobStatus.RUNNING.value)
            & (F("worker_id") == worker_id)
        )
        if await self._recover_one(still_ours, now) is None:
            return False
        if self._metrics:
            self._metrics.inc_leases_recovered(1)
        return True

    async def _recover_one(self, where: SQLerExpression, now: int) -> Job | None:
        """Return the first RUNNING job matching `where` to PENDING, as lease recovery does.

        Its attempt ends as LEASE_EXPIRED; its retry count is kept (a lost lease is
        not a task failure). One transaction: the reset and the attempt's end commit
        together or not at all, so a stop between them cannot leave a pending job
        whose last attempt still says running. Returns the job, or None if none matched.
        """
        async with self.db.transaction():
            job = (
                await self.jobs.filter(where)
                .order_by("lease_expires_at")
                .update_one(
                    status=JobStatus.PENDING.value,
                    worker_id="",
                    lease_expires_at=None,
                    updated_at=now,
                    progress=None,
                    progress_message="",
                )
            )
            if job is None:
                return None

            await self._terminalize_attempt(
                job.last_attempt_id,
                AttemptStatus.LEASE_EXPIRED.value,
                now,
                failure_kind=FailureKind.LEASE_EXPIRED.value,
                error="Lease expired",
            )

        # Told only after the commit
        _lifecycle(
            "job.lease_recovered",
            job_id=job.ulid,
            queue=job.queue_name,
            task=job.task,
            correlation_id=job.correlation_id,
        )
        return job

    # ------------------------------------------------------------------
    # Dependencies
    # ------------------------------------------------------------------

    async def _children_of(self, parent_ulid: str) -> list[str]:
        """The ulids of the jobs that wait on ``parent_ulid``, ascending."""
        return (await self._children_by_parent([parent_ulid])).get(parent_ulid, [])

    async def _children_by_parent(self, parents: list[str]) -> dict[str, list[str]]:
        """Each parent's children, ascending: one read of the edges per 900 parents
        (under SQLite's 999-variable limit); builds no model."""
        children: dict[str, set[str]] = {}
        for off in range(0, len(parents), 900):
            for edge in (
                await self._dependency_edges.filter(
                    F("parent_ulid").in_list(parents[off : off + 900])
                )
                .select("parent_ulid", "child_ulid")
                .as_dicts()
            ):
                children.setdefault(edge["parent_ulid"], set()).add(edge["child_ulid"])
        return {parent: sorted(kids) for parent, kids in children.items()}

    async def _resolve_dependencies(self, completed_ulid: str) -> None:
        """Decrement pending_dep_count for jobs depending on the completed job.

        One transaction (a savepoint inside the caller's): every child is decremented,
        or none is.
        """
        jobs = self.jobs
        async with self.db.transaction():
            children = await self._children_of(completed_ulid)

            now = now_epoch()
            for child_ulid in children:
                await jobs.filter(
                    (F("ulid") == child_ulid)
                    & (F("status") == JobStatus.PENDING.value)
                    & (F("pending_dep_count") > 0)
                ).update_one(
                    pending_dep_count=F("pending_dep_count") - 1,
                    updated_at=now,
                )

    async def _cascade_cancel_dependents(self, failed_ulid: str) -> list[str]:
        """Cancel PENDING jobs that depend on a failed/cancelled job, recursively.

        One transaction (a savepoint inside the caller's): the whole cascade is
        cancelled, or none of it. Returns the ULIDs it cancelled, in order, so the
        caller notifies waiters only after its own commit.
        """
        jobs = self.jobs
        now = now_epoch()
        cancelled_ulids: list[str] = []
        async with self.db.transaction():
            # A level at a time: one read for the edges of every job on the level, then
            # its parents in order, each one's children ascending, as a job-by-job walk
            # would go. A child is tried once, under the first parent that reaches it.
            level = [failed_ulid]
            tried: set[str] = set()
            while level:
                children = await self._children_by_parent(level)
                next_level: list[str] = []
                for parent_ulid in level:
                    for child_ulid in children.get(parent_ulid, []):
                        if child_ulid in tried:
                            continue
                        tried.add(child_ulid)
                        updated = await jobs.filter(
                            (F("ulid") == child_ulid) & (F("status") == JobStatus.PENDING.value)
                        ).update_one(
                            status=JobStatus.CANCELLED.value,
                            last_error=f"Dependency {parent_ulid} failed/cancelled",
                            finished_at=now,
                            updated_at=now,
                        )
                        if updated is not None:
                            cancelled_ulids.append(child_ulid)
                            next_level.append(child_ulid)
                level = next_level
        return cancelled_ulids

    async def cancel_job(self, job: Job) -> bool:
        """Cancel a job and cascade-cancel its dependents.

        A PENDING job and its pending dependents are cancelled in one transaction; a
        RUNNING job gets a cooperative cancellation request. Waiters are notified after
        the commit, and only for what was committed.
        """
        now = now_epoch()
        async with self.db.transaction():
            updated = await self.jobs.filter(
                (F("ulid") == job.ulid) & (F("status") == JobStatus.PENDING.value)
            ).update_one(
                status=JobStatus.CANCELLED.value,
                finished_at=now,
                updated_at=now,
            )
            cancelled = (
                await self._cascade_cancel_dependents(job.ulid) if updated is not None else []
            )

        if updated is None:
            # Not PENDING: if RUNNING, request cooperative cancellation instead
            return await job.request_cancel()

        job._sync_from(updated)
        _notify_fire(job.ulid)
        for child_ulid in cancelled:
            _notify_fire(child_ulid)
        return True

    async def replay_job(self, job: Job, *, queue_name: str | None = None) -> Job | None:
        """Replay a FAILED DLQ job back to its original queue.

        Resets the job to PENDING with retry_count=0 and clears the DLQ fields.
        Returns the updated Job, or None if the job wasn't FAILED.
        Raises UniqueKeyConflictError, and leaves the job FAILED, while another job
        with its ``unique_key`` is pending or running.

        Args:
            job: The failed job to replay.
            queue_name: Override queue to replay into. Defaults to job.original_queue.
        """
        target_queue = queue_name or job.original_queue
        if not target_queue:
            target_queue = "default"

        now = now_epoch()
        try:
            updated = await self.jobs.filter(
                (F("ulid") == job.ulid) & (F("status") == JobStatus.FAILED.value)
            ).update_one(**failed_to_pending_fields(now, target_queue=target_queue))
        except UniqueConstraintError as err:
            raise await unique_key_conflict(job, err) from err
        return updated

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    async def _check_rate_limits(self, job: Job, worker_id: str, now_ts: int) -> bool:
        """Check rate limits for a claimed job. Returns True if rate-limited.

        Checks queue-level rate limit first, then task-level. If either limit
        is exceeded, requeues the job with a delayed ETA.
        """
        # Queue-level rate limit
        queue_rate = self._rate_limits.get(job.queue_name)
        if queue_rate is not None:
            allowed = await try_acquire(f"queue:{job.queue_name}", queue_rate, db=self.db)
            if not allowed:
                await self._requeue_rate_limited(job, worker_id, queue_rate, now_ts)
                return True

        # Task-level rate limit
        task_wrapper = self._tasks.get(job.task)
        if task_wrapper is not None and task_wrapper.rate_spec is not None:
            allowed = await try_acquire(f"task:{job.task}", task_wrapper.rate_spec, db=self.db)
            if not allowed:
                await self._requeue_rate_limited(job, worker_id, task_wrapper.rate_spec, now_ts)
                return True

        return False

    async def _requeue_rate_limited(
        self, job: Job, worker_id: str, rate: RateSpec, now_ts: int
    ) -> None:
        """Requeue a rate-limited job back to PENDING with a delayed ETA."""
        delay = max(1, int(1.0 / rate.refill_rate)) if rate.refill_rate > 0 else 1
        await self.jobs.filter(
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
        attempt_id: str | None,
        status: str,
        finished_at: int,
        *,
        error: str | None = None,
        traceback: str | None = None,
        failure_kind: str | None = None,
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

        await self.attempts.filter(
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
            groups = await grouped_count(self.jobs.group_by("queue_name", "status"))
            return [(g["queue_name"], g["status"], g["count"]) for g in groups]
        except Exception:
            # A scrape must not fail, but the reason it came back empty is logged.
            logger.warning("qler queue depth query failed; reporting no depth", exc_info=True)
            return []

    # ------------------------------------------------------------------
    # Archival
    # ------------------------------------------------------------------

    async def archive_jobs(self, older_than_seconds: int = 300) -> int:
        """Move terminal jobs older than threshold to archive table.

        Returns number of jobs archived.
        """
        await self._lazy_init()
        threshold = now_epoch() - older_than_seconds

        # A job with no finished_at reads NULL, and NULL < threshold matches nothing.
        archivable = self.jobs.filter(
            F("status").in_list(list(TERMINAL_STATUSES)) & (F("finished_at") < threshold)
        )

        async with self.db.transaction():
            moved = await archivable.distinct_values("ulid")
            if not moved:
                return 0
            await archivable.copy_to(ARCHIVE_TABLE)

            # Remove the edges that name an archived job as parent or child
            edges = self._dependency_edges
            chunk = 499  # two lists of the chunk per statement: under SQLite's old 999 limit
            for offset in range(0, len(moved), chunk):
                part = moved[offset : offset + chunk]
                await edges.filter(
                    F("parent_ulid").in_list(part) | F("child_ulid").in_list(part)
                ).delete()

            count = await archivable.delete()

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
        query = self.archive
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

    async def pool_health(self) -> dict[str, Any]:
        """Report connection pool state for monitoring."""
        await self._lazy_init()
        stats = self.db.pool_stats()
        return {
            "pool_size": stats.max_connections,
            "available": stats.available_connections,
            "healthy": stats.available_connections == stats.max_connections,
        }

    async def archive_count(self) -> int:
        """Return total number of archived jobs."""
        await self._lazy_init()
        return await self.archive.count()

    async def close(self) -> None:
        """Close the database connection if we own it."""
        if self._owns_db and self._db is not None:
            await self._db.close()
            self._db = None
            self._initialized = False


def _calculate_retry_eta(job: Job) -> int:
    """Calculate the next retry ETA with exponential backoff + jitter."""
    base_delay = job.retry_delay
    delay = min(base_delay * (2**job.retry_count), _MAX_RETRY_DELAY)
    # Add 10% jitter (non-cryptographic, intentional)
    jitter = delay * 0.1 * random.random()
    return now_epoch() + int(delay + jitter)
