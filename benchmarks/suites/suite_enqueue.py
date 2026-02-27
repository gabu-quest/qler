"""Enqueue benchmark suite — scenarios 1-4."""

from __future__ import annotations

import asyncio
import json
import tempfile
from pathlib import Path

from benchmarks.core.base import BenchmarkResult
from benchmarks.core.config import BenchmarkConfig
from benchmarks.core.timer import AsyncPrecisionTimer
from benchmarks.generators.payloads import PayloadGenerator

SUITE_NAME = "enqueue"
TASK_PATH = "bench.noop"


def _make_queue(db_path: str):
    """Create a Queue pointing at the given DB file."""
    from qler import Queue
    return Queue(db=db_path)


class EnqueueScaling:
    """Scenario 1: Single enqueue() at increasing job counts."""

    name = "enqueue_scaling"
    suite = SUITE_NAME
    description = "Single task enqueue() at increasing job counts, measuring write throughput"

    def setup(self, config: BenchmarkConfig) -> None:
        self._tmp_dirs: list[tempfile.TemporaryDirectory] = []

    def run(self, config: BenchmarkConfig) -> list[BenchmarkResult]:
        return asyncio.run(self._run(config))

    async def _run(self, config: BenchmarkConfig) -> list[BenchmarkResult]:
        results = []
        timer = AsyncPrecisionTimer(warmup=config.warmup, iterations=config.iterations)
        gen = PayloadGenerator(seed=42)

        for count in config.scale.job_counts:
            payloads = gen.generate("small", count)

            async def do_enqueue(payloads=payloads, count=count):
                tmp = tempfile.TemporaryDirectory()
                self._tmp_dirs.append(tmp)
                db_path = str(Path(tmp.name) / "bench.db")
                q = _make_queue(db_path)
                await q.init_db()
                for i in range(count):
                    args, kwargs = payloads[i]
                    await q.enqueue(TASK_PATH, args=args, kwargs=kwargs)
                await q.close()

            stats = await timer.async_measure(do_enqueue)
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


class EnqueueBatchScaling:
    """Scenario 2: enqueue_many() at varying batch sizes."""

    name = "enqueue_batch_scaling"
    suite = SUITE_NAME
    description = "Batch enqueue_many() at varying batch sizes, measuring batch INSERT benefit"

    def setup(self, config: BenchmarkConfig) -> None:
        self._tmp_dirs: list[tempfile.TemporaryDirectory] = []

    def run(self, config: BenchmarkConfig) -> list[BenchmarkResult]:
        return asyncio.run(self._run(config))

    async def _run(self, config: BenchmarkConfig) -> list[BenchmarkResult]:
        results = []
        timer = AsyncPrecisionTimer(warmup=config.warmup, iterations=config.iterations)
        gen = PayloadGenerator(seed=42)

        for batch_size in config.scale.batch_sizes:
            payloads = gen.generate("small", batch_size)
            specs = [
                {"task_path": TASK_PATH, "args": args, "kwargs": kwargs}
                for args, kwargs in payloads
            ]

            async def do_batch(specs=specs):
                tmp = tempfile.TemporaryDirectory()
                self._tmp_dirs.append(tmp)
                db_path = str(Path(tmp.name) / "bench.db")
                q = _make_queue(db_path)
                await q.init_db()
                await q.enqueue_many(specs)
                await q.close()

            stats = await timer.async_measure(do_batch)
            throughput = batch_size / (stats.median_ms / 1000) if stats.median_ms > 0 else 0

            results.append(BenchmarkResult(
                scenario=self.name, suite=self.suite,
                parameter="batch_size", value=batch_size,
                timing=stats, rows=batch_size,
                throughput=round(throughput, 1),
            ))

        return results

    def teardown(self) -> None:
        for tmp in getattr(self, "_tmp_dirs", []):
            tmp.cleanup()
        self._tmp_dirs = []


class EnqueueSingleVsBatch:
    """Scenario 3: Head-to-head — N x enqueue() vs one enqueue_many(N)."""

    name = "enqueue_single_vs_batch"
    suite = SUITE_NAME
    description = "N x enqueue() vs one enqueue_many(N), quantifying the batch win"

    def setup(self, config: BenchmarkConfig) -> None:
        self._tmp_dirs: list[tempfile.TemporaryDirectory] = []

    def run(self, config: BenchmarkConfig) -> list[BenchmarkResult]:
        return asyncio.run(self._run(config))

    async def _run(self, config: BenchmarkConfig) -> list[BenchmarkResult]:
        results = []
        timer = AsyncPrecisionTimer(warmup=config.warmup, iterations=config.iterations)
        gen = PayloadGenerator(seed=42)

        # Test at a few representative sizes
        sizes = [s for s in config.scale.batch_sizes if s >= 10]
        if not sizes:
            sizes = [config.scale.batch_sizes[-1]]

        for n in sizes:
            payloads = gen.generate("small", n)
            specs = [
                {"task_path": TASK_PATH, "args": args, "kwargs": kwargs}
                for args, kwargs in payloads
            ]

            # Single enqueue loop
            async def do_single(payloads=payloads, n=n):
                tmp = tempfile.TemporaryDirectory()
                self._tmp_dirs.append(tmp)
                db_path = str(Path(tmp.name) / "bench.db")
                q = _make_queue(db_path)
                await q.init_db()
                for i in range(n):
                    args, kwargs = payloads[i]
                    await q.enqueue(TASK_PATH, args=args, kwargs=kwargs)
                await q.close()

            single_stats = await timer.async_measure(do_single)
            single_tp = n / (single_stats.median_ms / 1000) if single_stats.median_ms > 0 else 0

            results.append(BenchmarkResult(
                scenario=self.name, suite=self.suite,
                parameter="method", value=f"single_{n}",
                timing=single_stats, rows=n,
                throughput=round(single_tp, 1),
            ))

            # Batch enqueue
            async def do_batch(specs=specs):
                tmp = tempfile.TemporaryDirectory()
                self._tmp_dirs.append(tmp)
                db_path = str(Path(tmp.name) / "bench.db")
                q = _make_queue(db_path)
                await q.init_db()
                await q.enqueue_many(specs)
                await q.close()

            batch_stats = await timer.async_measure(do_batch)
            batch_tp = n / (batch_stats.median_ms / 1000) if batch_stats.median_ms > 0 else 0

            results.append(BenchmarkResult(
                scenario=self.name, suite=self.suite,
                parameter="method", value=f"batch_{n}",
                timing=batch_stats, rows=n,
                throughput=round(batch_tp, 1),
            ))

        return results

    def teardown(self) -> None:
        for tmp in getattr(self, "_tmp_dirs", []):
            tmp.cleanup()
        self._tmp_dirs = []


class PayloadSizeImpact:
    """Scenario 4: Fixed 1K jobs, vary payload size (tiny/small/medium/large)."""

    name = "payload_size_impact"
    suite = SUITE_NAME
    description = "Fixed 1K jobs, vary payload size to measure serialization overhead"

    def setup(self, config: BenchmarkConfig) -> None:
        self._tmp_dirs: list[tempfile.TemporaryDirectory] = []

    def run(self, config: BenchmarkConfig) -> list[BenchmarkResult]:
        return asyncio.run(self._run(config))

    async def _run(self, config: BenchmarkConfig) -> list[BenchmarkResult]:
        results = []
        timer = AsyncPrecisionTimer(warmup=config.warmup, iterations=config.iterations)
        job_count = 1_000

        for profile in ("tiny", "small", "medium", "large"):
            gen = PayloadGenerator(seed=42)
            payloads = gen.generate(profile, job_count)

            # Calculate average payload size for metadata
            avg_bytes = 0
            for args, kwargs in payloads[:100]:
                avg_bytes += len(json.dumps({"args": list(args), "kwargs": kwargs}))
            avg_bytes = avg_bytes // 100

            async def do_enqueue(payloads=payloads):
                tmp = tempfile.TemporaryDirectory()
                self._tmp_dirs.append(tmp)
                db_path = str(Path(tmp.name) / "bench.db")
                q = _make_queue(db_path)
                await q.init_db()
                for args, kwargs in payloads:
                    await q.enqueue(TASK_PATH, args=args, kwargs=kwargs)
                await q.close()

            stats = await timer.async_measure(do_enqueue)
            throughput = job_count / (stats.median_ms / 1000) if stats.median_ms > 0 else 0

            results.append(BenchmarkResult(
                scenario=self.name, suite=self.suite,
                parameter="profile", value=profile,
                timing=stats, rows=job_count,
                throughput=round(throughput, 1),
                metadata={"avg_payload_bytes": avg_bytes},
            ))

        return results

    def teardown(self) -> None:
        for tmp in getattr(self, "_tmp_dirs", []):
            tmp.cleanup()
        self._tmp_dirs = []


SUITE = [EnqueueScaling(), EnqueueBatchScaling(), EnqueueSingleVsBatch(), PayloadSizeImpact()]
