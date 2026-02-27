"""Base types for the benchmark framework."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from typing import Protocol, runtime_checkable


@dataclass(frozen=True, slots=True)
class TimingStats:
    """Statistical summary of timing measurements."""

    iterations: int
    min_ms: float
    max_ms: float
    mean_ms: float
    median_ms: float
    p95_ms: float
    p99_ms: float
    stddev_ms: float
    total_ms: float
    reliable_p95: bool = False

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass(slots=True)
class BenchmarkResult:
    """Single benchmark measurement at one parameter point."""

    scenario: str
    suite: str
    parameter: str
    value: int | float | str
    timing: TimingStats
    rows: int = 0
    throughput: float = 0.0  # rows/sec or ops/sec
    metadata: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        d = asdict(self)
        return d

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), indent=2)


@runtime_checkable
class Scenario(Protocol):
    """Protocol for benchmark scenarios."""

    name: str
    suite: str
    description: str

    def setup(self, config: object) -> None: ...
    def run(self, config: object) -> list[BenchmarkResult]: ...
    def teardown(self) -> None: ...
