"""Benchmark configuration and scale profiles."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True, slots=True)
class ScaleConfig:
    """Controls data sizes for benchmark runs."""

    name: str
    job_counts: tuple[int, ...]
    queue_depths: tuple[int, ...]
    batch_sizes: tuple[int, ...]

    @classmethod
    def small(cls) -> ScaleConfig:
        return cls(
            name="small",
            job_counts=(100, 500, 1_000, 5_000),
            queue_depths=(100, 1_000, 5_000),
            batch_sizes=(1, 10, 50, 100),
        )

    @classmethod
    def medium(cls) -> ScaleConfig:
        return cls(
            name="medium",
            job_counts=(500, 1_000, 5_000, 10_000),
            queue_depths=(1_000, 5_000, 10_000),
            batch_sizes=(1, 10, 50, 100, 500),
        )

    @classmethod
    def large(cls) -> ScaleConfig:
        return cls(
            name="large",
            job_counts=(1_000, 5_000, 10_000, 50_000),
            queue_depths=(5_000, 10_000, 50_000),
            batch_sizes=(10, 50, 100, 500, 1_000),
        )

    @classmethod
    def from_name(cls, name: str) -> ScaleConfig:
        configs = {"small": cls.small, "medium": cls.medium, "large": cls.large}
        factory = configs.get(name)
        if factory is None:
            raise ValueError(f"Unknown scale: {name!r} (choose from {list(configs)})")
        return factory()


@dataclass(slots=True)
class BenchmarkConfig:
    """Full benchmark configuration."""

    scale: ScaleConfig = field(default_factory=ScaleConfig.small)
    warmup: int = 3
    iterations: int = 20
    output_dir: str = "benchmarks/results"
    suites: list[str] | None = None  # None = all suites
    verbose: bool = False
