"""Make the fixture ``qler_0_5_1.db``: a queue database written by released qler 0.5.1.

Run it with qler 0.5.1 and the sqler that PyPI gave it then (1.2026.4.4), never with the
code under test. In a checkout of the tag ``v0.5.1``:

    UV_NO_SOURCES=1 uv sync
    uv run --no-sync python path/to/make_qler_0_5_1_db.py OUT_DIR

It writes ``qler_0_5_1.db`` and ``qler_0_5_1.json`` (the ulid of each job by its role) into
OUT_DIR. Every row is written through qler's own calls; nothing is edited afterwards.

What the file holds:

- ``archived``: a completed job, moved to the archive table
- ``parent``: pending, with two children ``child_a`` and ``child_b`` that wait on it
- ``idem``: pending, idempotency_key "fixture-idem"
- ``uniq``: pending, unique_key "fixture-uniq"
- ``done``: claimed and completed (result {"n": 1})
- ``dead``: claimed and failed with max_retries=0
- one rate-limit bucket for the queue "default"
"""

import asyncio
import json
import sys
from importlib.metadata import version
from pathlib import Path

from qler.queue import Queue


async def main(out_dir: Path) -> None:
    assert version("qler") == "0.5.1", version("qler")
    db_path = out_dir / "qler_0_5_1.db"
    for suffix in ("", "-wal", "-shm"):
        Path(str(db_path) + suffix).unlink(missing_ok=True)

    q = Queue(str(db_path), rate_limits={"default": "1000/s"})
    await q.init_db()
    roles: dict[str, str] = {}

    first = await q.enqueue("fixture.task", args=(0,), queue_name="first")
    claimed = await q.claim_job("fixture-worker", ["first"])
    await q.complete_job(claimed, "fixture-worker", result={"n": 0})
    await asyncio.sleep(2.1)  # the archive takes jobs finished before this second
    assert await q.archive_jobs(older_than_seconds=0) == 1
    roles["archived"] = first.ulid

    done = await q.enqueue("fixture.task", args=(1,), queue_name="work")
    claimed = await q.claim_job("fixture-worker", ["work"])
    await q.complete_job(claimed, "fixture-worker", result={"n": 1})
    roles["done"] = done.ulid

    dead = await q.enqueue("fixture.task", args=(2,), queue_name="work", max_retries=0)
    claimed = await q.claim_job("fixture-worker", ["work"])
    await q.fail_job(claimed, "fixture-worker", RuntimeError("fixture failure"))
    roles["dead"] = dead.ulid

    limited = await q.enqueue("fixture.task", args=(3,))  # queue "default": makes the bucket
    claimed = await q.claim_job("fixture-worker", ["default"])
    await q.complete_job(claimed, "fixture-worker", result={"n": 3})
    roles["limited_done"] = limited.ulid

    parent = await q.enqueue("fixture.task", args=(10,), queue_name="deps")
    child_a = await q.enqueue("fixture.task", args=(11,), queue_name="deps", depends_on=[parent.ulid])
    child_b = await q.enqueue("fixture.task", args=(12,), queue_name="deps", depends_on=[parent.ulid])
    roles["parent"], roles["child_a"], roles["child_b"] = parent.ulid, child_a.ulid, child_b.ulid

    idem = await q.enqueue("fixture.task", args=(20,), queue_name="keys", idempotency_key="fixture-idem")
    uniq = await q.enqueue("fixture.task", args=(21,), queue_name="keys", unique_key="fixture-uniq")
    roles["idem"], roles["uniq"] = idem.ulid, uniq.ulid

    await q.close()
    (out_dir / "qler_0_5_1.json").write_text(json.dumps(roles, indent=2) + "\n")
    print(json.dumps(roles))


if __name__ == "__main__":
    asyncio.run(main(Path(sys.argv[1])))
