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
