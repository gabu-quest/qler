"""Minimal Celery app for comparison benchmarks.

Defines a single no-op task used for apples-to-apples comparison with qler.
Only imported when the comparison suite is active (requires celery[redis]).
"""

from __future__ import annotations

REDIS_URL = "redis://localhost:6379/15"

try:
    from celery import Celery

    app = Celery(
        "bench",
        broker=REDIS_URL,
        backend=REDIS_URL,
    )
    app.conf.update(
        task_serializer="json",
        result_serializer="json",
        accept_content=["json"],
        task_track_started=False,
        worker_hijack_root_logger=False,
        worker_log_color=False,
    )

    @app.task
    def noop(*args, **kwargs):
        """No-op task — mirrors the qler benchmark pattern."""
        return {"ok": True}

except ImportError:
    app = None  # type: ignore[assignment]
    noop = None  # type: ignore[assignment]
