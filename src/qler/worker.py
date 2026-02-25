"""Worker — claims jobs from the queue, executes tasks, manages leases."""

from __future__ import annotations

import asyncio
import inspect
import json
import logging
import os
import pathlib
import signal
import socket
import time
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from qler._context import _current_job
from qler.lifecycle import emit as _lifecycle
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
        health_port: int | None = None,
        health_socket: str | None = None,
        archive_interval: float | None = None,
        archive_after: int = 300,
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

        hostname = socket.gethostname()
        pid = os.getpid()
        self.worker_id = f"{hostname}:{pid}:{generate_ulid()}"

        self._running = False
        self._started_at = now_epoch()
        self._semaphore: Optional[asyncio.Semaphore] = None
        self._active_jobs: dict[str, Any] = {}  # ulid -> Job
        self._active_tasks: set[asyncio.Task] = set()
        self._background_tasks: list[asyncio.Task] = []

    @property
    def status(self) -> str:
        """Return 'healthy' if running, 'draining' if shutting down."""
        return "healthy" if self._running else "draining"

    def _health_response(self) -> dict[str, Any]:
        """Build the JSON health response from live instance state."""
        return {
            "status": self.status,
            "worker_id": self.worker_id,
            "uptime_seconds": now_epoch() - self._started_at,
            "active_jobs": len(self._active_jobs),
            "concurrency": self.concurrency,
            "queues": self.queues,
            "started_at": self._started_at,
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
            self._background_tasks = [
                asyncio.create_task(self._lease_renewal_loop()),
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
        start_time = time.monotonic()
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

            # 4. Execute with correlation context and optional timeout
            correlation_id = job.correlation_id or job.ulid
            timeout = job.timeout
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
            except asyncio.TimeoutError:
                logger.error(
                    "Task %s timed out after %ss (job %s)",
                    job.task, timeout, job.ulid,
                )
                await self.queue.fail_job(
                    job,
                    self.worker_id,
                    TimeoutError(
                        f"Task {job.task} timed out after {timeout}s"
                    ),
                    failure_kind=FailureKind.TIMEOUT,
                )
                return
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
