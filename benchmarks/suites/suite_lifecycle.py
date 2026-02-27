"""Lifecycle benchmark suite — scenarios 9-12."""

from __future__ import annotations

import asyncio
import tempfile
import time
from pathlib import Path

from benchmarks.core.base import BenchmarkResult
from benchmarks.core.config import BenchmarkConfig
from benchmarks.core.timer import AsyncPrecisionTimer
from benchmarks.generators.payloads import PayloadGenerator

SUITE_NAME = "lifecycle"
TASK_PATH = "bench.noop"
WORKER_ID = "bench-worker-001"


def _make_queue(db_path: str):
    """Create a Queue pointing at the given DB file."""
    from qler import Queue
    return Queue(db=db_path, default_max_retries=1, default_retry_delay=1)


class FullCycle:
    """Scenario 9: enqueue -> claim -> complete as one unit."""

    name = "full_cycle"
    suite = SUITE_NAME
    description = "Full enqueue -> claim -> complete cycle, the real-world metric"

    def setup(self, config: BenchmarkConfig) -> None:
        self._tmp_dirs: list[tempfile.TemporaryDirectory] = []

    def run(self, config: BenchmarkConfig) -> list[BenchmarkResult]:
        return asyncio.run(self._run(config))

    async def _run(self, config: BenchmarkConfig) -> list[BenchmarkResult]:
        results = []
        timer = AsyncPrecisionTimer(warmup=config.warmup, iterations=config.iterations)

        for count in config.scale.job_counts:
            gen = PayloadGenerator(seed=42)
            payloads = gen.generate("small", count)

            async def do_cycle(payloads=payloads, count=count):
                tmp = tempfile.TemporaryDirectory()
                self._tmp_dirs.append(tmp)
                db_path = str(Path(tmp.name) / "bench.db")
                q = _make_queue(db_path)
                await q.init_db()

                # Enqueue all
                for args, kwargs in payloads:
                    await q.enqueue(TASK_PATH, args=args, kwargs=kwargs)

                # Claim and complete all
                for i in range(count):
                    job = await q.claim_job(f"{WORKER_ID}-{i}", ["default"])
                    if job:
                        await q.complete_job(job, f"{WORKER_ID}-{i}", result={"ok": True})

                await q.close()

            stats = await timer.async_measure(do_cycle)
            throughput = count / (stats.median_ms / 1000) if stats.median_ms > 0 else 0

            results.append(BenchmarkResult(
                scenario=self.name, suite=self.suite,
                parameter="job_count", value=count,
                timing=stats, rows=count,
                throughput=round(throughput, 1),
            ))

        return results

    def teardown(self) -> None:
        for tmp in getattr(self, "_tmp_dirs", []):
            tmp.cleanup()
        self._tmp_dirs = []


class RetryCycle:
    """Scenario 10: enqueue -> claim -> fail -> claim -> complete."""

    name = "retry_cycle"
    suite = SUITE_NAME
    description = "Enqueue -> claim -> fail (retry) -> claim -> complete, measuring retry overhead"

    def setup(self, config: BenchmarkConfig) -> None:
        self._tmp_dirs: list[tempfile.TemporaryDirectory] = []

    def run(self, config: BenchmarkConfig) -> list[BenchmarkResult]:
        return asyncio.run(self._run(config))

    async def _run(self, config: BenchmarkConfig) -> list[BenchmarkResult]:
        from qler import FailureKind

        results = []
        timer = AsyncPrecisionTimer(warmup=config.warmup, iterations=config.iterations)
        # Use smaller counts — retry cycle is 2x the work
        counts = [c for c in config.scale.job_counts if c <= 1_000]
        if not counts:
            counts = [config.scale.job_counts[0]]

        for count in counts:
            gen = PayloadGenerator(seed=42)
            payloads = gen.generate("tiny", count)

            async def do_retry(payloads=payloads, count=count):
                tmp = tempfile.TemporaryDirectory()
                self._tmp_dirs.append(tmp)
                db_path = str(Path(tmp.name) / "bench.db")
                q = _make_queue(db_path)
                await q.init_db()

                # Enqueue all with max_retries=1
                for args, kwargs in payloads:
                    await q.enqueue(
                        TASK_PATH, args=args, kwargs=kwargs,
                        max_retries=1, retry_delay=1,
                    )

                # First pass: claim and fail
                for i in range(count):
                    job = await q.claim_job(f"{WORKER_ID}-a{i}", ["default"])
                    if job:
                        await q.fail_job(
                            job, f"{WORKER_ID}-a{i}",
                            exc=RuntimeError("bench error"),
                            failure_kind=FailureKind.EXCEPTION,
                        )

                # Second pass: claim and complete (retry attempt)
                for i in range(count):
                    job = await q.claim_job(f"{WORKER_ID}-b{i}", ["default"])
                    if job:
                        await q.complete_job(job, f"{WORKER_ID}-b{i}", result={"ok": True})

                await q.close()

            stats = await timer.async_measure(do_retry)
            throughput = count / (stats.median_ms / 1000) if stats.median_ms > 0 else 0

            results.append(BenchmarkResult(
                scenario=self.name, suite=self.suite,
                parameter="job_count", value=count,
                timing=stats, rows=count,
                throughput=round(throughput, 1),
                metadata={"retries": 1},
            ))

        return results

    def teardown(self) -> None:
        for tmp in getattr(self, "_tmp_dirs", []):
            tmp.cleanup()
        self._tmp_dirs = []


class DependencyChain:
    """Scenario 11: Parent -> N children (depends_on), complete parent -> children claimable."""

    name = "dependency_chain"
    suite = SUITE_NAME
    description = "Parent + N children with depends_on, measuring dep resolution cost"

    def setup(self, config: BenchmarkConfig) -> None:
        self._tmp_dirs: list[tempfile.TemporaryDirectory] = []

    def run(self, config: BenchmarkConfig) -> list[BenchmarkResult]:
        return asyncio.run(self._run(config))

    async def _run(self, config: BenchmarkConfig) -> list[BenchmarkResult]:
        results = []
        timer = AsyncPrecisionTimer(warmup=config.warmup, iterations=config.iterations)
        # Children counts to test
        child_counts = [c for c in config.scale.batch_sizes if c >= 10]
        if not child_counts:
            child_counts = [config.scale.batch_sizes[-1]]

        for n_children in child_counts:
            gen = PayloadGenerator(seed=42)

            async def do_deps(n_children=n_children):
                tmp = tempfile.TemporaryDirectory()
                self._tmp_dirs.append(tmp)
                db_path = str(Path(tmp.name) / "bench.db")
                q = _make_queue(db_path)
                await q.init_db()

                # Enqueue parent
                parent = await q.enqueue(TASK_PATH, args=(0,))

                # Enqueue children depending on parent
                child_payloads = gen.generate("tiny", n_children)
                for args, kwargs in child_payloads:
                    await q.enqueue(
                        TASK_PATH, args=args, kwargs=kwargs,
                        depends_on=[parent.ulid],
                    )

                # Complete parent -> triggers dep resolution
                parent = await q.claim_job(WORKER_ID, ["default"])
                if parent:
                    await q.complete_job(parent, WORKER_ID, result={"ok": True})

                # Claim all children (should now be ready)
                for i in range(n_children):
                    child = await q.claim_job(f"{WORKER_ID}-c{i}", ["default"])
                    if child:
                        await q.complete_job(child, f"{WORKER_ID}-c{i}")

                await q.close()

            stats = await timer.async_measure(do_deps)
            total_jobs = 1 + n_children
            throughput = total_jobs / (stats.median_ms / 1000) if stats.median_ms > 0 else 0

            results.append(BenchmarkResult(
                scenario=self.name, suite=self.suite,
                parameter="children", value=n_children,
                timing=stats, rows=total_jobs,
                throughput=round(throughput, 1),
            ))

        return results

    def teardown(self) -> None:
        for tmp in getattr(self, "_tmp_dirs", []):
            tmp.cleanup()
        self._tmp_dirs = []


class ArchivalImpact:
    """Scenario 12: Full cycle N times, archive, then claim more."""

    name = "archival_impact"
    suite = SUITE_NAME
    description = "Full cycle -> archive -> claim more, showing working-set cleanup benefit"

    def setup(self, config: BenchmarkConfig) -> None:
        self._tmp_dirs: list[tempfile.TemporaryDirectory] = []

    def run(self, config: BenchmarkConfig) -> list[BenchmarkResult]:
        return asyncio.run(self._run(config))

    async def _run(self, config: BenchmarkConfig) -> list[BenchmarkResult]:
        results = []
        # Use a smaller count per phase for this multi-phase benchmark
        base_count = min(500, config.scale.job_counts[0])

        for depth in config.scale.queue_depths:
            timings_before: list[float] = []
            timings_after: list[float] = []

            for phase in range(config.warmup + config.iterations):
                tmp = tempfile.TemporaryDirectory()
                self._tmp_dirs.append(tmp)
                db_path = str(Path(tmp.name) / "bench.db")
                q = _make_queue(db_path)
                await q.init_db()
                gen = PayloadGenerator(seed=42)

                # Phase 1: Fill queue with completed jobs to create "dead weight"
                payloads = gen.generate("tiny", depth)
                batch_size = 500
                for start in range(0, depth, batch_size):
                    batch = payloads[start:start + batch_size]
                    specs = [
                        {"task_path": TASK_PATH, "args": args, "kwargs": kwargs}
                        for args, kwargs in batch
                    ]
                    await q.enqueue_many(specs)

                # Claim and complete all to make them archivable
                for i in range(depth):
                    job = await q.claim_job(f"w-{i}", ["default"])
                    if job:
                        await q.complete_job(job, f"w-{i}")

                # Add new pending jobs to claim
                new_payloads = gen.generate("tiny", base_count)
                for args, kwargs in new_payloads:
                    await q.enqueue(TASK_PATH, args=args, kwargs=kwargs)

                if phase < config.warmup:
                    await q.close()
                    continue

                # Measure claim BEFORE archival
                start = time.perf_counter()
                job = await q.claim_job("measure-worker", ["default"])
                elapsed_before = (time.perf_counter() - start) * 1000
                if job:
                    await q.complete_job(job, "measure-worker")
                timings_before.append(elapsed_before)

                # Archive completed jobs
                await q.archive_jobs(older_than_seconds=0)

                # Measure claim AFTER archival
                start = time.perf_counter()
                job = await q.claim_job("measure-worker-2", ["default"])
                elapsed_after = (time.perf_counter() - start) * 1000
                timings_after.append(elapsed_after)

                await q.close()

            timer = AsyncPrecisionTimer(warmup=0, iterations=0)
            stats_before = timer._compute_stats(timings_before)
            stats_after = timer._compute_stats(timings_after)

            results.append(BenchmarkResult(
                scenario=self.name, suite=self.suite,
                parameter="phase", value=f"before_archive_{depth}",
                timing=stats_before, rows=1,
                throughput=round(1000 / stats_before.median_ms, 1) if stats_before.median_ms > 0 else 0,
                metadata={"dead_weight": depth},
            ))
            results.append(BenchmarkResult(
                scenario=self.name, suite=self.suite,
                parameter="phase", value=f"after_archive_{depth}",
                timing=stats_after, rows=1,
                throughput=round(1000 / stats_after.median_ms, 1) if stats_after.median_ms > 0 else 0,
                metadata={"dead_weight": depth, "archived": True},
            ))

        return results

    def teardown(self) -> None:
        for tmp in getattr(self, "_tmp_dirs", []):
            tmp.cleanup()
        self._tmp_dirs = []


SUITE = [FullCycle(), RetryCycle(), DependencyChain(), ArchivalImpact()]
