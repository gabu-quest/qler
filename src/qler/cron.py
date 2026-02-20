"""Cron scheduling — @cron decorator and CronWrapper for periodic tasks."""

from __future__ import annotations

import inspect
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any, Callable, Optional

from croniter import croniter

from qler.exceptions import ConfigurationError
from qler.task import TaskWrapper

if TYPE_CHECKING:
    from qler.queue import Queue


@dataclass(frozen=True)
class CronSchedule:
    """Parsed cron schedule metadata."""

    expression: str
    max_running: int = 1
    timezone: str = "UTC"
    catchup: int = 0  # 0 = disabled, N = max missed runs to catch up
    catchup_latest: bool = False  # True = most recent run only (not oldest)


class CronWrapper:
    """Wraps a TaskWrapper with cron scheduling metadata.

    Delegates enqueue(), run_now(), and __call__ to the inner TaskWrapper.
    """

    def __init__(self, task_wrapper: TaskWrapper, schedule: CronSchedule) -> None:
        self.task_wrapper = task_wrapper
        self.schedule = schedule

    @property
    def task_path(self) -> str:
        return self.task_wrapper.task_path

    @property
    def fn(self) -> Callable:
        return self.task_wrapper.fn

    def next_run(self, after: datetime | None = None) -> datetime:
        """Calculate the next run time after the given datetime.

        Args:
            after: Reference time. Defaults to now (UTC).

        Returns:
            Next scheduled run time as a timezone-aware datetime.
        """
        if after is None:
            after = datetime.now(timezone.utc)
        it = croniter(self.schedule.expression, after)
        return it.get_next(datetime)

    def idempotency_key(self, run_ts: int) -> str:
        """Generate a deduplication key for a specific run timestamp.

        Format: cron:{task_path}:{run_ts}
        """
        return f"cron:{self.task_path}:{run_ts}"

    async def _find_last_enqueued_ts(self) -> int | None:
        """Query DB for the most recent cron job's scheduled timestamp.

        Parses the timestamp from the idempotency key (cron:{path}:{ts}).
        Returns None if no previous cron jobs exist for this task.
        """
        from sqler import F

        from qler.models.job import Job

        last_job = await Job.query().filter(
            F("task") == self.task_path
        ).order_by("-ulid").first()

        if last_job is None:
            return None

        key = last_job.idempotency_key
        prefix = f"cron:{self.task_path}:"
        if key and key.startswith(prefix):
            try:
                return int(key.rsplit(":", 1)[1])
            except (ValueError, IndexError):
                return None
        return None

    def missed_runs(self, since_ts: int, now_ts: int) -> list[int]:
        """Walk croniter forward from since_ts, return timestamps <= now_ts.

        Returns up to (catchup + safety margin) timestamps. Caller is
        responsible for trimming to the actual catchup limit.
        """
        base = datetime.fromtimestamp(since_ts, tz=timezone.utc)
        it = croniter(self.schedule.expression, base)
        runs: list[int] = []
        while True:
            nxt = it.get_next(datetime)
            ts = int(nxt.timestamp())
            if ts > now_ts:
                break
            runs.append(ts)
            if len(runs) > 200:  # safety cap
                break
        return runs

    async def enqueue(
        self,
        *args: Any,
        _delay: Optional[int] = None,
        _eta: Optional[int] = None,
        _priority: Optional[int] = None,
        _idempotency_key: Optional[str] = None,
        _correlation_id: Optional[str] = None,
        **kwargs: Any,
    ) -> Any:
        """Delegate to the inner TaskWrapper's enqueue."""
        return await self.task_wrapper.enqueue(
            *args,
            _delay=_delay,
            _eta=_eta,
            _priority=_priority,
            _idempotency_key=_idempotency_key,
            _correlation_id=_correlation_id,
            **kwargs,
        )

    async def run_now(self, *args: Any, **kwargs: Any) -> Any:
        """Delegate to the inner TaskWrapper's run_now."""
        return await self.task_wrapper.run_now(*args, **kwargs)

    async def __call__(self, *args: Any, **kwargs: Any) -> Any:
        """Delegate to the inner TaskWrapper's __call__."""
        return await self.task_wrapper(*args, **kwargs)


def _normalize_catchup(catchup: int | str | bool) -> tuple[int, bool]:
    """Normalize catchup parameter to (int, is_latest).

    Returns:
        (0, False) for disabled.
        (1, True) for "latest" (most recent missed run only).
        (N, False) for integer N (oldest N missed runs).

    Raises ConfigurationError for invalid values.
    """
    if catchup is True:
        raise ConfigurationError(
            "catchup=True is not supported (unbounded catchup is almost always a bug). "
            "Use catchup='latest' for 1, or catchup=N for a specific limit."
        )
    if catchup is False:
        return 0, False
    if catchup == "latest":
        return 1, True
    if isinstance(catchup, int):
        if catchup < 1:
            raise ConfigurationError(
                f"catchup must be >= 1, got {catchup}"
            )
        if catchup > 100:
            raise ConfigurationError(
                f"catchup must be <= 100, got {catchup}"
            )
        return catchup, False
    raise ConfigurationError(
        f"catchup must be False, 'latest', or int 1-100, got {catchup!r}"
    )


def cron(
    queue: Queue,
    expression: str,
    *,
    max_running: int = 1,
    catchup: int | str | bool = False,
    timezone_name: str = "UTC",
    queue_name: Optional[str] = None,
    max_retries: Optional[int] = None,
    retry_delay: Optional[int] = None,
    priority: Optional[int] = None,
    lease_duration: Optional[int] = None,
    sync: bool = False,
) -> Callable[[Callable], CronWrapper]:
    """Decorator to register a function as a periodic cron task.

    Args:
        queue: The Queue instance to register on.
        expression: Cron expression (e.g., "*/5 * * * *").
        max_running: Maximum concurrent pending/running instances (default: 1).
        catchup: How many missed runs to enqueue on startup.
            False (default): skip missed runs.
            "latest": enqueue only the most recent missed run.
            int (1-100): enqueue up to N missed runs, oldest first.
        timezone_name: Timezone for cron evaluation (default: "UTC").
        queue_name: Queue name override (default: "default").
        max_retries: Max retry count override.
        retry_delay: Base retry delay in seconds (must be >= 1 if provided).
        priority: Default priority override.
        lease_duration: Default lease duration override.
        sync: If True, the function is synchronous and will be run via asyncio.to_thread.
    """
    # Validate cron expression at decoration time (fail-fast)
    if not croniter.is_valid(expression):
        raise ConfigurationError(
            f"Invalid cron expression: '{expression}'"
        )

    if max_running < 1:
        raise ConfigurationError(
            f"max_running must be >= 1, got {max_running}"
        )

    catchup_int, is_latest = _normalize_catchup(catchup)

    def decorator(fn: Callable) -> CronWrapper:
        # Reject nested functions
        if fn.__qualname__ != fn.__name__:
            raise ConfigurationError(
                f"@cron cannot decorate nested or inner function '{fn.__qualname__}'. "
                "Only module-level functions are supported."
            )

        # Validate async/sync consistency
        if sync and inspect.iscoroutinefunction(fn):
            raise ConfigurationError(
                f"@cron(sync=True) used on async function '{fn.__name__}'. "
                "Remove sync=True or make the function synchronous."
            )
        if not sync and not inspect.iscoroutinefunction(fn):
            raise ConfigurationError(
                f"@cron used on sync function '{fn.__name__}'. "
                "Either make it async or pass sync=True."
            )

        # Validate retry_delay
        if retry_delay is not None and retry_delay < 1:
            raise ConfigurationError(
                f"retry_delay must be >= 1, got {retry_delay}"
            )

        # Create the inner TaskWrapper
        task_wrapper = TaskWrapper(
            fn,
            queue,
            queue_name=queue_name,
            max_retries=max_retries,
            retry_delay=retry_delay,
            priority=priority,
            lease_duration=lease_duration,
            sync=sync,
        )

        schedule = CronSchedule(
            expression=expression,
            max_running=max_running,
            timezone=timezone_name,
            catchup=catchup_int,
            catchup_latest=is_latest,
        )

        cron_wrapper = CronWrapper(task_wrapper, schedule)

        # Register on queue's cron task registry
        queue._cron_tasks[task_wrapper.task_path] = cron_wrapper

        return cron_wrapper

    return decorator
