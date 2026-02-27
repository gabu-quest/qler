"""Celery + Redis harness for comparison benchmarks.

Provides Redis availability detection, DB cleanup, and a context manager
that starts/stops a Celery worker subprocess for the benchmark duration.
"""

from __future__ import annotations

import asyncio
import os
import signal
import subprocess
import sys
import time


REDIS_URL = "redis://localhost:6379/15"
WORKER_STARTUP_TIMEOUT = 15  # seconds
WORKER_SHUTDOWN_TIMEOUT = 5  # seconds


def redis_available(url: str = REDIS_URL) -> bool:
    """Check if Redis is reachable with a 2s timeout."""
    try:
        import redis

        r = redis.Redis.from_url(url, socket_connect_timeout=2)
        return r.ping()
    except Exception:
        return False


def flush_redis(url: str = REDIS_URL) -> None:
    """FLUSHDB on the benchmark Redis database (DB 15)."""
    import redis

    r = redis.Redis.from_url(url, socket_connect_timeout=2)
    r.flushdb()


class CeleryWorkerContext:
    """Context manager that starts and stops a Celery worker subprocess.

    Usage:
        async with CeleryWorkerContext() as ctx:
            # worker is ready, run benchmarks
            ...
    """

    def __init__(self, concurrency: int = 1):
        self.concurrency = concurrency
        self._proc: subprocess.Popen | None = None

    async def __aenter__(self) -> CeleryWorkerContext:
        env = os.environ.copy()
        # Suppress Celery banner noise
        env["C_FORCE_ROOT"] = "1"

        self._proc = subprocess.Popen(
            [
                sys.executable, "-m", "celery",
                "-A", "benchmarks.celery_app",
                "worker",
                "--pool", "solo",
                "--concurrency", str(self.concurrency),
                "--without-heartbeat",
                "--without-mingle",
                "--without-gossip",
                "--loglevel", "WARNING",
                "-Q", "celery",
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            env=env,
        )

        # Wait for the worker to become responsive
        await self._wait_for_ready()
        return self

    async def __aexit__(self, *exc) -> None:
        if self._proc and self._proc.poll() is None:
            self._proc.send_signal(signal.SIGTERM)
            try:
                self._proc.wait(timeout=WORKER_SHUTDOWN_TIMEOUT)
            except subprocess.TimeoutExpired:
                self._proc.kill()
                self._proc.wait(timeout=2)
        self._proc = None

    async def _wait_for_ready(self) -> None:
        """Poll Celery control.ping() until a worker responds."""
        from benchmarks.celery_app import app

        deadline = time.monotonic() + WORKER_STARTUP_TIMEOUT
        while time.monotonic() < deadline:
            if self._proc and self._proc.poll() is not None:
                raise RuntimeError(
                    f"Celery worker exited during startup (code={self._proc.returncode})"
                )
            try:
                response = app.control.ping(timeout=1)
                if response:
                    return
            except Exception:
                pass
            await asyncio.sleep(0.3)

        raise TimeoutError(
            f"Celery worker not ready after {WORKER_STARTUP_TIMEOUT}s"
        )
