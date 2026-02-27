"""Comparison benchmark suite — qler vs Celery+Redis.

4 paired scenarios that run identical no-op workloads through both systems.
Each scenario produces two BenchmarkResult entries with metadata["system"]
set to "qler" or "celery" for pairing.

Requires Redis on localhost:6379 and celery[redis] installed.
"""

from __future__ import annotations

import asyncio
import tempfile
import time
from pathlib import Path

from benchmarks.celery_harness import (
    CeleryWorkerContext,
    flush_redis,
    redis_available,
)
from benchmarks.core.base import BenchmarkResult
from benchmarks.core.config import BenchmarkConfig
from benchmarks.core.timer import AsyncPrecisionTimer

SUITE_NAME = "comparison"
TASK_PATH = "bench.noop"
WORKER_ID = "bench-worker-001"


def _make_queue(db_path: str):
    """Create a Queue pointing at the given DB file."""
    from qler import Queue

    return Queue(db=db_path, default_max_retries=1, default_retry_delay=1)


def _check_celery_available() -> bool:
    """Check if both Redis and celery are available."""
    try:
        from benchmarks.celery_app import app  # noqa: F401

        if app is None:
            return False
    except ImportError:
        return False
    return redis_available()


class EnqueueLatency:
    """C1: Single enqueue latency — qler vs Celery apply_async."""

    name = "enqueue_latency"
    suite = SUITE_NAME
    description = "Single enqueue() vs apply_async() latency comparison"

    def setup(self, config: BenchmarkConfig) -> None:
        self._tmp_dirs: list[tempfile.TemporaryDirectory] = []

    def run(self, config: BenchmarkConfig) -> list[BenchmarkResult]:
        return asyncio.run(self._run(config))

    async def _run(self, config: BenchmarkConfig) -> list[BenchmarkResult]:
        results = []
        timer = AsyncPrecisionTimer(warmup=config.warmup, iterations=config.iterations)

        for count in config.scale.job_counts:
            # --- qler ---
            async def do_qler(count=count):
                tmp = tempfile.TemporaryDirectory()
                self._tmp_dirs.append(tmp)
                db_path = str(Path(tmp.name) / "bench.db")
                q = _make_queue(db_path)
                await q.init_db()

                for i in range(count):
                    await q.enqueue(TASK_PATH, args=(i,))

                await q.close()

            stats = await timer.async_measure(do_qler)
            throughput = count / (stats.median_ms / 1000) if stats.median_ms > 0 else 0

            results.append(BenchmarkResult(
                scenario=self.name, suite=self.suite,
                parameter="job_count", value=count,
                timing=stats, rows=count,
                throughput=round(throughput, 1),
                metadata={"system": "qler"},
            ))

            # --- Celery ---
            from benchmarks.celery_app import noop

            timings_ms: list[float] = []

            for phase in range(config.warmup + config.iterations):
                flush_redis()
                start = time.perf_counter()
                for i in range(count):
                    noop.apply_async(args=(i,))
                elapsed = (time.perf_counter() - start) * 1000

                if phase >= config.warmup:
                    timings_ms.append(elapsed)

            stats = timer._compute_stats(timings_ms)
            throughput = count / (stats.median_ms / 1000) if stats.median_ms > 0 else 0

            results.append(BenchmarkResult(
                scenario=self.name, suite=self.suite,
                parameter="job_count", value=count,
                timing=stats, rows=count,
                throughput=round(throughput, 1),
                metadata={"system": "celery"},
            ))

        return results

    def teardown(self) -> None:
        for tmp in getattr(self, "_tmp_dirs", []):
            tmp.cleanup()
        self._tmp_dirs = []
        try:
            flush_redis()
        except Exception:
            pass


class BatchEnqueue:
    """C2: Batch enqueue — qler enqueue_many vs celery group."""

    name = "batch_enqueue"
    suite = SUITE_NAME
    description = "Batch enqueue_many() vs celery group() comparison"

    def setup(self, config: BenchmarkConfig) -> None:
        self._tmp_dirs: list[tempfile.TemporaryDirectory] = []

    def run(self, config: BenchmarkConfig) -> list[BenchmarkResult]:
        return asyncio.run(self._run(config))

    async def _run(self, config: BenchmarkConfig) -> list[BenchmarkResult]:
        results = []
        timer = AsyncPrecisionTimer(warmup=config.warmup, iterations=config.iterations)

        for count in config.scale.job_counts:
            # --- qler ---
            async def do_qler_batch(count=count):
                tmp = tempfile.TemporaryDirectory()
                self._tmp_dirs.append(tmp)
                db_path = str(Path(tmp.name) / "bench.db")
                q = _make_queue(db_path)
                await q.init_db()

                specs = [
                    {"task_path": TASK_PATH, "args": (i,)}
                    for i in range(count)
                ]
                await q.enqueue_many(specs)

                await q.close()

            stats = await timer.async_measure(do_qler_batch)
            throughput = count / (stats.median_ms / 1000) if stats.median_ms > 0 else 0

            results.append(BenchmarkResult(
                scenario=self.name, suite=self.suite,
                parameter="job_count", value=count,
                timing=stats, rows=count,
                throughput=round(throughput, 1),
                metadata={"system": "qler"},
            ))

            # --- Celery ---
            from celery import group as celery_group
            from benchmarks.celery_app import noop

            timings_ms: list[float] = []

            for phase in range(config.warmup + config.iterations):
                flush_redis()
                batch = celery_group(noop.s(i) for i in range(count))
                start = time.perf_counter()
                batch.apply_async()
                elapsed = (time.perf_counter() - start) * 1000

                if phase >= config.warmup:
                    timings_ms.append(elapsed)

            stats = timer._compute_stats(timings_ms)
            throughput = count / (stats.median_ms / 1000) if stats.median_ms > 0 else 0

            results.append(BenchmarkResult(
                scenario=self.name, suite=self.suite,
                parameter="job_count", value=count,
                timing=stats, rows=count,
                throughput=round(throughput, 1),
                metadata={"system": "celery"},
            ))

        return results

    def teardown(self) -> None:
        for tmp in getattr(self, "_tmp_dirs", []):
            tmp.cleanup()
        self._tmp_dirs = []
        try:
            flush_redis()
        except Exception:
            pass


class RoundTrip:
    """C3: Full round-trip — enqueue -> claim -> complete vs delay().get()."""

    name = "round_trip"
    suite = SUITE_NAME
    description = "End-to-end round-trip: enqueue -> process -> result"

    def setup(self, config: BenchmarkConfig) -> None:
        self._tmp_dirs: list[tempfile.TemporaryDirectory] = []

    def run(self, config: BenchmarkConfig) -> list[BenchmarkResult]:
        return asyncio.run(self._run(config))

    async def _run(self, config: BenchmarkConfig) -> list[BenchmarkResult]:
        results = []
        timer = AsyncPrecisionTimer(warmup=config.warmup, iterations=config.iterations)

        # Use smaller counts — round-trip includes processing time
        counts = [c for c in config.scale.job_counts if c <= 1_000]
        if not counts:
            counts = [config.scale.job_counts[0]]

        for count in counts:
            # --- qler ---
            async def do_qler_rt(count=count):
                tmp = tempfile.TemporaryDirectory()
                self._tmp_dirs.append(tmp)
                db_path = str(Path(tmp.name) / "bench.db")
                q = _make_queue(db_path)
                await q.init_db()

                for i in range(count):
                    await q.enqueue(TASK_PATH, args=(i,))

                for i in range(count):
                    job = await q.claim_job(f"{WORKER_ID}-{i}", ["default"])
                    if job:
                        await q.complete_job(job, f"{WORKER_ID}-{i}", result={"ok": True})

                await q.close()

            stats = await timer.async_measure(do_qler_rt)
            throughput = count / (stats.median_ms / 1000) if stats.median_ms > 0 else 0

            results.append(BenchmarkResult(
                scenario=self.name, suite=self.suite,
                parameter="job_count", value=count,
                timing=stats, rows=count,
                throughput=round(throughput, 1),
                metadata={"system": "qler"},
            ))

            # --- Celery ---
            from benchmarks.celery_app import noop

            async with CeleryWorkerContext(concurrency=1) as _ctx:
                timings_ms: list[float] = []

                for phase in range(config.warmup + config.iterations):
                    flush_redis()
                    start = time.perf_counter()
                    for i in range(count):
                        result = noop.delay(i)
                        result.get(timeout=30)
                    elapsed = (time.perf_counter() - start) * 1000

                    if phase >= config.warmup:
                        timings_ms.append(elapsed)

            stats = timer._compute_stats(timings_ms)
            throughput = count / (stats.median_ms / 1000) if stats.median_ms > 0 else 0

            results.append(BenchmarkResult(
                scenario=self.name, suite=self.suite,
                parameter="job_count", value=count,
                timing=stats, rows=count,
                throughput=round(throughput, 1),
                metadata={"system": "celery"},
            ))

        return results

    def teardown(self) -> None:
        for tmp in getattr(self, "_tmp_dirs", []):
            tmp.cleanup()
        self._tmp_dirs = []
        try:
            flush_redis()
        except Exception:
            pass


class Throughput:
    """C4: Sustained throughput — sequential full cycles."""

    name = "throughput"
    suite = SUITE_NAME
    description = "Sustained single-worker throughput: sequential full cycles"

    def setup(self, config: BenchmarkConfig) -> None:
        self._tmp_dirs: list[tempfile.TemporaryDirectory] = []

    def run(self, config: BenchmarkConfig) -> list[BenchmarkResult]:
        return asyncio.run(self._run(config))

    async def _run(self, config: BenchmarkConfig) -> list[BenchmarkResult]:
        results = []
        timer = AsyncPrecisionTimer(warmup=config.warmup, iterations=config.iterations)

        for count in config.scale.job_counts:
            # --- qler ---
            async def do_qler_throughput(count=count):
                tmp = tempfile.TemporaryDirectory()
                self._tmp_dirs.append(tmp)
                db_path = str(Path(tmp.name) / "bench.db")
                q = _make_queue(db_path)
                await q.init_db()

                for i in range(count):
                    await q.enqueue(TASK_PATH, args=(i,))
                    job = await q.claim_job(f"{WORKER_ID}-{i}", ["default"])
                    if job:
                        await q.complete_job(job, f"{WORKER_ID}-{i}", result={"ok": True})

                await q.close()

            stats = await timer.async_measure(do_qler_throughput)
            throughput = count / (stats.median_ms / 1000) if stats.median_ms > 0 else 0

            results.append(BenchmarkResult(
                scenario=self.name, suite=self.suite,
                parameter="job_count", value=count,
                timing=stats, rows=count,
                throughput=round(throughput, 1),
                metadata={"system": "qler"},
            ))

            # --- Celery ---
            from benchmarks.celery_app import noop

            async with CeleryWorkerContext(concurrency=1) as _ctx:
                timings_ms: list[float] = []

                for phase in range(config.warmup + config.iterations):
                    flush_redis()
                    start = time.perf_counter()
                    for i in range(count):
                        result = noop.delay(i)
                        result.get(timeout=30)
                    elapsed = (time.perf_counter() - start) * 1000

                    if phase >= config.warmup:
                        timings_ms.append(elapsed)

            stats = timer._compute_stats(timings_ms)
            throughput = count / (stats.median_ms / 1000) if stats.median_ms > 0 else 0

            results.append(BenchmarkResult(
                scenario=self.name, suite=self.suite,
                parameter="job_count", value=count,
                timing=stats, rows=count,
                throughput=round(throughput, 1),
                metadata={"system": "celery"},
            ))

        return results

    def teardown(self) -> None:
        for tmp in getattr(self, "_tmp_dirs", []):
            tmp.cleanup()
        self._tmp_dirs = []
        try:
            flush_redis()
        except Exception:
            pass


def check_available() -> bool:
    """Check if comparison suite dependencies are available."""
    return _check_celery_available()


SUITE = [EnqueueLatency(), BatchEnqueue(), RoundTrip(), Throughput()]
