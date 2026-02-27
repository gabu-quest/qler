"""Benchmark suites registry."""

from __future__ import annotations

from .suite_claim import SUITE as CLAIM_SUITE
from .suite_enqueue import SUITE as ENQUEUE_SUITE
from .suite_lifecycle import SUITE as LIFECYCLE_SUITE

ALL_SUITES: dict[str, list] = {
    "enqueue": ENQUEUE_SUITE,
    "claim": CLAIM_SUITE,
    "lifecycle": LIFECYCLE_SUITE,
}

# Comparison suite: registered for --list even if deps are missing.
# The module uses lazy imports so it loads fine without celery/redis.
from .suite_comparison import SUITE as COMPARISON_SUITE

ALL_SUITES["comparison"] = COMPARISON_SUITE


def _comparison_skip_reason() -> str | None:
    """Return a skip reason if comparison suite can't run, or None if ready."""
    try:
        from benchmarks.celery_app import app

        if app is None:
            return "celery not installed (uv sync --group bench)"
    except ImportError:
        return "celery not installed (uv sync --group bench)"

    from benchmarks.celery_harness import redis_available

    if not redis_available():
        return "Redis not available on localhost:6379/15"

    return None


def get_scenarios(suite_names: list[str] | None = None) -> list:
    """Get scenarios for the given suites, or all if None."""
    if suite_names is None:
        # Default: only run suites that don't require external deps
        suite_names = [k for k in ALL_SUITES if k != "comparison"]
    scenarios = []
    for name in suite_names:
        if name == "comparison":
            skip = _comparison_skip_reason()
            if skip:
                print(f"\n  [skip] comparison suite: {skip}\n")
                continue
        if name not in ALL_SUITES:
            raise ValueError(f"Unknown suite: {name!r} (choose from {list(ALL_SUITES)})")
        scenarios.extend(ALL_SUITES[name])
    return scenarios
