"""Benchmark runner — discovers, executes, and saves results."""

from __future__ import annotations

import gc
import json
import time
import traceback
from datetime import datetime, timezone
from pathlib import Path

from .base import BenchmarkResult, Scenario
from .config import BenchmarkConfig
from .hygiene import validate_result
from .system import SystemInfo


class BenchmarkRunner:
    """Discovers and runs benchmark scenarios, saves results."""

    def __init__(self, config: BenchmarkConfig):
        self.config = config
        self.system_info = SystemInfo.collect()

    def run(self, scenarios: list[Scenario]) -> list[BenchmarkResult]:
        """Execute all scenarios and return combined results."""
        all_results: list[BenchmarkResult] = []
        total = len(scenarios)

        for i, scenario in enumerate(scenarios, 1):
            label = f"[{i}/{total}] {scenario.suite}/{scenario.name}"
            print(f"\n{'='*60}")
            print(f"  {label}")
            print(f"  {scenario.description}")
            print(f"{'='*60}")

            # GC between scenarios for clean baseline
            gc.collect()

            try:
                scenario.setup(self.config)
                start = time.perf_counter()
                results = scenario.run(self.config)
                elapsed = time.perf_counter() - start
                scenario.teardown()

                # Validate each result for hygiene violations
                for r in results:
                    issues = validate_result(r)
                    for issue in issues:
                        print(f"  [hygiene] {issue}")

                all_results.extend(results)
                print(f"  -> {len(results)} results in {elapsed:.1f}s")

                if self.config.verbose:
                    for r in results:
                        p95_str = (
                            f"{r.timing.p95_ms:.2f}"
                            if r.timing.reliable_p95
                            else "n/a"
                        )
                        print(
                            f"     {r.parameter}={r.value}: "
                            f"median={r.timing.median_ms:.2f}ms "
                            f"p95={p95_str}ms"
                        )
            except Exception as e:
                print(f"  !! FAILED: {e}")
                traceback.print_exc()
                scenario.teardown()

        return all_results

    def save_results(self, results: list[BenchmarkResult], tag: str | None = None) -> Path:
        """Save results to JSON file, return the path."""
        output_dir = Path(self.config.output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)

        ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        suffix = f"_{tag}" if tag else ""
        filename = f"bench_{self.config.scale.name}{suffix}_{ts}.json"
        path = output_dir / filename

        payload = {
            "system": self.system_info.to_dict(),
            "config": {
                "scale": self.config.scale.name,
                "warmup": self.config.warmup,
                "iterations": self.config.iterations,
            },
            "results": [r.to_dict() for r in results],
            "summary": self._build_summary(results),
        }

        path.write_text(json.dumps(payload, indent=2))

        # Also write a "latest" symlink/copy
        latest = output_dir / "latest.json"
        latest.write_text(json.dumps(payload, indent=2))

        print(f"\nResults saved to {path}")
        print(f"Latest alias: {latest}")
        return path

    def _build_summary(self, results: list[BenchmarkResult]) -> dict:
        """Build a summary of all results by suite."""
        by_suite: dict[str, list[dict]] = {}
        for r in results:
            if r.suite not in by_suite:
                by_suite[r.suite] = []
            by_suite[r.suite].append({
                "scenario": r.scenario,
                "parameter": r.parameter,
                "value": r.value,
                "median_ms": r.timing.median_ms,
                "p95_ms": r.timing.p95_ms,
                "throughput": r.throughput,
            })
        return {
            "total_scenarios": len({r.scenario for r in results}),
            "total_measurements": len(results),
            "suites": by_suite,
        }
