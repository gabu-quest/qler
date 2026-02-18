"""Cron scheduling — @cron decorator and CronWrapper for periodic tasks."""

from __future__ import annotations

import inspect
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
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


def cron(
    queue: Queue,
    expression: str,
    *,
    max_running: int = 1,
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
        )

        cron_wrapper = CronWrapper(task_wrapper, schedule)

        # Register on queue's cron task registry
        queue._cron_tasks[task_wrapper.task_path] = cron_wrapper

        return cron_wrapper

    return decorator
