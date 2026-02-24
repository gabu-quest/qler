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
        idempotency_key: Optional[Callable[..., str]] = None,
        timeout: Optional[int] = None,
    ) -> None:
        self.fn = fn
        self.queue = queue
        self._queue_name = queue_name
        self._max_retries = max_retries
        self._retry_delay = retry_delay
        self._priority = priority
        self._lease_duration = lease_duration
        self._timeout = timeout
        self.sync = sync
        self.task_path = f"{fn.__module__}.{fn.__qualname__}"
        self.rate_spec: Optional[RateSpec] = parse_rate(rate_limit) if rate_limit else None
        self.idempotency_key_fn = idempotency_key

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
        _timeout: Optional[int] = None,
        **kwargs: Any,
    ) -> "Job":
        """Enqueue this task for background execution."""
        if _idempotency_key is None and self.idempotency_key_fn is not None:
            _idempotency_key = self.idempotency_key_fn(*args, **kwargs)
            if not isinstance(_idempotency_key, str):
                raise TypeError(
                    f"idempotency_key function must return str, "
                    f"got {type(_idempotency_key).__name__}"
                )
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
            timeout=_timeout if _timeout is not None else self._timeout,
        )

    async def enqueue_many(
        self,
        arg_list: list[tuple | dict],
    ) -> list["Job"]:
        """Enqueue multiple instances of this task at once.

        Each element can be:
          - A tuple of positional args: (arg1, arg2, ...)
          - A dict with "args" and/or "kwargs" keys plus optional overrides

        Returns list of Job instances.
        """
        jobs = []
        for item in arg_list:
            if isinstance(item, tuple):
                spec: dict[str, Any] = {"args": item}
            elif isinstance(item, dict):
                spec = dict(item)
            else:
                raise TypeError(
                    f"Each item must be a tuple or dict, got {type(item).__name__}"
                )

            spec.setdefault("task_path", self.task_path)
            spec.setdefault("queue_name", self.queue_name)
            spec.setdefault("priority", self.priority)
            spec.setdefault("max_retries", self.max_retries)
            spec.setdefault("retry_delay", self.retry_delay)
            spec.setdefault("lease_duration", self.lease_duration)
            if self._timeout is not None:
                spec.setdefault("timeout", self._timeout)

            # Apply idempotency key function if set and no explicit key
            if "idempotency_key" not in spec and self.idempotency_key_fn is not None:
                args = spec.get("args", ())
                kwargs = spec.get("kwargs", {})
                key = self.idempotency_key_fn(*args, **kwargs)
                if not isinstance(key, str):
                    raise TypeError(
                        f"idempotency_key function must return str, "
                        f"got {type(key).__name__}"
                    )
                spec["idempotency_key"] = key

            jobs.append(spec)

        return await self.queue.enqueue_many(jobs)

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
    idempotency_key: Optional[Callable[..., str]] = None,
    timeout: Optional[int] = None,
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
        idempotency_key: Callable that generates an idempotency key from task args/kwargs.
        timeout: Execution timeout in seconds. None = no limit.
    """
    # Validate rate_limit early (fail at import time, not at enqueue time)
    if rate_limit is not None:
        parse_rate(rate_limit)

    if idempotency_key is not None and not callable(idempotency_key):
        raise ConfigurationError(
            f"idempotency_key must be callable, got {type(idempotency_key).__name__}"
        )

    if timeout is not None and (not isinstance(timeout, int) or timeout <= 0):
        raise ConfigurationError(
            f"timeout must be a positive integer, got {timeout!r}"
        )

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
            idempotency_key=idempotency_key,
            timeout=timeout,
        )

    return decorator
