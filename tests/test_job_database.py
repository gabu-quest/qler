"""A job's own methods use the database its row lives in.

Two queues in one process, each on its own file, never share a database: every job
method on a job of the first queue changes only the first file. A job with no
database (never enqueued, or unpickled) raises sqler's NotBoundError.
"""

import asyncio
import pickle
import warnings

import pytest
import pytest_asyncio
from sqler import F
from sqler.exceptions import NotBoundError

from qler._context import _current_job, is_cancellation_requested, set_progress
from qler.enums import JobStatus
from qler.models.job import Job
from qler.queue import Queue

NO_HOME = "Job instance has no database: it was never read or saved, or it was unpickled."


@pytest_asyncio.fixture
async def two_queues(tmp_path):
    """Two initialized queues on two files, the second opened after the first."""
    first = Queue(str(tmp_path / "first.db"))
    await first.init_db()
    second = Queue(str(tmp_path / "second.db"))
    await second.init_db()
    yield first, second
    await first.close()
    await second.close()


async def _rows(queue: Queue) -> list[dict]:
    return await queue.jobs.order_by("ulid").as_dicts()


async def _row(queue: Queue, ulid: str) -> Job:
    row = await queue.jobs.filter(F("ulid") == ulid).first()
    assert row is not None
    return row


async def _enqueue_both(first: Queue, second: Queue) -> tuple[Job, list[dict]]:
    """A job in each queue, interleaved; returns the first queue's job and the
    second file's rows before any job method runs."""
    job = await first.enqueue("mod.task", max_retries=0)
    await second.enqueue("mod.task", max_retries=0)
    return job, await _rows(second)


async def _as_read(queue: Queue, job: Job, source: str) -> Job:
    """The job as ``enqueue`` returned it, or read back through ``queue.jobs``."""
    return job if source == "enqueue" else await _row(queue, job.ulid)


class TestTwoQueuesInOneProcess:
    async def test_init_db_warns_of_no_set_db(self, tmp_path):
        queue = Queue(str(tmp_path / "q.db"))
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            await queue.init_db()
        await queue.close()
        assert [str(w.message) for w in caught if "set_db" in str(w.message)] == []

    @pytest.mark.parametrize("source", ["enqueue", "read"])
    async def test_cancel_changes_only_its_file(self, two_queues, source):
        first, second = two_queues
        job, before = await _enqueue_both(first, second)
        job = await _as_read(first, job, source)
        assert await job.cancel() is True
        assert (await _row(first, job.ulid)).status == JobStatus.CANCELLED.value
        assert await _rows(second) == before

    @pytest.mark.parametrize("source", ["enqueue", "read"])
    async def test_request_cancel_changes_only_its_file(self, two_queues, source):
        first, second = two_queues
        job, _ = await _enqueue_both(first, second)
        claimed = await first.claim_job("w1", ["default"])
        assert claimed is not None
        before = await _rows(second)
        target = claimed if source == "read" else job
        assert await target.request_cancel() is True
        assert (await _row(first, job.ulid)).cancel_requested is True
        assert await _rows(second) == before

    @pytest.mark.parametrize("source", ["enqueue", "read"])
    async def test_retry_changes_only_its_file(self, two_queues, source):
        first, second = two_queues
        job, _ = await _enqueue_both(first, second)
        claimed = await first.claim_job("w1", ["default"])
        await first.fail_job(claimed, "w1", RuntimeError("boom"))
        before = await _rows(second)
        target = await _as_read(first, job, source)
        assert await target.retry() is True
        assert (await _row(first, job.ulid)).status == JobStatus.PENDING.value
        assert await _rows(second) == before

    async def test_renew_lease_changes_only_its_file(self, two_queues):
        first, second = two_queues
        job, _ = await _enqueue_both(first, second)
        claimed = await first.claim_job("w1", ["default"])
        before = await _rows(second)
        assert await claimed.renew_lease(100_000) is True
        assert (await _row(first, job.ulid)).lease_expires_at == claimed.lease_expires_at
        assert await _rows(second) == before

    async def test_wait_for_dependencies_reads_its_file(self, two_queues):
        first, second = two_queues
        parent = await first.enqueue("mod.task")
        child = await first.enqueue("mod.task", depends_on=[parent.ulid])
        await second.enqueue("mod.task")
        claimed = await first.claim_job("w1", ["default"])
        assert claimed.ulid == parent.ulid
        await first.complete_job(claimed, "w1", result=1)
        assert await child.wait_for_dependencies(timeout=1.0, poll_interval=0.01) is None

    async def test_set_progress_changes_only_its_file(self, two_queues):
        first, second = two_queues
        job, _ = await _enqueue_both(first, second)
        claimed = await first.claim_job("w1", ["default"])
        before = await _rows(second)
        token = _current_job.set(claimed)
        try:
            await set_progress(40, "forty")
        finally:
            _current_job.reset(token)
        row = await _row(first, job.ulid)
        assert (row.progress, row.progress_message) == (40, "forty")
        assert await _rows(second) == before

    async def test_set_progress_from_a_sync_thread(self, two_queues):
        first, second = two_queues
        job, _ = await _enqueue_both(first, second)
        claimed = await first.claim_job("w1", ["default"])
        before = await _rows(second)
        loop = asyncio.get_running_loop()

        def sync_task() -> None:
            asyncio.run_coroutine_threadsafe(set_progress(70, "thread"), loop).result(5)

        token = _current_job.set(claimed)
        try:
            await asyncio.to_thread(sync_task)
        finally:
            _current_job.reset(token)
        row = await _row(first, job.ulid)
        assert (row.progress, row.progress_message) == (70, "thread")
        assert await _rows(second) == before

    async def test_is_cancellation_requested_reads_its_file(self, two_queues):
        first, second = two_queues
        job, _ = await _enqueue_both(first, second)
        claimed = await first.claim_job("w1", ["default"])
        assert await first.cancel_job(claimed) is True
        token = _current_job.set(claimed)
        try:
            assert await is_cancellation_requested() is True
        finally:
            _current_job.reset(token)


class TestJobWithNoDatabase:
    @pytest.mark.parametrize(
        "call",
        [
            lambda job: job.cancel(),
            lambda job: job.request_cancel(),
            lambda job: job.retry(),
            lambda job: job.renew_lease(),
            lambda job: job.wait_for_dependencies(timeout=0.1),
        ],
        ids=["cancel", "request_cancel", "retry", "renew_lease", "wait_for_dependencies"],
    )
    async def test_never_enqueued_job_raises(self, queue, call):
        job = Job(ulid="01NEVERENQUEUED", task="mod.task", dependencies=["01PARENT"])
        with pytest.raises(NotBoundError, match=NO_HOME):
            await call(job)

    @pytest.mark.parametrize(
        "call",
        [lambda: set_progress(10), lambda: is_cancellation_requested()],
        ids=["set_progress", "is_cancellation_requested"],
    )
    async def test_never_enqueued_job_in_context_raises(self, queue, call):
        token = _current_job.set(Job(ulid="01NEVERENQUEUED", task="mod.task"))
        try:
            with pytest.raises(NotBoundError, match=NO_HOME):
                await call()
        finally:
            _current_job.reset(token)

    async def test_unpickled_job_cancel_raises(self, queue):
        job = await queue.enqueue("mod.task")
        loaded = pickle.loads(pickle.dumps(job))
        with pytest.raises(NotBoundError, match=NO_HOME):
            await loaded.cancel()
        assert (await _row(queue, job.ulid)).status == JobStatus.PENDING.value


class TestArchivedJob:
    async def test_retry_of_an_archived_failed_job_changes_nothing(self, queue):
        """Today's behaviour, kept: the archive is read-only, so retry() on a row read
        from it returns False and neither table changes."""
        await queue.enqueue("mod.task", max_retries=0)
        claimed = await queue.claim_job("w1", ["default"])
        await queue.fail_job(claimed, "w1", RuntimeError("boom"))
        await queue.jobs.filter(F("ulid") == claimed.ulid).update_one(finished_at=0)
        assert await queue.archive_jobs(older_than_seconds=1) == 1
        archived = await queue.archive.filter(F("ulid") == claimed.ulid).first()
        assert archived is not None
        assert await archived.retry() is False
        rows = await queue.archive.as_dicts()
        assert [(r["ulid"], r["status"]) for r in rows] == [(claimed.ulid, JobStatus.FAILED.value)]
        assert await queue.jobs.count() == 0
