"""Worker — claims jobs from the queue, executes tasks, manages leases."""

from __future__ import annotations

import asyncio
import inspect
import json
import logging
import os
import signal
import socket
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from qler._context import _current_job
from qler._time import generate_ulid, now_epoch
from qler.enums import FailureKind, JobStatus
from qler.exceptions import ConfigurationError
from qler.models.job import Job

try:
    from logler.context import correlation_context
except ImportError:

    @contextmanager
    def correlation_context(correlation_id: str):  # type: ignore[misc]
        yield correlation_id


logger = logging.getLogger("qler.worker")


class Worker:
    """Async worker that claims and executes jobs from one or more queues.

    The worker runs a claim loop gated by a semaphore for concurrency control,
    with background tasks for lease renewal and expired lease recovery.
    """

    def __init__(
        self,
        queue: Any,
        *,
        queues: list[str] | None = None,
        concurrency: int = 1,
        poll_interval: float = 1.0,
        lease_recovery_interval: float = 60.0,
        shutdown_timeout: float = 30.0,
    ) -> None:
        if queues is not None and len(queues) == 0:
            raise ConfigurationError("queues list must not be empty")
        if concurrency < 1:
            raise ConfigurationError(
                f"concurrency must be >= 1, got {concurrency}"
            )
        if shutdown_timeout <= 0:
            raise ConfigurationError(
                f"shutdown_timeout must be > 0, got {shutdown_timeout}"
            )

        self.queue = queue
        self.queues = queues or ["default"]
        self.concurrency = concurrency
        self.poll_interval = poll_interval
        self.lease_recovery_interval = lease_recovery_interval
        self.shutdown_timeout = shutdown_timeout

        hostname = socket.gethostname()
        pid = os.getpid()
        self.worker_id = f"{hostname}:{pid}:{generate_ulid()}"

        self._running = False
        self._semaphore: Optional[asyncio.Semaphore] = None
        self._active_jobs: dict[str, Any] = {}  # ulid -> Job
        self._active_tasks: set[asyncio.Task] = set()
        self._background_tasks: list[asyncio.Task] = []

    async def run(self) -> None:
        """Start the worker loop. Blocks until shutdown."""
        await self.queue.init_db()
        self._running = True
        self._semaphore = asyncio.Semaphore(self.concurrency)

        # Install signal handlers (may fail in non-main thread / pytest)
        loop = asyncio.get_running_loop()
        self._original_handlers: dict[int, Any] = {}
        for sig in (signal.SIGTERM, signal.SIGINT):
            try:
                old = loop.add_signal_handler(sig, self._signal_handler)
                self._original_handlers[sig] = old
            except (NotImplementedError, OSError, RuntimeError):
                pass

        try:
            # Initial lease recovery
            recovered = await self.queue.recover_expired_leases()
            if recovered:
                logger.info("Recovered %d expired leases on startup", recovered)

            # Start background tasks
            self._background_tasks = [
                asyncio.create_task(self._lease_renewal_loop()),
                asyncio.create_task(self._lease_recovery_loop()),
            ]
            if self.queue._cron_tasks:
                self._background_tasks.append(
                    asyncio.create_task(self._cron_scheduler_loop())
                )

            # Main claim loop
            await self._claim_loop()
        finally:
            await self._shutdown()

    def _signal_handler(self) -> None:
        """Handle SIGTERM/SIGINT — set running to False for graceful drain."""
        logger.info("Received shutdown signal, draining...")
        self._running = False

    async def _claim_loop(self) -> None:
        """Main loop: acquire semaphore → claim job → dispatch execution."""
        while self._running:
            # Use timeout on acquire so _running=False can exit the loop
            # even when all concurrency slots are occupied by active jobs.
            try:
                await asyncio.wait_for(
                    self._semaphore.acquire(), timeout=self.poll_interval
                )
            except asyncio.TimeoutError:
                continue

            if not self._running:
                self._semaphore.release()
                break

            try:
                job = await self.queue.claim_job(self.worker_id, self.queues)
            except Exception:
                self._semaphore.release()
                logger.exception("Error claiming job")
                await asyncio.sleep(self.poll_interval)
                continue

            if job is None:
                self._semaphore.release()
                await asyncio.sleep(self.poll_interval)
                continue

            # Dispatch execution
            self._active_jobs[job.ulid] = job
            t = asyncio.create_task(self._execute_job(job))
            self._active_tasks.add(t)
            t.add_done_callback(self._active_tasks.discard)

    async def _execute_job(self, job: Any) -> None:
        """Execute a single job: resolve task, validate signature, run, complete/fail."""
        token = _current_job.set(job)
        try:
            # 1. Resolve task
            task_wrapper = self.queue._tasks.get(job.task)
            if task_wrapper is None:
                logger.error("Task not found: %s (job %s)", job.task, job.ulid)
                await self.queue.fail_job(
                    job,
                    self.worker_id,
                    failure_kind=FailureKind.TASK_NOT_FOUND,
                )
                return

            # 2. Parse payload
            try:
                payload = json.loads(job.payload_json)
                args = payload.get("args", [])
                kwargs = payload.get("kwargs", {})
            except (json.JSONDecodeError, TypeError, KeyError, AttributeError) as exc:
                logger.error("Invalid payload for job %s: %s", job.ulid, exc)
                await self.queue.fail_job(
                    job,
                    self.worker_id,
                    exc,
                    failure_kind=FailureKind.PAYLOAD_INVALID,
                )
                return

            # 3. Signature check (separates TypeError from sig vs body)
            fn = task_wrapper.fn
            try:
                inspect.signature(fn).bind(*args, **kwargs)
            except TypeError as exc:
                logger.error(
                    "Signature mismatch for %s (job %s): %s",
                    job.task, job.ulid, exc,
                )
                await self.queue.fail_job(
                    job,
                    self.worker_id,
                    exc,
                    failure_kind=FailureKind.SIGNATURE_MISMATCH,
                )
                return

            # 4. Execute with correlation context
            correlation_id = job.correlation_id or job.ulid
            try:
                with correlation_context(correlation_id):
                    if task_wrapper.sync:
                        result = await asyncio.to_thread(fn, *args, **kwargs)
                    else:
                        result = await fn(*args, **kwargs)
            except asyncio.CancelledError:
                logger.warning(
                    "Task %s cancelled (job %s), marking failed",
                    job.task, job.ulid,
                )
                await self.queue.fail_job(
                    job,
                    self.worker_id,
                    failure_kind=FailureKind.CANCELLED,
                )
                raise
            except Exception as exc:
                logger.error(
                    "Task %s failed (job %s): %s",
                    job.task, job.ulid, exc,
                )
                await self.queue.fail_job(
                    job,
                    self.worker_id,
                    exc,
                    failure_kind=FailureKind.EXCEPTION,
                )
                return

            # 5. Complete
            await self.queue.complete_job(job, self.worker_id, result=result)

        finally:
            _current_job.reset(token)
            self._active_jobs.pop(job.ulid, None)
            if self._semaphore is not None:
                self._semaphore.release()

    async def _lease_renewal_loop(self) -> None:
        """Background task: periodically renew leases for active jobs."""
        while self._running:
            interval = max(self.queue.default_lease_duration / 3, 1.0)
            await asyncio.sleep(interval)

            for ulid, job in list(self._active_jobs.items()):
                if not self._running:
                    break
                now = now_epoch()
                if (
                    job.lease_expires_at is not None
                    and job.lease_expires_at - now < job.lease_duration / 3
                ):
                    renewed = await job.renew_lease()
                    if not renewed:
                        logger.warning(
                            "Lost ownership of job %s during renewal", ulid
                        )
                        self._active_jobs.pop(ulid, None)

    async def _lease_recovery_loop(self) -> None:
        """Background task: periodically scan for expired leases."""
        while self._running:
            await asyncio.sleep(self.lease_recovery_interval)
            if not self._running:
                break
            try:
                recovered = await self.queue.recover_expired_leases()
                if recovered:
                    logger.info("Recovered %d expired leases", recovered)
            except Exception:
                logger.exception("Error in lease recovery loop")

    async def _cron_scheduler_loop(self) -> None:
        """Background task: enqueue cron jobs at their scheduled times.

        For each registered cron task, checks if the next scheduled run is due.
        Uses idempotency keys to prevent duplicate scheduling and max_running
        guards to prevent pile-up.

        On startup, catchup-enabled tasks query their last enqueued timestamp
        and walk forward to enqueue missed runs (up to their catchup limit).
        """
        from sqler import F

        # One-time startup: query last enqueued timestamp for catchup tasks
        catchup_anchors: dict[str, int | None] = {}
        for path, cw in self.queue._cron_tasks.items():
            if cw.schedule.catchup > 0:
                try:
                    catchup_anchors[path] = await cw._find_last_enqueued_ts()
                except Exception:
                    logger.exception("Error querying catchup anchor for %s", path)

        while self._running:
            now = datetime.now(timezone.utc)
            now_ts = int(now.timestamp())

            for path, cw in self.queue._cron_tasks.items():
                if not self._running:
                    break
                try:
                    # --- Catchup pass (first tick only for catchup-enabled tasks) ---
                    if path in catchup_anchors:
                        anchor = catchup_anchors.pop(path)
                        if anchor is not None:
                            missed = cw.missed_runs(anchor, now_ts)
                            # Trim to catchup limit
                            missed = missed[:cw.schedule.catchup]
                            for ts in missed:
                                # max_running guard
                                if cw.schedule.max_running > 0:
                                    running_count = await Job.query().filter(
                                        (F("task") == path)
                                        & F("status").in_list([
                                            JobStatus.PENDING.value,
                                            JobStatus.RUNNING.value,
                                        ])
                                    ).count()
                                    if running_count >= cw.schedule.max_running:
                                        break
                                await cw.task_wrapper.enqueue(
                                    _idempotency_key=cw.idempotency_key(ts),
                                )
                        # After catchup (or no history), fall through to normal scheduling

                    # --- Normal scheduling (60s lookback) ---
                    next_run = cw.next_run(after=now - timedelta(seconds=60))
                    next_ts = int(next_run.timestamp())
                    if next_ts > now_ts:
                        continue

                    # max_running guard: check pending/running count
                    if cw.schedule.max_running > 0:
                        running_count = await Job.query().filter(
                            (F("task") == path)
                            & F("status").in_list([
                                JobStatus.PENDING.value,
                                JobStatus.RUNNING.value,
                            ])
                        ).count()
                        if running_count >= cw.schedule.max_running:
                            continue

                    # Enqueue with idempotency key to prevent duplicates
                    await cw.task_wrapper.enqueue(
                        _idempotency_key=cw.idempotency_key(next_ts),
                    )
                except Exception:
                    logger.exception("Error scheduling cron task %s", path)

            await asyncio.sleep(self.poll_interval)

    async def _shutdown(self) -> None:
        """Graceful shutdown: cancel background tasks, wait for active jobs."""
        logger.info("Worker shutting down...")

        # Cancel background tasks
        for t in self._background_tasks:
            t.cancel()
        if self._background_tasks:
            await asyncio.gather(*self._background_tasks, return_exceptions=True)
        self._background_tasks.clear()

        # Wait for active job tasks to finish
        if self._active_tasks:
            _done, pending = await asyncio.wait(
                self._active_tasks, timeout=self.shutdown_timeout
            )
            for t in pending:
                t.cancel()
            if pending:
                await asyncio.gather(*pending, return_exceptions=True)
                logger.warning(
                    "Cancelled %d jobs that didn't finish within timeout", len(pending)
                )

        # Remove signal handlers
        loop = asyncio.get_running_loop()
        for sig in self._original_handlers:
            try:
                loop.remove_signal_handler(sig)
            except (NotImplementedError, OSError, RuntimeError):
                pass

        logger.info("Worker shutdown complete")
