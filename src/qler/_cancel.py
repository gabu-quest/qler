"""The one cancel of a job, reached by ``Job.cancel()`` and ``Queue.cancel_job()``.

Works on the database the job's row lives in, through public sqler calls only, so
it needs no queue. Imports no job module at load time, so both callers can import it.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqler import AsyncSQLerQuerySet, F

from qler._notify import fire as _notify_fire
from qler._time import now_epoch
from qler.enums import JobStatus
from qler.models.dependency import JobDependency

if TYPE_CHECKING:
    from qler.models.job import Job

# One read of the edges per this many parents, under SQLite's 999-variable limit.
_EDGE_CHUNK = 900


async def children_by_parent(
    edges: AsyncSQLerQuerySet[JobDependency], parents: list[str]
) -> dict[str, list[str]]:
    """Each parent's children, ascending: one read of the edges per 900 parents;
    builds no model."""
    children: dict[str, set[str]] = {}
    for off in range(0, len(parents), _EDGE_CHUNK):
        for edge in (
            await edges.filter(F("parent_ulid").in_list(parents[off : off + _EDGE_CHUNK]))
            .select("parent_ulid", "child_ulid")
            .as_dicts()
        ):
            children.setdefault(edge["parent_ulid"], set()).add(edge["child_ulid"])
    return {parent: sorted(kids) for parent, kids in children.items()}


async def cascade_cancel_dependents(jobs: AsyncSQLerQuerySet[Job], failed_ulid: str) -> list[str]:
    """Cancel PENDING jobs that depend on a failed/cancelled job, recursively.

    Runs on the database ``jobs`` uses. One transaction (a savepoint inside the
    caller's): the whole cascade is cancelled, or none of it. Returns the ULIDs it
    cancelled, in order, so the caller notifies waiters only after its own commit.
    """
    db = jobs.db()
    edges = JobDependency.using(db)
    now = now_epoch()
    cancelled_ulids: list[str] = []
    async with db.transaction():
        # A level at a time: one read for the edges of every job on the level, then
        # its parents in order, each one's children ascending, as a job-by-job walk
        # would go. A child is tried once, under the first parent that reaches it.
        level = [failed_ulid]
        tried: set[str] = set()
        while level:
            children = await children_by_parent(edges, level)
            next_level: list[str] = []
            for parent_ulid in level:
                for child_ulid in children.get(parent_ulid, []):
                    if child_ulid in tried:
                        continue
                    tried.add(child_ulid)
                    updated = await jobs.filter(
                        (F("ulid") == child_ulid) & (F("status") == JobStatus.PENDING.value)
                    ).update_one(
                        status=JobStatus.CANCELLED.value,
                        last_error=f"Dependency {parent_ulid} failed/cancelled",
                        finished_at=now,
                        updated_at=now,
                    )
                    if updated is not None:
                        cancelled_ulids.append(child_ulid)
                        next_level.append(child_ulid)
            level = next_level
    return cancelled_ulids


async def cancel_job(job: Job) -> bool:
    """Cancel a job and its pending dependents, in the database its row lives in.

    A PENDING job and its pending dependents are cancelled in one transaction; a
    RUNNING job gets a cooperative cancellation request and no dependent is touched;
    any other state returns False and writes nothing. Waiters are notified after the
    commit, and only for what was committed. Raises NotBoundError (sqler) for a job
    that was never enqueued or read.
    """
    jobs = type(job).beside(job)
    now = now_epoch()
    async with jobs.db().transaction():
        updated = await jobs.filter(
            (F("ulid") == job.ulid) & (F("status") == JobStatus.PENDING.value)
        ).update_one(
            status=JobStatus.CANCELLED.value,
            finished_at=now,
            updated_at=now,
        )
        cancelled = await cascade_cancel_dependents(jobs, job.ulid) if updated is not None else []

    if updated is None:
        # Not PENDING: if RUNNING, request cooperative cancellation instead
        return await job.request_cancel()

    job._sync_from(updated)
    _notify_fire(job.ulid)
    for child_ulid in cancelled:
        _notify_fire(child_ulid)
    return True
