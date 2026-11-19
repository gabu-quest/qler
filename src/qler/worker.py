"""Worker — claims jobs from the queue, executes tasks, manages leases."""

from __future__ import annotations

import asyncio
import inspect
import json
import logging
import os
import pathlib
import platform
import signal
import socket
import time
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from qler._context import _current_job, _current_queue
from qler._time import generate_ulid, now_epoch
from qler.enums import FailureKind, JobStatus
from qler.exceptions import ConfigurationError
from qler.lifecycle import emit as _lifecycle
from qler.models.job import Job

try:
    from logler.context import correlation_context
except ImportError:

    @contextmanager
    def correlation_context(correlation_id: str):  # type: ignore[misc]
        yield correlation_id


logger = logging.getLogger("qler.worker")

# After the shutdown cuts the jobs still in their task function, how long it
# waits for the cut tasks to end before it leaves them and returns. A task that
# swallows the cancel and keeps going would otherwise hold the worker forever.
SHUTDOWN_CUT_GRACE = 5.0

# Shortest sleep of the lease-renewal loop, so a renewal that keeps failing is
# retried a few times a second rather than in a busy loop.
_RENEWAL_MIN_SLEEP = 0.25


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
        health_port: int | None = None,
        health_socket: str | None = None,
        archive_interval: float | None = None,
        archive_after: int = 300,
        memory_limit_mb: int | None = None,
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
        if health_port is not None and health_socket is not None:
            raise ConfigurationError(
                "health_port and health_socket are mutually exclusive"
            )
        if health_port is not None and not (1 <= health_port <= 65535):
            raise ConfigurationError(
                f"health_port must be between 1 and 65535, got {health_port}"
            )
        if archive_interval is not None and archive_interval < 1.0:
            raise ConfigurationError(
                f"archive_interval must be >= 1.0 seconds, got {archive_interval}"
            )
        if archive_after < 0:
            raise ConfigurationError(
                f"archive_after must be >= 0, got {archive_after}"
            )
        if memory_limit_mb is not None and memory_limit_mb < 1:
            raise ConfigurationError(
                f"memory_limit_mb must be >= 1, got {memory_limit_mb}"
            )

        self.queue = queue
        self.queues = queues or ["default"]
        self.concurrency = concurrency
        self.poll_interval = poll_interval
        self.lease_recovery_interval = lease_recovery_interval
        self.shutdown_timeout = shutdown_timeout
        self.health_port = health_port
        self.health_socket = health_socket
        self.archive_interval = archive_interval
        self.archive_after = archive_after
        self.memory_limit_mb = memory_limit_mb

        hostname = socket.gethostname()
        pid = os.getpid()
        self.worker_id = f"{hostname}:{pid}:{generate_ulid()}"

        self._running = False
        self._started_at = now_epoch()
        self._semaphore: Optional[asyncio.Semaphore] = None
        self._active_jobs: dict[str, Any] = {}  # ulid -> Job
        self._active_tasks: set[asyncio.Task] = set()
        self._background_tasks: list[asyncio.Task] = []
        # Kept apart from the other background tasks: _shutdown stops it only
        # after the drain, so running jobs keep their leases while they finish.
        self._renewal_task: Optional[asyncio.Task] = None
        # ulid -> task, for jobs whose task function is being awaited right now
        # (not resolving, not writing their outcome).
        self._in_task_body: dict[str, asyncio.Task] = {}
        # Set by _shutdown before it cancels a job's task; the fact that tells
        # the shutdown's cancel apart from any other in _execute_job.
        self._cut_by_shutdown: set[str] = set()
        self._left_for_recovery: list[str] = []
        self._failed_on_cut: list[str] = []

    @property
    def status(self) -> str:
        """Return 'healthy' if running, 'draining' if shutting down."""
        return "healthy" if self._running else "draining"

    def _health_response(self) -> dict[str, Any]:
        """Build the JSON health response from live instance state."""
        adapter = self.queue.db.adapter
        return {
            "status": self.status,
            "worker_id": self.worker_id,
            "uptime_seconds": now_epoch() - self._started_at,
            "active_jobs": len(self._active_jobs),
            "concurrency": self.concurrency,
            "queues": self.queues,
            "started_at": self._started_at,
            "pool": {
                "size": adapter._pool_size,
                "available": adapter._pool.qsize(),
                "healthy": adapter._pool.qsize() == adapter._pool_size,
            },
        }

    async def _handle_health_request(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        """Minimal HTTP/1.1 handler for health checks."""
        try:
            async with asyncio.timeout(5.0):
                request_line = await reader.readline()
                # Consume remaining headers (capped to prevent abuse)
                for _ in range(100):
                    line = await reader.readline()
                    if line in (b"\r\n", b"\n", b""):
                        break

            decoded = request_line.decode("utf-8", errors="replace")
            parts = decoded.strip().split()
            path = parts[1] if len(parts) >= 2 else "/"

            if path == "/health":
                body = json.dumps(self._health_response())
                response = (
                    f"HTTP/1.1 200 OK\r\n"
                    f"Content-Type: application/json\r\n"
                    f"Content-Length: {len(body)}\r\n"
                    f"Connection: close\r\n"
                    f"\r\n"
                    f"{body}"
                )
            elif path == "/metrics" and self.queue._metrics:
                # No auth required — the server is bound to 127.0.0.1 only.
                # Add token auth before exposing over a network.
                await self.queue._metrics.update_depth()
                body_bytes = self.queue._metrics.generate()
                header = (
                    b"HTTP/1.1 200 OK\r\n"
                    b"Content-Type: text/plain; version=0.0.4; charset=utf-8\r\n"
                    b"Content-Length: " + str(len(body_bytes)).encode() + b"\r\n"
                    b"Connection: close\r\n"
                    b"\r\n"
                )
                writer.write(header + body_bytes)
                await writer.drain()
                return
            else:
                body = '{"error": "not found"}'
                response = (
                    f"HTTP/1.1 404 Not Found\r\n"
                    f"Content-Type: application/json\r\n"
                    f"Content-Length: {len(body)}\r\n"
                    f"Connection: close\r\n"
                    f"\r\n"
                    f"{body}"
                )

            writer.write(response.encode())
            await writer.drain()
        except TimeoutError:
            pass  # Slow client — silently drop
        finally:
            writer.close()
            await writer.wait_closed()

    async def _health_server_loop(self) -> None:
        """Start and run the health check server.

        Keeps serving until cancelled (not until _running=False) so that
        draining status can be queried by health probes during shutdown.
        """
        server: asyncio.AbstractServer | None = None
        try:
            if self.health_socket is not None:
                # Remove stale socket from a previous crash
                pathlib.Path(self.health_socket).unlink(missing_ok=True)
                server = await asyncio.start_unix_server(
                    self._handle_health_request, path=self.health_socket
                )
                logger.info(
                    "Health endpoint listening on unix:%s", self.health_socket
                )
            else:
                server = await asyncio.start_server(
                    self._handle_health_request,
                    host="127.0.0.1",
                    port=self.health_port,
                )
                logger.info(
                    "Health endpoint listening on 127.0.0.1:%d", self.health_port
                )

            # Serve until cancelled by _shutdown(). This means the health
            # endpoint stays up during graceful drain so procler can see
            # status="draining" instead of a connection refusal.
            await asyncio.Future()  # block forever until cancelled
        except asyncio.CancelledError:
            pass
        finally:
            if server is not None:
                server.close()
                await server.wait_closed()
            if self.health_socket is not None:
                sock_path = pathlib.Path(self.health_socket)
                if sock_path.exists():
                    sock_path.unlink()

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
            self._renewal_task = asyncio.create_task(self._lease_renewal_loop())
            self._background_tasks = [
                self._renewal_task,
                asyncio.create_task(self._lease_recovery_loop()),
            ]
            if self.queue._cron_tasks:
                self._background_tasks.append(
                    asyncio.create_task(self._cron_scheduler_loop())
                )
            if self.health_port is not None or self.health_socket is not None:
                self._background_tasks.append(
                    asyncio.create_task(self._health_server_loop())
                )
            if self.archive_interval is not None:
                self._background_tasks.append(
                    asyncio.create_task(self._archival_loop())
                )
            if self.memory_limit_mb is not None:
                self._background_tasks.append(
                    asyncio.create_task(self._memory_watchdog_loop())
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
        """Main loop: acquire semaphore slots → batch claim jobs → dispatch."""
        while self._running:
            # Wait for at least one free slot
            try:
                await asyncio.wait_for(
                    self._semaphore.acquire(), timeout=self.poll_interval
                )
            except asyncio.TimeoutError:
                continue

            if not self._running:
                self._semaphore.release()
                break

            # Greedily acquire additional free slots for batch claiming
            batch_size = 1
            while batch_size < self.concurrency and not self._semaphore.locked():
                await self._semaphore.acquire()
                batch_size += 1

            try:
                jobs = await self.queue.claim_jobs(
                    self.worker_id, self.queues, n=batch_size,
                )
            except Exception:
                for _ in range(batch_size):
                    self._semaphore.release()
                logger.exception("Error claiming jobs")
                await asyncio.sleep(self.poll_interval)
                continue

            if not jobs:
                for _ in range(batch_size):
                    self._semaphore.release()
                await asyncio.sleep(self.poll_interval)
                continue

            # Release unused slots
            unused = batch_size - len(jobs)
            for _ in range(unused):
                self._semaphore.release()

            # Dispatch each claimed job
            for job in jobs:
                self._active_jobs[job.ulid] = job
                t = asyncio.create_task(self._execute_job(job))
                self._active_tasks.add(t)
                t.add_done_callback(self._active_tasks.discard)

    async def _execute_job(self, job: Any) -> None:
        """Execute a single job: resolve task, validate signature, run, complete/fail."""
        start_time = time.monotonic()
        token = _current_job.set(job)
        queue_token = _current_queue.set(self.queue)
        try:
            # 1. Resolve task
            task_wrapper = self.queue._tasks.get(job.task)
            if task_wrapper is None:
                logger.error("Task not found: %s (job %s)", job.task, job.ulid)
                await self._finish(
                    "fail",
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
                await self._finish(
                    "fail",
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
                await self._finish(
                    "fail",
                    job,
                    self.worker_id,
                    exc,
                    failure_kind=FailureKind.SIGNATURE_MISMATCH,
                )
                return

            # 4. Execute with correlation context and optional timeout
            correlation_id = job.correlation_id or job.ulid
            timeout = job.timeout
            try:
                current = asyncio.current_task()
                if current is not None:
                    self._in_task_body[job.ulid] = current
                try:
                    with correlation_context(correlation_id):
                        if task_wrapper.sync:
                            coro = asyncio.to_thread(fn, *args, **kwargs)
                        else:
                            coro = fn(*args, **kwargs)
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
                finally:
                    self._in_task_body.pop(job.ulid, None)
            except asyncio.TimeoutError:
                logger.error(
                    "Task %s timed out after %ss (job %s)",
                    job.task, timeout, job.ulid,
                )
                await self._finish(
                    "fail",
                    job,
                    self.worker_id,
                    TimeoutError(
                        f"Task {job.task} timed out after {timeout}s"
                    ),
                    failure_kind=FailureKind.TIMEOUT,
                )
                return
            except asyncio.CancelledError:
                if (
                    job.ulid in self._cut_by_shutdown
                    and not await self._cancel_was_requested(job)
                ):
                    # The worker's own shutdown cut this job, not the user. The
                    # row stays RUNNING with this worker's id and lease, so lease
                    # recovery hands it to a worker once the lease ends.
                    self._left_for_recovery.append(job.ulid)
                    logger.warning(
                        "Shutdown stopped task %s (job %s) before it finished; "
                        "left it running for lease recovery",
                        job.task, job.ulid,
                    )
                    raise
                if job.ulid in self._cut_by_shutdown:
                    self._failed_on_cut.append(job.ulid)
                logger.warning(
                    "Task %s cancelled (job %s), marking failed",
                    job.task, job.ulid,
                )
                await self._finish(
                    "fail",
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
                await self._finish(
                    "fail",
                    job,
                    self.worker_id,
                    exc,
                    failure_kind=FailureKind.EXCEPTION,
                )
                return

            # 5. Complete
            await self._finish("complete", job, self.worker_id, result=result)

        finally:
            duration = time.monotonic() - start_time
            _lifecycle(
                "job.executed", job_id=job.ulid, queue=job.queue_name,
                task=job.task, correlation_id=job.correlation_id or job.ulid,
                duration=round(duration, 4),
            )
            if self.queue._metrics:
                self.queue._metrics.observe_duration(
                    job.queue_name, job.task, duration
                )
            _current_job.reset(token)
            _current_queue.reset(queue_token)
            self._active_jobs.pop(job.ulid, None)
            if self._semaphore is not None:
                self._semaphore.release()

    async def _finish(self, which: str, job: Any, *args: Any, **kwargs: Any) -> None:
        """Run the queue's complete_job or fail_job for a job; log it if it raises.

        `which` is "complete" or "fail". The finish is one write, so when it raises
        nothing was written: the job stays RUNNING with this worker's id and lease, and
        lease recovery runs it again once the lease ends. The worker logs it and carries
        on; the database's busy timeout is the retry. CancelledError is not caught.
        """
        finish = self.queue.complete_job if which == "complete" else self.queue.fail_job
        try:
            await finish(job, *args, **kwargs)
        except Exception:
            logger.error(
                "Could not %s job %s (task %s); left it running for lease recovery",
                which, job.ulid, job.task, exc_info=True,
            )

    async def _cancel_was_requested(self, job: Any) -> bool:
        """Return True if the user has asked to cancel this job (cancel_requested)."""
        from sqler import F

        row = await Job.query().filter(F("ulid") == job.ulid).first()
        return bool(row is not None and row.cancel_requested)

    @staticmethod
    def _renewal_due(job: Any) -> float:
        """Epoch second at which a job's lease is renewed: half its lease_duration before it ends."""
        return job.lease_expires_at - job.lease_duration / 2

    async def _lease_renewal_loop(self) -> None:
        """Background task: renew each active job's lease before it can end.

        Runs until _shutdown cancels it, which it does only after the drain.
        Each pass renews the jobs that are due, then sleeps until the earliest
        due moment among the active jobs, and never longer than
        default_lease_duration / 3. That cap covers a job claimed while the loop
        sleeps: a claim leases for default_lease_duration, so the loop sees the
        job while two thirds of its claim lease remain and from then on wakes
        by the job's own due moment.
        """
        while True:
            for ulid, job in list(self._active_jobs.items()):
                if job.lease_expires_at is None:
                    continue
                if time.time() < self._renewal_due(job):
                    continue
                try:
                    renewed = await job.renew_lease()
                except Exception as exc:
                    # Per job: the others are still renewed this pass, and this
                    # one is tried again on the next.
                    logger.warning(
                        "Lease renewal failed for job %s: %s: %s; "
                        "trying again on the next pass",
                        ulid, type(exc).__name__, exc,
                    )
                    continue
                if not renewed:
                    logger.warning(
                        "Lost ownership of job %s during renewal", ulid
                    )
                    self._active_jobs.pop(ulid, None)

            wake = time.time() + self.queue.default_lease_duration / 3
            for job in list(self._active_jobs.values()):
                if job.lease_expires_at is not None:
                    wake = min(wake, self._renewal_due(job))
            await asyncio.sleep(max(wake - time.time(), _RENEWAL_MIN_SLEEP))

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

    async def _archival_loop(self) -> None:
        """Background task: periodically archive terminal jobs."""
        while self._running:
            await asyncio.sleep(self.archive_interval)
            if not self._running:
                break
            try:
                count = await self.queue.archive_jobs(
                    older_than_seconds=self.archive_after
                )
                if count:
                    logger.info("Archived %d terminal jobs", count)
            except Exception:
                logger.exception("Error in archival loop")

    async def _memory_watchdog_loop(self) -> None:
        """Background task: log warnings and trigger archival when RSS exceeds limit."""
        while self._running:
            await asyncio.sleep(30)
            if not self._running:
                break
            try:
                try:
                    import resource

                    rss_raw = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
                    if platform.system() == "Darwin":
                        rss_mb = rss_raw / (1024 * 1024)  # macOS: bytes -> MB
                    else:
                        rss_mb = rss_raw / 1024  # Linux: KB -> MB
                except ImportError:
                    continue  # resource module unavailable on Windows
                if rss_mb > self.memory_limit_mb:
                    logger.warning(
                        "Memory watchdog: RSS %.0f MB exceeds limit %d MB, "
                        "triggering emergency archival",
                        rss_mb,
                        self.memory_limit_mb,
                    )
                    count = await self.queue.archive_jobs(older_than_seconds=0)
                    if count:
                        logger.info(
                            "Memory watchdog: archived %d jobs", count
                        )
            except Exception:
                logger.exception("Error in memory watchdog loop")

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
                            if cw.schedule.catchup_latest:
                                # Need all runs to pick the most recent
                                missed = cw.missed_runs(anchor, now_ts)
                                missed = missed[-1:]
                            else:
                                missed = cw.missed_runs(
                                    anchor, now_ts,
                                    limit=cw.schedule.catchup,
                                )
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
        """Graceful shutdown: stop background tasks, drain active jobs, then stop renewal."""
        logger.info("Worker shutting down...")

        # Cancel background tasks, all but lease renewal: the jobs still
        # running keep their leases until the drain is over, so no other
        # worker recovers and starts a job this worker is still running.
        renewal = self._renewal_task
        others = [t for t in self._background_tasks if t is not renewal]
        for t in others:
            t.cancel()
        if others:
            await asyncio.gather(*others, return_exceptions=True)

        try:
            await self._drain()
        finally:
            if renewal is not None:
                renewal.cancel()
                await asyncio.gather(renewal, return_exceptions=True)
            self._renewal_task = None
            self._background_tasks.clear()

        # Remove signal handlers
        loop = asyncio.get_running_loop()
        for sig in self._original_handlers:
            try:
                loop.remove_signal_handler(sig)
            except (NotImplementedError, OSError, RuntimeError):
                pass

        logger.info("Worker shutdown complete")

    async def _drain(self) -> None:
        """Wait for active jobs; cut those still running after shutdown_timeout.

        After the cut, wait at most SHUTDOWN_CUT_GRACE for the cut tasks to end.
        A task still going then (it swallowed the cancel) is logged by job id
        and left; its row stays RUNNING for lease recovery.
        """
        if not self._active_tasks:
            return
        _done, pending = await asyncio.wait(
            self._active_tasks, timeout=self.shutdown_timeout
        )
        # Cut only the jobs still inside their task function. A job that is
        # writing its outcome is left to finish the write, so no row is
        # left half recorded. No await between the wait and the cuts, so
        # _in_task_body is the state at the moment the timeout passed.
        cut: dict[asyncio.Task, str] = {}
        for ulid, t in list(self._in_task_body.items()):
            if t in pending:
                self._cut_by_shutdown.add(ulid)
                cut[t] = ulid
                t.cancel()
        if not pending:
            return
        _ended, still_going = await asyncio.wait(
            pending, timeout=SHUTDOWN_CUT_GRACE
        )
        not_stopped = sorted(cut.get(t, "<writing its outcome>") for t in still_going)
        logger.warning(
            "Shutdown timeout of %ss passed with %d job(s) unfinished: "
            "left %d running for lease recovery, failed %d as cancelled "
            "because their cancel was requested",
            self.shutdown_timeout, len(pending),
            len(self._left_for_recovery), len(self._failed_on_cut),
        )
        if not_stopped:
            logger.warning(
                "%d job(s) did not stop within the %ss grace after the cut and "
                "were left running for lease recovery: %s",
                len(not_stopped), SHUTDOWN_CUT_GRACE, ", ".join(not_stopped),
            )
