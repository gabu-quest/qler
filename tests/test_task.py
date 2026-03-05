"""Tests for @task decorator and TaskWrapper."""

import re

import pytest
from qler._time import now_epoch
from qler.exceptions import ConfigurationError, PayloadNotSerializableError
from qler.task import TaskWrapper, task

_ULID_PATTERN = re.compile(r"^[0-9A-Z]{26}$")


# Module-level task functions (required by @task's qualname validation)
async def noop_task():
    pass


async def add_task(x, y):
    return x + y


async def add_task_default(x, y=0):
    return x + y


async def task_with_arg(x):
    pass


def sync_add_task(x, y):
    return x + y


class TestTaskDecorator:
    """Verify @task creates TaskWrapper with correct attributes."""

    async def test_creates_task_wrapper(self, queue):
        wrapped = task(queue)(noop_task)
        assert isinstance(wrapped, TaskWrapper)

    async def test_correct_task_path(self, queue):
        wrapped = task(queue)(noop_task)
        assert wrapped.task_path == f"{__name__}.noop_task"

    async def test_registers_on_queue(self, queue):
        wrapped = task(queue)(noop_task)
        assert wrapped.task_path in queue._tasks
        assert queue._tasks[wrapped.task_path] is wrapped

    async def test_default_queue_name(self, queue):
        wrapped = task(queue)(noop_task)
        assert wrapped.queue_name == "default"

    async def test_custom_queue_name(self, queue):
        wrapped = task(queue, queue_name="emails")(noop_task)
        assert wrapped.queue_name == "emails"

    async def test_max_retries_from_queue(self, queue):
        wrapped = task(queue)(noop_task)
        assert wrapped.max_retries == queue.default_max_retries

    async def test_max_retries_override(self, queue):
        wrapped = task(queue, max_retries=5)(noop_task)
        assert wrapped.max_retries == 5

    async def test_retry_delay_override(self, queue):
        wrapped = task(queue, retry_delay=120)(noop_task)
        assert wrapped.retry_delay == 120

    async def test_priority_override(self, queue):
        wrapped = task(queue, priority=10)(noop_task)
        assert wrapped.priority == 10

    async def test_lease_duration_override(self, queue):
        wrapped = task(queue, lease_duration=600)(noop_task)
        assert wrapped.lease_duration == 600


class TestTaskValidation:
    """Verify @task rejects invalid configurations."""

    def test_rejects_nested_function(self, queue):
        with pytest.raises(ConfigurationError, match="nested or inner"):

            def outer():
                @task(queue)
                async def inner():
                    pass

            outer()

    def test_rejects_sync_on_async(self, queue):
        """sync=True on an async function should raise."""
        with pytest.raises(ConfigurationError, match="sync=True.*async"):
            # Need a real async function at module level to test this
            task(queue, sync=True)(noop_task)

    def test_rejects_async_on_sync(self, queue):
        """An undecorated sync function without sync=True should raise."""
        with pytest.raises(ConfigurationError, match="sync function"):
            task(queue)(sync_add_task)

    def test_rejects_retry_delay_zero(self, queue):
        with pytest.raises(ConfigurationError, match="retry_delay must be >= 1"):
            task(queue, retry_delay=0)(noop_task)

    def test_rejects_retry_delay_negative(self, queue):
        with pytest.raises(ConfigurationError, match="retry_delay must be >= 1"):
            task(queue, retry_delay=-1)(noop_task)


class TestTaskEnqueue:
    """Verify TaskWrapper.enqueue delegates to Queue."""

    async def test_enqueue_creates_job(self, queue):
        wrapped = task(queue)(add_task)

        job = await wrapped.enqueue(1, 2)
        assert _ULID_PATTERN.match(job.ulid), f"Expected ULID format, got {job.ulid!r}"
        assert job.status == "pending"
        assert job.task == wrapped.task_path
        assert job.payload == {"args": [1, 2], "kwargs": {}}

    async def test_enqueue_with_kwargs(self, queue):
        wrapped = task(queue)(add_task_default)

        job = await wrapped.enqueue(1, y=2)
        assert job.payload == {"args": [1], "kwargs": {"y": 2}}

    async def test_enqueue_with_priority(self, queue):
        wrapped = task(queue, priority=5)(noop_task)

        job = await wrapped.enqueue()
        assert job.priority == 5

    async def test_enqueue_priority_override(self, queue):
        wrapped = task(queue, priority=5)(noop_task)

        job = await wrapped.enqueue(_priority=10)
        assert job.priority == 10

    async def test_enqueue_with_delay(self, queue):
        wrapped = task(queue)(noop_task)
        before = now_epoch()
        job = await wrapped.enqueue(_delay=60)
        after = now_epoch()
        assert before + 60 <= job.eta <= after + 60

    async def test_enqueue_with_eta(self, queue):
        wrapped = task(queue)(noop_task)
        fixed_eta = now_epoch() + 3600
        job = await wrapped.enqueue(_eta=fixed_eta)
        assert job.eta == fixed_eta

    async def test_enqueue_with_idempotency_key(self, queue):
        wrapped = task(queue)(noop_task)
        job1 = await wrapped.enqueue(_idempotency_key="dedup-1")
        job2 = await wrapped.enqueue(_idempotency_key="dedup-1")
        assert job1.ulid == job2.ulid

    async def test_enqueue_with_correlation_id(self, queue):
        wrapped = task(queue)(noop_task)
        job = await wrapped.enqueue(_correlation_id="req-abc")
        assert job.correlation_id == "req-abc"


class TestIdempotencyKeyFn:
    """Verify per-task idempotency key generation."""

    async def test_key_fn_generates_key_and_deduplicates(self, queue):
        """Key fn is called and its output used for dedup."""
        wrapped = task(
            queue, idempotency_key=lambda x, y: f"order:{x}"
        )(add_task)
        job1 = await wrapped.enqueue(42, 0)
        job2 = await wrapped.enqueue(42, 0)
        assert job1.ulid == job2.ulid
        assert job1.idempotency_key == "order:42"
        assert job2.idempotency_key == "order:42"

    async def test_key_fn_discriminates_different_args(self, queue):
        """Different args produce different keys and different jobs."""
        wrapped = task(
            queue, idempotency_key=lambda x, y: f"order:{x}"
        )(add_task)
        job_a = await wrapped.enqueue(1, 0)
        job_b = await wrapped.enqueue(2, 0)
        assert job_a.ulid != job_b.ulid
        assert job_a.idempotency_key == "order:1"
        assert job_b.idempotency_key == "order:2"

    async def test_key_fn_with_kwargs(self, queue):
        """Key fn receives kwargs correctly and deduplicates."""
        wrapped = task(
            queue, idempotency_key=lambda x, y=0: f"add:{x}:{y}"
        )(add_task_default)
        job1 = await wrapped.enqueue(1, y=2)
        assert job1.idempotency_key == "add:1:2"
        job2 = await wrapped.enqueue(1, y=2)
        assert job2.ulid == job1.ulid

    async def test_explicit_key_overrides_fn(self, queue):
        """Explicit _idempotency_key= takes precedence over fn."""
        wrapped = task(
            queue, idempotency_key=lambda x, y: f"auto:{x}"
        )(add_task)
        job1 = await wrapped.enqueue(1, 2, _idempotency_key="manual:override")
        assert job1.idempotency_key == "manual:override"
        job2 = await wrapped.enqueue(1, 2, _idempotency_key="manual:override")
        assert job2.ulid == job1.ulid

    async def test_cancelled_job_does_not_block_reenqueue(self, queue):
        """Cancelled job with same key allows re-enqueue."""
        wrapped = task(queue, idempotency_key=lambda: "key-x")(noop_task)
        job1 = await wrapped.enqueue()
        assert job1.idempotency_key == "key-x"
        await queue.cancel_job(job1)
        job2 = await wrapped.enqueue()
        assert job2.ulid != job1.ulid
        assert job2.idempotency_key == "key-x"

    async def test_no_key_without_fn(self, queue):
        """No fn and no explicit key → no idempotency key (existing behavior)."""
        wrapped = task(queue)(noop_task)
        job = await wrapped.enqueue()
        assert job.idempotency_key is None

    def test_non_callable_raises_configuration_error(self, queue):
        """Non-callable idempotency_key raises ConfigurationError at decoration time."""
        with pytest.raises(ConfigurationError, match="idempotency_key must be callable"):
            task(queue, idempotency_key="not-a-function")(noop_task)

    async def test_key_fn_exception_propagates(self, queue):
        """Exception in key fn propagates to caller."""
        def bad_key(*args, **kwargs):
            raise ValueError("key generation failed")

        wrapped = task(queue, idempotency_key=bad_key)(noop_task)
        with pytest.raises(ValueError, match="key generation failed"):
            await wrapped.enqueue()

    async def test_key_fn_returns_non_string_raises_type_error(self, queue):
        """Non-string return from key fn raises TypeError."""
        wrapped = task(
            queue, idempotency_key=lambda: 42
        )(noop_task)
        with pytest.raises(TypeError, match="must return str"):
            await wrapped.enqueue()

    async def test_idempotency_key_fn_attr_stored(self, queue):
        """The idempotency_key_fn attribute is accessible on TaskWrapper."""
        key_fn = lambda x: f"key:{x}"
        wrapped = task(queue, idempotency_key=key_fn)(add_task)
        assert wrapped.idempotency_key_fn is key_fn

        wrapped_no_fn = task(queue)(noop_task)
        assert wrapped_no_fn.idempotency_key_fn is None


class TestTaskRunNow:
    """Verify TaskWrapper.run_now executes directly."""

    async def test_run_now_async(self, queue):
        wrapped = task(queue)(add_task)
        result = await wrapped.run_now(3, 4)
        assert result == 7

    async def test_run_now_sync(self, queue):
        wrapped = task(queue, sync=True)(sync_add_task)
        result = await wrapped.run_now(3, 4)
        assert result == 7

    async def test_run_now_validates_payload(self, queue):
        wrapped = task(queue)(task_with_arg)

        with pytest.raises(PayloadNotSerializableError, match="not JSON-serializable"):
            await wrapped.run_now(object())


class TestTaskCall:
    """Verify TaskWrapper.__call__ calls the function directly."""

    async def test_call_async(self, queue):
        wrapped = task(queue)(add_task)
        result = await wrapped(3, 4)
        assert result == 7

    async def test_call_sync(self, queue):
        wrapped = task(queue, sync=True)(sync_add_task)
        result = await wrapped(3, 4)
        assert result == 7


class TestTaskTimeout:
    """Timeout configuration on @task decorator."""

    async def test_timeout_stored_on_wrapper(self, queue):
        wrapped = task(queue, timeout=30)(noop_task)
        assert wrapped._timeout == 30

    async def test_timeout_none_by_default(self, queue):
        wrapped = task(queue)(noop_task)
        assert wrapped._timeout is None

    async def test_timeout_passed_to_enqueue(self, queue):
        """Task timeout flows through to Job.timeout on enqueue."""
        wrapped = task(queue, timeout=45)(noop_task)
        job = await wrapped.enqueue()
        assert job.timeout == 45

    async def test_per_job_timeout_overrides_task(self, queue):
        """_timeout on enqueue overrides task-level default."""
        wrapped = task(queue, timeout=45)(noop_task)
        job = await wrapped.enqueue(_timeout=10)
        assert job.timeout == 10

    async def test_timeout_none_when_not_set(self, queue):
        """Job has timeout=None when no timeout configured."""
        wrapped = task(queue)(noop_task)
        job = await wrapped.enqueue()
        assert job.timeout is None

    def test_invalid_timeout_zero(self, queue):
        with pytest.raises(ConfigurationError, match="positive integer"):
            task(queue, timeout=0)(noop_task)

    def test_invalid_timeout_negative(self, queue):
        with pytest.raises(ConfigurationError, match="positive integer"):
            task(queue, timeout=-5)(noop_task)

    def test_invalid_timeout_string(self, queue):
        with pytest.raises(ConfigurationError, match="positive integer"):
            task(queue, timeout="30")(noop_task)

    def test_invalid_timeout_float(self, queue):
        with pytest.raises(ConfigurationError, match="positive integer"):
            task(queue, timeout=1.5)(noop_task)


class TestEnqueueMany:
    """TaskWrapper.enqueue_many() — bulk enqueue convenience."""

    async def test_enqueue_many_with_tuples(self, queue):
        """Each tuple becomes positional args for the task."""
        wrapped = task(queue)(add_task)
        jobs = await wrapped.enqueue_many([(1, 2), (3, 4), (5, 6)])
        assert len(jobs) == 3
        for job in jobs:
            assert job.task == wrapped.task_path
            assert job.queue_name == "default"
            assert job.status == "pending"

    async def test_enqueue_many_with_dicts(self, queue):
        """Dicts can specify args, kwargs, and per-job overrides."""
        wrapped = task(queue)(add_task)
        jobs = await wrapped.enqueue_many([
            {"args": (1, 2)},
            {"args": (3,), "kwargs": {"y": 4}, "priority": 5},
        ])
        assert len(jobs) == 2
        assert jobs[1].priority == 5

    async def test_enqueue_many_empty(self, queue):
        wrapped = task(queue)(noop_task)
        jobs = await wrapped.enqueue_many([])
        assert jobs == []

    async def test_enqueue_many_inherits_task_defaults(self, queue):
        """Batch jobs inherit task-level config (retries, timeout, etc.)."""
        wrapped = task(queue, max_retries=3, timeout=30)(noop_task)
        jobs = await wrapped.enqueue_many([(), ()])
        assert len(jobs) == 2
        for job in jobs:
            assert job.max_retries == 3
            assert job.timeout == 30

    async def test_enqueue_many_invalid_item_type(self, queue):
        wrapped = task(queue)(noop_task)
        with pytest.raises(TypeError, match="tuple or dict"):
            await wrapped.enqueue_many(["not_valid"])
