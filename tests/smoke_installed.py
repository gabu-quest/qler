"""Run against an installed qler, with nothing but what `pip install qler` brings.

CI builds the wheel, installs it alone into a new environment and runs this file with
that environment's Python, from a folder that is not the checkout. It fails when the
package needs something its metadata does not declare: every module is imported, and
one job is enqueued and run.

Not collected by pytest (the name does not start with `test_`): the test environment
has the development packages, which is what this check must not have.
"""

from __future__ import annotations

import asyncio
import importlib
import pkgutil
import sys
from pathlib import Path

from sqler import AsyncSQLerDB

from qler import JobStatus, Queue, task

db = AsyncSQLerDB.in_memory()
queue = Queue(db, immediate=True)


@task(queue)
async def add(a: int, b: int) -> int:
    return a + b


def import_every_module() -> list[str]:
    import qler

    names = [qler.__name__]
    for module in pkgutil.walk_packages(qler.__path__, prefix="qler."):
        importlib.import_module(module.name)
        names.append(module.name)
    return names


async def run_one_job() -> None:
    job = await add.enqueue(2, 3)
    assert job.status == JobStatus.COMPLETED.value, job.status
    assert job.result == 5, job.result
    await db.close()


def main() -> int:
    import qler

    checkout = Path(__file__).resolve().parent.parent
    if checkout in Path(qler.__file__).resolve().parents:
        print(f"qler was imported from the checkout ({qler.__file__}), not from an install")
        return 1
    names = import_every_module()
    asyncio.run(run_one_job())
    print(f"qler from {Path(qler.__file__).parent}: {len(names)} modules imported, one job run")
    return 0


if __name__ == "__main__":
    sys.exit(main())
