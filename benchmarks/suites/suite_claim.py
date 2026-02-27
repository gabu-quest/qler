"""Claim benchmark suite — scenarios 5-8."""

from __future__ import annotations

import asyncio
import tempfile
from pathlib import Path

from benchmarks.core.base import BenchmarkResult
from benchmarks.core.config import BenchmarkConfig
from benchmarks.core.timer import AsyncPrecisionTimer
from benchmarks.generators.payloads import PayloadGenerator

SUITE_NAME = "claim"
TASK_PATH = "bench.noop"
WORKER_ID = "bench-worker-001"


def _make_queue(db_path: str):
    """Create a Queue pointing at the given DB file."""
    from qler import Queue
    return Queue(db=db_path)


async def _prefill_queue(q, count: int, gen: PayloadGenerator) -> None:
    """Enqueue `count` jobs into the queue."""
    payloads = gen.generate("small", count)
    # Use batch enqueue for speed when prefilling
    batch_size = 500
    for start in range(0, count, batch_size):
        batch = payloads[start:start + batch_size]
        specs = [
            {"task_path": TASK_PATH, "args": args, "kwargs": kwargs}
            for args, kwargs in batch
        ]
        await q.enqueue_many(specs)


class ClaimFromDepth:
    """Scenario 5: Pre-fill queue to depth D, then claim_job() once."""

    name = "claim_from_depth"
    suite = SUITE_NAME
    description = "claim_job() from queues at increasing depth, testing index performance"

    def setup(self, config: BenchmarkConfig) -> None:
        self._tmp_dirs: list[tempfile.TemporaryDirectory] = []

    def run(self, config: BenchmarkConfig) -> list[BenchmarkResult]:
        return asyncio.run(self._run(config))

    async def _run(self, config: BenchmarkConfig) -> list[BenchmarkResult]:
        results = []
        timer = AsyncPrecisionTimer(warmup=config.warmup, iterations=config.iterations)

        for depth in config.scale.queue_depths:
            # Pre-fill a DB, then measure claim from it
            async def do_claim(depth=depth):
                tmp = tempfile.TemporaryDirectory()
                self._tmp_dirs.append(tmp)
                db_path = str(Path(tmp.name) / "bench.db")
                q = _make_queue(db_path)
                await q.init_db()
                await _prefill_queue(q, depth, PayloadGenerator(seed=42))

                # Measure N claims (each from same pre-filled queue)
                claimed = []
                for i in range(20):
                    job = await q.claim_job(f"{WORKER_ID}-{i}", ["default"])
                    if job:
                        claimed.append(job)

                await q.close()
                return len(claimed)

            # For this scenario, we do manual timing since each iteration
            # needs its own pre-filled queue
            import time
            timings_ms: list[float] = []

            # Warmup
            for _ in range(config.warmup):
                await do_claim()

            # Measured runs
            for _ in range(config.iterations):
                tmp = tempfile.TemporaryDirectory()
                self._tmp_dirs.append(tmp)
                db_path = str(Path(tmp.name) / "bench.db")
                q = _make_queue(db_path)
                await q.init_db()
                await _prefill_queue(q, depth, PayloadGenerator(seed=42))

                start = time.perf_counter()
                job = await q.claim_job(WORKER_ID, ["default"])
                elapsed = (time.perf_counter() - start) * 1000
                timings_ms.append(elapsed)

                await q.close()

            stats = timer._compute_stats(timings_ms)

            results.append(BenchmarkResult(
                scenario=self.name, suite=self.suite,
                parameter="queue_depth", value=depth,
                timing=stats, rows=1,
                throughput=round(1000 / stats.median_ms, 1) if stats.median_ms > 0 else 0,
                metadata={"operation": "single_claim"},
            ))

        return results

    def teardown(self) -> None:
        for tmp in getattr(self, "_tmp_dirs", []):
            tmp.cleanup()
        self._tmp_dirs = []


class ClaimBatchScaling:
    """Scenario 6: Pre-fill queue, claim_jobs(n) at varying n."""

    name = "claim_batch_scaling"
    suite = SUITE_NAME
    description = "Batch claim_jobs(n) at varying n from pre-filled queue"

    def setup(self, config: BenchmarkConfig) -> None:
        self._tmp_dirs: list[tempfile.TemporaryDirectory] = []

    def run(self, config: BenchmarkConfig) -> list[BenchmarkResult]:
        return asyncio.run(self._run(config))

    async def _run(self, config: BenchmarkConfig) -> list[BenchmarkResult]:
        results = []
        timer = AsyncPrecisionTimer(warmup=config.warmup, iterations=config.iterations)
        # Use a medium depth so we don't run out of jobs to claim
        prefill_depth = max(config.scale.queue_depths)

        for batch_size in config.scale.batch_sizes:
            import time
            timings_ms: list[float] = []

            # Warmup
            for _ in range(config.warmup):
                tmp = tempfile.TemporaryDirectory()
                self._tmp_dirs.append(tmp)
                db_path = str(Path(tmp.name) / "bench.db")
                q = _make_queue(db_path)
                await q.init_db()
                await _prefill_queue(q, prefill_depth, PayloadGenerator(seed=42))
                await q.claim_jobs(WORKER_ID, ["default"], n=batch_size)
                await q.close()

            # Measured runs
            for _ in range(config.iterations):
                tmp = tempfile.TemporaryDirectory()
                self._tmp_dirs.append(tmp)
                db_path = str(Path(tmp.name) / "bench.db")
                q = _make_queue(db_path)
                await q.init_db()
                await _prefill_queue(q, prefill_depth, PayloadGenerator(seed=42))

                start = time.perf_counter()
                jobs = await q.claim_jobs(WORKER_ID, ["default"], n=batch_size)
                elapsed = (time.perf_counter() - start) * 1000
                timings_ms.append(elapsed)

                await q.close()

            stats = timer._compute_stats(timings_ms)
            actual_claimed = min(batch_size, prefill_depth)
            throughput = actual_claimed / (stats.median_ms / 1000) if stats.median_ms > 0 else 0

            results.append(BenchmarkResult(
                scenario=self.name, suite=self.suite,
                parameter="batch_size", value=batch_size,
                timing=stats, rows=actual_claimed,
                throughput=round(throughput, 1),
            ))

        return results

    def teardown(self) -> None:
        for tmp in getattr(self, "_tmp_dirs", []):
            tmp.cleanup()
        self._tmp_dirs = []


class ClaimWithDeps:
    """Scenario 7: Queue has mix of ready and blocked jobs."""

    name = "claim_with_deps"
    suite = SUITE_NAME
    description = "Claim from queue with mix of ready and blocked (dep) jobs, testing partial index"

    def setup(self, config: BenchmarkConfig) -> None:
        self._tmp_dirs: list[tempfile.TemporaryDirectory] = []

    def run(self, config: BenchmarkConfig) -> list[BenchmarkResult]:
        return asyncio.run(self._run(config))

    async def _run(self, config: BenchmarkConfig) -> list[BenchmarkResult]:
        results = []
        timer = AsyncPrecisionTimer(warmup=config.warmup, iterations=config.iterations)

        for depth in config.scale.queue_depths:
            import time
            timings_ms: list[float] = []

            for phase in range(config.warmup + config.iterations):
                tmp = tempfile.TemporaryDirectory()
                self._tmp_dirs.append(tmp)
                db_path = str(Path(tmp.name) / "bench.db")
                q = _make_queue(db_path)
                await q.init_db()

                # Enqueue half as "parent" jobs (ready)
                ready_count = depth // 2
                blocked_count = depth - ready_count
                gen = PayloadGenerator(seed=42)

                # Ready jobs
                ready_payloads = gen.generate("small", ready_count)
                if ready_payloads:
                    specs = [
                        {"task_path": TASK_PATH, "args": args, "kwargs": kwargs}
                        for args, kwargs in ready_payloads
                    ]
                    parent_jobs = await q.enqueue_many(specs)
                else:
                    parent_jobs = []

                # Blocked jobs (depend on first parent)
                if parent_jobs and blocked_count > 0:
                    blocked_payloads = gen.generate("small", blocked_count)
                    for args, kwargs in blocked_payloads:
                        await q.enqueue(
                            TASK_PATH, args=args, kwargs=kwargs,
                            depends_on=[parent_jobs[0].ulid],
                        )

                if phase < config.warmup:
                    await q.close()
                    continue

                start = time.perf_counter()
                job = await q.claim_job(WORKER_ID, ["default"])
                elapsed = (time.perf_counter() - start) * 1000
                timings_ms.append(elapsed)

                await q.close()

            stats = timer._compute_stats(timings_ms)
            results.append(BenchmarkResult(
                scenario=self.name, suite=self.suite,
                parameter="queue_depth", value=depth,
                timing=stats, rows=1,
                throughput=round(1000 / stats.median_ms, 1) if stats.median_ms > 0 else 0,
                metadata={"ready_fraction": 0.5},
            ))

        return results

    def teardown(self) -> None:
        for tmp in getattr(self, "_tmp_dirs", []):
            tmp.cleanup()
        self._tmp_dirs = []


class ClaimContested:
    """Scenario 8: Sequential claim_job() calls with different worker_ids."""

    name = "claim_contested"
    suite = SUITE_NAME
    description = "Sequential claim_job() with N different worker_ids, measuring contention"

    def setup(self, config: BenchmarkConfig) -> None:
        self._tmp_dirs: list[tempfile.TemporaryDirectory] = []

    def run(self, config: BenchmarkConfig) -> list[BenchmarkResult]:
        return asyncio.run(self._run(config))

    async def _run(self, config: BenchmarkConfig) -> list[BenchmarkResult]:
        results = []
        timer = AsyncPrecisionTimer(warmup=config.warmup, iterations=config.iterations)
        worker_counts = (1, 2, 4, 8)
        prefill_depth = max(config.scale.queue_depths)

        for n_workers in worker_counts:
            import time
            timings_ms: list[float] = []

            for phase in range(config.warmup + config.iterations):
                tmp = tempfile.TemporaryDirectory()
                self._tmp_dirs.append(tmp)
                db_path = str(Path(tmp.name) / "bench.db")
                q = _make_queue(db_path)
                await q.init_db()
                await _prefill_queue(q, prefill_depth, PayloadGenerator(seed=42))

                if phase < config.warmup:
                    for w in range(n_workers):
                        await q.claim_job(f"worker-{w}", ["default"])
                    await q.close()
                    continue

                start = time.perf_counter()
                for w in range(n_workers):
                    await q.claim_job(f"worker-{w}", ["default"])
                elapsed = (time.perf_counter() - start) * 1000
                timings_ms.append(elapsed)

                await q.close()

            stats = timer._compute_stats(timings_ms)
            throughput = n_workers / (stats.median_ms / 1000) if stats.median_ms > 0 else 0

            results.append(BenchmarkResult(
                scenario=self.name, suite=self.suite,
                parameter="workers", value=n_workers,
                timing=stats, rows=n_workers,
                throughput=round(throughput, 1),
                metadata={"queue_depth": prefill_depth},
            ))

        return results

    def teardown(self) -> None:
        for tmp in getattr(self, "_tmp_dirs", []):
            tmp.cleanup()
        self._tmp_dirs = []


SUITE = [ClaimFromDepth(), ClaimBatchScaling(), ClaimWithDeps(), ClaimContested()]
