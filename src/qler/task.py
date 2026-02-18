"""@task decorator and TaskWrapper for defining background tasks."""

from __future__ import annotations

import asyncio
import inspect
import json
from typing import TYPE_CHECKING, Any, Callable, Optional

from qler.exceptions import ConfigurationError, PayloadNotSerializableError
from qler.rate_limit import RateSpec, parse_rate

if TYPE_CHECKING:
    from qler.models.job import Job
    from qler.queue import Queue


class TaskWrapper:
    """Wraps a function as a qler task with enqueue/run capabilities."""

    def __init__(
        self,
        fn: Callable,
        queue: Queue,
        *,
        queue_name: Optional[str] = None,
        max_retries: Optional[int] = None,
        retry_delay: Optional[int] = None,
        priority: Optional[int] = None,
        lease_duration: Optional[int] = None,
        sync: bool = False,
        rate_limit: Optional[str] = None,
    ) -> None:
        self.fn = fn
        self.queue = queue
        self._queue_name = queue_name
        self._max_retries = max_retries
        self._retry_delay = retry_delay
        self._priority = priority
        self._lease_duration = lease_duration
        self.sync = sync
        self.task_path = f"{fn.__module__}.{fn.__qualname__}"
        self.rate_spec: Optional[RateSpec] = parse_rate(rate_limit) if rate_limit else None

        # Register on queue
        queue._tasks[self.task_path] = self

    @property
    def queue_name(self) -> str:
        return self._queue_name or "default"

    @property
    def max_retries(self) -> int:
        if self._max_retries is not None:
            return self._max_retries
        return self.queue.default_max_retries

    @property
    def retry_delay(self) -> int:
        if self._retry_delay is not None:
            return self._retry_delay
        return self.queue.default_retry_delay

    @property
    def priority(self) -> int:
        if self._priority is not None:
            return self._priority
        return 0

    @property
    def lease_duration(self) -> int:
        if self._lease_duration is not None:
            return self._lease_duration
        return self.queue.default_lease_duration

    async def enqueue(
        self,
        *args: Any,
        _delay: Optional[int] = None,
        _eta: Optional[int] = None,
        _priority: Optional[int] = None,
        _idempotency_key: Optional[str] = None,
        _correlation_id: Optional[str] = None,
        _depends_on: Optional[list[str]] = None,
        **kwargs: Any,
    ) -> "Job":
        """Enqueue this task for background execution."""
        return await self.queue.enqueue(
            self.task_path,
            args=args,
            kwargs=kwargs,
            queue_name=self.queue_name,
            delay=_delay,
            eta=_eta,
            priority=_priority if _priority is not None else self.priority,
            max_retries=self.max_retries,
            retry_delay=self.retry_delay,
            lease_duration=self.lease_duration,
            idempotency_key=_idempotency_key,
            correlation_id=_correlation_id,
            depends_on=_depends_on,
        )

    async def run_now(self, *args: Any, **kwargs: Any) -> Any:
        """Execute the task directly (not via queue). Validates payload is serializable."""
        payload = {"args": list(args), "kwargs": kwargs}
        try:
            json.dumps(payload)
        except (TypeError, ValueError, OverflowError) as exc:
            raise PayloadNotSerializableError(
                f"Payload is not JSON-serializable: {exc}"
            ) from exc

        if self.sync:
            return await asyncio.to_thread(self.fn, *args, **kwargs)
        return await self.fn(*args, **kwargs)

    async def __call__(self, *args: Any, **kwargs: Any) -> Any:
        """Call the underlying function directly."""
        if self.sync:
            return await asyncio.to_thread(self.fn, *args, **kwargs)
        return await self.fn(*args, **kwargs)


def task(
    queue: Queue,
    *,
    queue_name: Optional[str] = None,
    max_retries: Optional[int] = None,
    retry_delay: Optional[int] = None,
    priority: Optional[int] = None,
    lease_duration: Optional[int] = None,
    sync: bool = False,
    rate_limit: Optional[str] = None,
) -> Callable[[Callable], TaskWrapper]:
    """Decorator to register a function as a qler task.

    Args:
        queue: The Queue instance to register on.
        queue_name: Queue name override (default: "default").
        max_retries: Max retry count override.
        retry_delay: Base retry delay in seconds (must be >= 1 if provided).
        priority: Default priority override.
        lease_duration: Default lease duration override.
        sync: If True, the function is synchronous and will be run via asyncio.to_thread.
        rate_limit: Rate limit spec (e.g., "10/m", "100/h"). None = unlimited.
    """
    # Validate rate_limit early (fail at import time, not at enqueue time)
    if rate_limit is not None:
        parse_rate(rate_limit)

    def decorator(fn: Callable) -> TaskWrapper:
        # Reject nested functions (methods, closures, inner functions)
        if fn.__qualname__ != fn.__name__:
            raise ConfigurationError(
                f"@task cannot decorate nested or inner function '{fn.__qualname__}'. "
                "Only module-level functions are supported."
            )

        # Validate async/sync consistency
        if sync and inspect.iscoroutinefunction(fn):
            raise ConfigurationError(
                f"@task(sync=True) used on async function '{fn.__name__}'. "
                "Remove sync=True or make the function synchronous."
            )
        if not sync and not inspect.iscoroutinefunction(fn):
            raise ConfigurationError(
                f"@task used on sync function '{fn.__name__}'. "
                "Either make it async or pass sync=True."
            )

        # Validate retry_delay
        if retry_delay is not None and retry_delay < 1:
            raise ConfigurationError(
                f"retry_delay must be >= 1, got {retry_delay}"
            )

        return TaskWrapper(
            fn,
            queue,
            queue_name=queue_name,
            max_retries=max_retries,
            retry_delay=retry_delay,
            priority=priority,
            lease_duration=lease_duration,
            sync=sync,
            rate_limit=rate_limit,
        )

    return decorator
