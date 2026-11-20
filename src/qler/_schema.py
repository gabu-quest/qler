"""The database schema qler makes and upgrades: tables, indexes, and the steps that bring
an older file up to date. ``Queue.init_db`` calls ``ensure_schema``; nothing else here is public.
"""

from __future__ import annotations

import logging

from sqler import AsyncSQLerDB, F

from qler._sqler import grouped_count
from qler.models.attempt import JobAttempt
from qler.models.bucket import RateLimitBucket
from qler.models.dependency import JobDependency
from qler.models.job import ARCHIVE_TABLE, KEY_INDEXES, Job, KeyIndex

_MAX_KEYS_LOGGED = 20

# The name is spelled out because this code moved out of qler.queue: a user's log filter and the
# tests name that logger (the other modules also name theirs by string).
logger = logging.getLogger("qler.queue")


async def ensure_schema(db: AsyncSQLerDB) -> None:
    """Make the schema qler needs, or bring an older file up to date. Idempotent."""
    await _migrate_rate_limit_bucket_key_column(db)

    # Tables with promoted columns, CHECK constraints and a version column. ensure_table
    # also adds a promoted column an existing table lacks.
    await db.ensure_table(
        Job.__tablename__, promoted=Job.__promoted__, checks=Job.__checks__, versioned=True
    )
    await db.ensure_table(
        JobAttempt.__tablename__,
        promoted=JobAttempt.__promoted__,
        checks=JobAttempt.__checks__,
        versioned=True,
    )
    await db.ensure_table(
        RateLimitBucket.__tablename__,
        promoted=RateLimitBucket.__promoted__,
        checks=RateLimitBucket.__checks__,
        versioned=True,
    )

    # Reverse dependency map: the children of a parent, read through its UNIQUE index
    await db.ensure_table(JobDependency.__tablename__, promoted=JobDependency.__promoted__)

    await _create_indexes(db)
    await upgrade_job_deps_from_0_5_1(db)

    # The archive table: a second table of Job rows, same columns and checks. Nothing is
    # claimed from it, so its only index is the UNIQUE one on ulid (_create_unique_indexes).
    await db.ensure_table(
        ARCHIVE_TABLE, promoted=Job.__promoted__, checks=Job.__checks__, versioned=True
    )
    await _create_unique_indexes(db)


async def _migrate_rate_limit_bucket_key_column(db: AsyncSQLerDB) -> None:
    """Rename the legacy reserved-word bucket column to ``bucket_key``."""
    found = await db.execute_sql(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='qler_rate_limit_buckets';"
    )
    if not found:
        return

    columns = {
        col["name"] for col in await db.execute_sql('PRAGMA table_info("qler_rate_limit_buckets");')
    }

    if "key" in columns and "bucket_key" not in columns:
        # Upgrade step for databases made by qler 0.2.0 to 0.5.0; sqler has no column rename,
        # and execute_sql takes only reads, so the rename stays on the adapter.
        cur = await db.adapter.execute(
            'ALTER TABLE "qler_rate_limit_buckets" RENAME COLUMN "key" TO bucket_key'
        )
        await cur.close()
        await db.adapter.auto_commit()


async def _create_unique_indexes(db: AsyncSQLerDB) -> None:
    """Create the UNIQUE indexes on the ulid columns and the bucket key.

    The idempotency-key and unique-key indexes are made by ``_create_key_index``.
    """
    await db.create_index(Job.__tablename__, "ulid", unique=True, name="idx_qler_jobs_ulid")
    await db.create_index(
        JobAttempt.__tablename__, "ulid", unique=True, name="idx_qler_job_attempts_ulid"
    )
    await db.create_index(
        RateLimitBucket.__tablename__,
        "bucket_key",
        unique=True,
        name="idx_qler_rate_limit_buckets_bucket_key",
    )
    await db.create_index(ARCHIVE_TABLE, "ulid", unique=True, name="idx_qler_jobs_archive_ulid")


async def _create_indexes(db: AsyncSQLerDB) -> None:
    """Create all required indexes (idempotent)."""
    # 1. Claim: pick highest-priority pending job with earliest ETA and no pending deps
    await db.drop_index("idx_qler_jobs_claim")
    await db.create_index(
        Job.__tablename__,
        ["queue_name", ("priority", "desc"), "eta", "ulid"],
        name="idx_qler_jobs_claim",
        where="status = 'pending' AND pending_dep_count = 0",
    )
    # 2. Lease expiry: find running jobs with expired leases
    await db.create_index(
        Job.__tablename__,
        "lease_expires_at",
        name="idx_qler_jobs_lease",
        where="status = 'running'",
    )
    # 3. Correlation: look up jobs by correlation ID
    await db.create_index(
        Job.__tablename__,
        "correlation_id",
        name="idx_qler_jobs_correlation",
        where="json_extract(data, '$.correlation_id') != ''",
    )
    # 4. Attempt lookup: find attempts for a job
    await db.create_index(JobAttempt.__tablename__, "job_ulid", name="idx_qler_attempts_job")
    # 5. Idempotency and 7. uniqueness: see _create_key_index
    # 6. DLQ: find failed jobs in a specific queue
    await db.create_index(
        Job.__tablename__,
        ["queue_name", "status"],
        name="idx_qler_jobs_dlq",
        where="status = 'failed'",
    )
    # 8. Dependency resolution: find pending jobs with unresolved deps
    await db.create_index(
        Job.__tablename__,
        ["status", "pending_dep_count"],
        name="idx_qler_jobs_pending_deps",
        where="status = 'pending' AND pending_dep_count > 0",
    )
    # 9. Reverse dependency map: one edge per (parent, child); finds children by parent
    await db.create_index(
        JobDependency.__tablename__,
        ["parent_ulid", "child_ulid"],
        unique=True,
        name="idx_qler_job_dependencies_edge",
    )
    for spec in KEY_INDEXES:
        await _create_key_index(db, spec)


async def upgrade_job_deps_from_0_5_1(db: AsyncSQLerDB) -> None:
    """The upgrade from qler 0.5.1 and earlier: move the edges of the table ``qler_job_deps``
    into ``qler_job_dependencies``, and leave a view under the old name.

    ``qler_job_deps`` is not a sqler model, so these statements stay raw:

    - ``SELECT parent_ulid, child_ulid FROM qler_job_deps WHERE NOT EXISTS (...)``, the
      edges not yet in the new table, through ``db.execute_sql``; they are written with
      ``JobDependency.save_many``;
    - ``DROP TABLE qler_job_deps`` (its index goes with it) and ``CREATE VIEW qler_job_deps``,
      through ``db.adapter.execute``: ``execute_sql`` takes only reads and sqler has no
      public call that drops a table or makes a view.

    The view is for a process still on qler 0.5.1 or earlier, which knows only the old
    name. Without it, such a process starting on this file would make ``qler_job_deps``
    again, empty, and never release a job that waits on another. With it, a process
    already running reads the right children, every write of its own to the edges fails
    with SQLite's "cannot modify qler_job_deps because it is a view", and a process
    starting fails on its index ("views may not be indexed"). A file this code made
    gets the view too.

    The look for the old name, the copy, the drop and the view are one transaction, which
    takes the write lock at its start: a second process starting on the same file at the
    same time waits for it, then finds the view. Every edge is there once.
    """
    async with db.transaction():
        found = await db.execute_sql("SELECT type FROM sqlite_master WHERE name='qler_job_deps'")
        if found and found[0]["type"] == "view":
            return
        if found:
            rows = await db.execute_sql(
                "SELECT parent_ulid, child_ulid FROM qler_job_deps AS old "
                "WHERE NOT EXISTS (SELECT 1 FROM qler_job_dependencies AS new "
                "WHERE new.parent_ulid = old.parent_ulid AND new.child_ulid = old.child_ulid) "
                "ORDER BY parent_ulid, child_ulid"
            )
            if rows:
                await JobDependency.save_many(
                    [
                        JobDependency(parent_ulid=r["parent_ulid"], child_ulid=r["child_ulid"])
                        for r in rows
                    ],
                    db=db,
                )
            cur = await db.adapter.execute("DROP TABLE qler_job_deps")
            await cur.close()
        cur = await db.adapter.execute(
            "CREATE VIEW qler_job_deps AS SELECT parent_ulid, child_ulid FROM qler_job_dependencies"
        )
        await cur.close()


async def _create_key_index(db: AsyncSQLerDB, spec: KeyIndex) -> None:
    """Make the UNIQUE partial index over one job key, or say why it cannot be made.

    The look for rows that already share a key and the making of the index are one
    transaction, so no enqueue writes a duplicate between them. A database that already
    holds duplicates (written by a qler without the index) still starts: one error names
    the keys, the older non-unique index stays for the lookups, and enqueue's check under
    the write lock keeps working. The next start after the duplicates are gone makes the
    index.
    """
    async with db.transaction():
        if spec.lookup_name is not None:
            await Job.add_index(spec.field, name=spec.lookup_name, where=spec.lookup_where, db=db)
        dups = await grouped_count(
            Job.using(db, Job.__tablename__)
            .filter(F(spec.field).is_not_null() & spec.active)
            .group_by(spec.field)
            .having(F("_count") > 1)
        )
        if not dups:
            await Job.add_index(spec.field, unique=True, name=spec.name, where=spec.where, db=db)
            await db.drop_index(spec.legacy_name)
            return
        await Job.add_index(spec.field, name=spec.legacy_name, where=spec.legacy_where, db=db)
    keys = sorted(str(row[spec.field]) for row in dups)
    shown = ", ".join(keys[:_MAX_KEYS_LOGGED])
    rest = len(keys) - _MAX_KEYS_LOGGED
    more = f", and {rest} more" if rest > 0 else ""
    logger.error(
        "qler_jobs holds %d %ss that each name more than one %s job: %s%s. "
        "The unique index %s is not made, so the database does not refuse a second "
        "job with such a key; cancel or remove the extra jobs and start again.",
        len(keys),
        spec.label,
        spec.scope,
        shown,
        more,
        spec.name,
    )
