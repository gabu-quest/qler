"""Precision timer with warmup, iterations, and percentile calculation."""

from __future__ import annotations

import gc
import math
import statistics
import time
import warnings
from typing import Callable

from .base import TimingStats


class PrecisionTimer:
    """High-precision timer for benchmark measurements.

    Runs warmup iterations (discarded), then measured iterations.
    Computes min/max/median/mean/p95/p99/stddev from measured timings.
    """

    def __init__(self, warmup: int = 3, iterations: int = 20):
        self.warmup = warmup
        self.iterations = iterations

    def measure(
        self, fn: Callable[[], object], *, gc_isolate: bool = True
    ) -> TimingStats:
        """Run fn with warmup + measured iterations, return stats.

        Args:
            fn: The callable to benchmark.
            gc_isolate: If True, run gc.collect() before and disable GC during
                        the measured loop to reduce noise.
        """
        # Warmup — discard results
        for _ in range(self.warmup):
            fn()

        # Measured runs
        timings_ms: list[float] = []

        if gc_isolate:
            gc.collect()
            gc.disable()

        try:
            for _ in range(self.iterations):
                start = time.perf_counter()
                fn()
                elapsed = (time.perf_counter() - start) * 1000
                timings_ms.append(elapsed)
        finally:
            if gc_isolate:
                gc.enable()

        return self._compute_stats(timings_ms)

    def measure_once(self, fn: Callable[[], object]) -> TimingStats:
        """Deprecated: delegates to measure() with min 3 iterations."""
        warnings.warn(
            "measure_once() is deprecated — use measure() with sufficient iterations",
            DeprecationWarning,
            stacklevel=2,
        )
        saved = self.iterations
        self.iterations = max(3, saved)
        try:
            return self.measure(fn)
        finally:
            self.iterations = saved

    def _compute_stats(self, timings_ms: list[float]) -> TimingStats:
        n = len(timings_ms)
        if n == 0:
            return TimingStats(
                iterations=0,
                min_ms=0, max_ms=0, mean_ms=0, median_ms=0,
                p95_ms=0, p99_ms=0, stddev_ms=0, total_ms=0,
                reliable_p95=False,
            )

        sorted_t = sorted(timings_ms)
        mean = statistics.mean(sorted_t)
        median = statistics.median(sorted_t)
        stddev = statistics.stdev(sorted_t) if n > 1 else 0.0

        # Percentiles: NaN when sample too small for meaningful estimate
        reliable = n >= 20
        if reliable:
            p95 = self._percentile(sorted_t, 0.95)
            p99 = self._percentile(sorted_t, 0.99)
        else:
            p95 = float("nan")
            p99 = float("nan")

        return TimingStats(
            iterations=n,
            min_ms=sorted_t[0],
            max_ms=sorted_t[-1],
            mean_ms=round(mean, 4),
            median_ms=round(median, 4),
            p95_ms=round(p95, 4) if not math.isnan(p95) else p95,
            p99_ms=round(p99, 4) if not math.isnan(p99) else p99,
            stddev_ms=round(stddev, 4),
            total_ms=round(sum(sorted_t), 4),
            reliable_p95=reliable,
        )

    @staticmethod
    def _percentile(sorted_data: list[float], p: float) -> float:
        """Linear interpolation percentile on sorted data."""
        n = len(sorted_data)
        if n == 1:
            return sorted_data[0]
        idx = p * (n - 1)
        lo = int(idx)
        hi = min(lo + 1, n - 1)
        frac = idx - lo
        return sorted_data[lo] + frac * (sorted_data[hi] - sorted_data[lo])


class AsyncPrecisionTimer(PrecisionTimer):
    """Async-aware precision timer for benchmarking coroutines."""

    async def async_measure(
        self, fn: Callable, *, gc_isolate: bool = True
    ) -> TimingStats:
        """Run async fn with warmup + measured iterations, return stats."""
        # Warmup — discard results
        for _ in range(self.warmup):
            await fn()

        # Measured runs
        timings_ms: list[float] = []

        if gc_isolate:
            gc.collect()
            gc.disable()

        try:
            for _ in range(self.iterations):
                start = time.perf_counter()
                await fn()
                elapsed = (time.perf_counter() - start) * 1000
                timings_ms.append(elapsed)
        finally:
            if gc_isolate:
                gc.enable()

        return self._compute_stats(timings_ms)
