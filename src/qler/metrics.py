"""Prometheus metrics for qler. Created by Queue when metrics=True."""

from __future__ import annotations

import logging
from typing import Any, Awaitable, Callable, Optional

logger = logging.getLogger("qler.metrics")

_DURATION_BUCKETS = (0.1, 0.25, 0.5, 1, 2.5, 5, 10, 30, 60, 120, 300, 600)


class QlerMetrics:
    """Prometheus metrics for qler.

    Created by Queue when ``metrics=True`` or a custom ``CollectorRegistry``
    is passed.  All metric objects live on the supplied (or auto-created)
    registry so multiple Queue instances don't collide.
    """

    def __init__(self, registry: Any = None) -> None:
        from prometheus_client import CollectorRegistry, Counter, Gauge, Histogram

        self.registry: CollectorRegistry = registry or CollectorRegistry()

        self.jobs_enqueued = Counter(
            "qler_jobs_enqueued_total",
            "Total jobs enqueued",
            ["queue", "task"],
            registry=self.registry,
        )
        self.jobs_claimed = Counter(
            "qler_jobs_claimed_total",
            "Total jobs claimed by workers",
            ["queue", "task"],
            registry=self.registry,
        )
        self.jobs_completed = Counter(
            "qler_jobs_completed_total",
            "Total jobs completed successfully",
            ["queue", "task"],
            registry=self.registry,
        )
        self.jobs_failed = Counter(
            "qler_jobs_failed_total",
            "Total jobs that permanently failed",
            ["queue", "task", "failure_kind"],
            registry=self.registry,
        )
        self.jobs_retried = Counter(
            "qler_jobs_retried_total",
            "Total jobs scheduled for retry",
            ["queue", "task"],
            registry=self.registry,
        )
        self.leases_recovered = Counter(
            "qler_leases_recovered_total",
            "Total expired leases recovered",
            registry=self.registry,
        )
        self.job_duration = Histogram(
            "qler_job_duration_seconds",
            "Job execution wall-clock time",
            ["queue", "task"],
            buckets=_DURATION_BUCKETS,
            registry=self.registry,
        )
        self.queue_depth = Gauge(
            "qler_queue_depth",
            "Number of jobs per queue and status",
            ["queue", "status"],
            registry=self.registry,
        )

        self._depth_query_fn: Optional[Callable[[], Awaitable[list[tuple[str, str, int]]]]] = None

    def set_depth_query(self, fn: Callable[[], Awaitable[list[tuple[str, str, int]]]]) -> None:
        """Set the async depth query function called before each /metrics scrape."""
        self._depth_query_fn = fn

    def inc_enqueued(self, queue: str, task: str) -> None:
        self.jobs_enqueued.labels(queue=queue, task=task).inc()

    def inc_claimed(self, queue: str, task: str) -> None:
        self.jobs_claimed.labels(queue=queue, task=task).inc()

    def inc_completed(self, queue: str, task: str) -> None:
        self.jobs_completed.labels(queue=queue, task=task).inc()

    def inc_failed(self, queue: str, task: str, failure_kind: str) -> None:
        self.jobs_failed.labels(queue=queue, task=task, failure_kind=failure_kind).inc()

    def inc_retried(self, queue: str, task: str) -> None:
        self.jobs_retried.labels(queue=queue, task=task).inc()

    def inc_leases_recovered(self, count: int = 1) -> None:
        self.leases_recovered.inc(count)

    def observe_duration(self, queue: str, task: str, seconds: float) -> None:
        self.job_duration.labels(queue=queue, task=task).observe(seconds)

    async def update_depth(self) -> None:
        """Refresh queue depth gauge from DB. Call before generate()."""
        if self._depth_query_fn is None:
            return
        try:
            rows = await self._depth_query_fn()
        except Exception:
            logger.warning("queue depth scrape failed", exc_info=True)
            return
        # Clear stale labels, then set current values
        self.queue_depth._metrics.clear()
        for queue_name, status, count in rows:
            self.queue_depth.labels(queue=queue_name, status=status).set(count)

    def generate(self) -> bytes:
        """Prometheus text exposition format for /metrics endpoint."""
        from prometheus_client import generate_latest

        return generate_latest(self.registry)
