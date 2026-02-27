"""Methodology enforcement layer — all suites import this to prevent bad benchmarks."""

from __future__ import annotations

import gc
import warnings
from contextlib import contextmanager

MIN_ITERATIONS = 20
MIN_WARMUP = 3


def enforce_iterations(n: int) -> int:
    """Raise floor on iteration count; warn if caller requested fewer."""
    if n < MIN_ITERATIONS:
        warnings.warn(
            f"Requested {n} iterations, minimum is {MIN_ITERATIONS}. "
            f"Using {MIN_ITERATIONS}.",
            stacklevel=2,
        )
        return MIN_ITERATIONS
    return n


def enforce_warmup(n: int) -> int:
    """Raise floor on warmup count; warn if caller requested fewer."""
    if n < MIN_WARMUP:
        warnings.warn(
            f"Requested {n} warmup rounds, minimum is {MIN_WARMUP}. "
            f"Using {MIN_WARMUP}.",
            stacklevel=2,
        )
        return MIN_WARMUP
    return n


@contextmanager
def gc_isolation():
    """Disable GC during measured section to reduce noise."""
    gc.collect()
    gc.disable()
    try:
        yield
    finally:
        gc.enable()


def validate_result(result) -> list[str]:
    """Check a BenchmarkResult for methodology violations. Returns list of warnings."""
    issues: list[str] = []
    t = result.timing

    if t.iterations <= 0:
        issues.append(f"{result.scenario}: iterations={t.iterations} (must be > 0)")

    if t.min_ms < 0 or t.median_ms < 0 or t.mean_ms < 0:
        issues.append(f"{result.scenario}: negative timing detected")

    if t.iterations < MIN_ITERATIONS:
        issues.append(
            f"{result.scenario}: only {t.iterations} iterations "
            f"(minimum {MIN_ITERATIONS} for reliable percentiles)"
        )

    return issues
