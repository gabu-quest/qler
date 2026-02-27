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


def get_scenarios(suite_names: list[str] | None = None) -> list:
    """Get scenarios for the given suites, or all if None."""
    if suite_names is None:
        suite_names = list(ALL_SUITES.keys())
    scenarios = []
    for name in suite_names:
        if name not in ALL_SUITES:
            raise ValueError(f"Unknown suite: {name!r} (choose from {list(ALL_SUITES)})")
        scenarios.extend(ALL_SUITES[name])
    return scenarios
