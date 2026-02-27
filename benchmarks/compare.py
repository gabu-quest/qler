"""Report generator: comparison JSON → COMPARISON.md.

Reads the latest comparison benchmark results and generates a committed
markdown file with tables, narrative, and honest caveats.

Usage: uv run --group bench python -m benchmarks compare
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path


RESULTS_DIR = Path("benchmarks/results")
OUTPUT_PATH = Path("benchmarks/COMPARISON.md")

CAVEATS = """\
## Caveats (Read First)

Before drawing conclusions, understand what this comparison is and isn't:

1. **Architecture**: qler = same-process SQLite, zero network hops. \
Celery = separate Redis process, TCP loopback. Different tradeoffs, not just different speeds.
2. **Single machine only**: qler cannot distribute across machines. Celery can. Not tested here.
3. **SQLite write ceiling**: SQLite tops out at ~1-5K writes/sec. Redis handles 100K+. \
At extreme throughput, Celery wins. We test moderate loads (100-5K jobs).
4. **Solo pool**: Celery uses `--pool solo` (single-threaded) for fair c=1 comparison. \
Real deployments use `prefork` with multiple worker processes.
5. **Localhost Redis**: No real network latency. Production Redis is often remote \
(+0.1-1ms per roundtrip). This *understates* the real-world gap.
6. **Cold state**: Fresh DB/Redis each iteration. Production has warm caches and \
accumulated data.

**Framing**: "Should I add Redis to my stack just for background jobs, or is SQLite \
enough? If you're on one machine processing < 1K jobs/minute, this shows what you \
gain (simplicity) and what it costs (throughput ceiling)."
"""

WHEN_TO_USE = """\
## When to Use Which

| Scenario | Recommendation |
|----------|---------------|
| Single machine, < 1K jobs/min | **qler** — no infrastructure, same-process, debuggable |
| Single machine, > 5K jobs/min | **Celery+Redis** — SQLite write ceiling becomes a bottleneck |
| Multi-machine / distributed | **Celery+Redis** — qler can't distribute |
| Development / prototyping | **qler** — zero setup, `pip install qler` and go |
| Already running Redis | **Celery** — marginal cost of adding a queue is near zero |
| Debugging / observability first | **qler** — full SQL access to every job, attempt, failure |
"""


def _find_comparison_results(results_dir: Path) -> dict | None:
    """Find the most recent results JSON that contains comparison data."""
    latest = results_dir / "latest.json"
    if latest.exists():
        data = json.loads(latest.read_text())
        comparison_results = [
            r for r in data.get("results", [])
            if r.get("suite") == "comparison"
        ]
        if comparison_results:
            data["results"] = comparison_results
            return data

    # Fall back to scanning timestamped files
    json_files = sorted(results_dir.glob("bench_*.json"), reverse=True)
    for path in json_files:
        data = json.loads(path.read_text())
        comparison_results = [
            r for r in data.get("results", [])
            if r.get("suite") == "comparison"
        ]
        if comparison_results:
            data["results"] = comparison_results
            return data

    return None


def _pair_results(results: list[dict]) -> dict[str, dict[str, dict]]:
    """Group results into paired (qler, celery) entries by scenario+value.

    Returns: {scenario: {value: {"qler": result, "celery": result}}}
    """
    paired: dict[str, dict[str, dict]] = {}

    for r in results:
        scenario = r["scenario"]
        value = str(r["value"])
        system = r.get("metadata", {}).get("system", "unknown")

        if scenario not in paired:
            paired[scenario] = {}
        if value not in paired[scenario]:
            paired[scenario][value] = {}
        paired[scenario][value][system] = r

    return paired


def _format_number(n: float, precision: int = 2) -> str:
    """Format a number with commas and specified precision."""
    if n >= 1000:
        return f"{n:,.{precision}f}"
    return f"{n:.{precision}f}"


def _format_throughput(n: float) -> str:
    """Format throughput as ops/sec with commas."""
    if n >= 1000:
        return f"{n:,.0f}"
    return f"{n:.1f}"


def _speedup(qler_ms: float, celery_ms: float) -> str:
    """Calculate and format speedup ratio."""
    if celery_ms <= 0 or qler_ms <= 0:
        return "n/a"
    ratio = celery_ms / qler_ms
    if ratio >= 1:
        return f"{ratio:.1f}x faster"
    return f"{1/ratio:.1f}x slower"


SCENARIO_DESCRIPTIONS = {
    "enqueue_latency": (
        "Enqueue Latency",
        "Measures the write path: serialize a job and persist (SQLite) or publish (Redis).",
    ),
    "batch_enqueue": (
        "Batch Enqueue",
        "Bulk submission: `enqueue_many()` (single SQLite transaction) vs "
        "`group().apply_async()` (pipelined Redis publishes).",
    ),
    "round_trip": (
        "Round-Trip",
        "End-to-end: enqueue → worker claims → executes → returns result. "
        "The number that matters for latency-sensitive workloads.",
    ),
    "throughput": (
        "Sustained Throughput",
        "Sequential enqueue-process-complete cycles. "
        "The number that matters for batch workloads.",
    ),
}


def generate_report(data: dict) -> str:
    """Generate the full COMPARISON.md content from benchmark data."""
    system = data.get("system", {})
    config = data.get("config", {})
    results = data["results"]
    paired = _pair_results(results)

    lines: list[str] = []

    # Header
    lines.append("# qler vs Celery+Redis: Benchmark Comparison")
    lines.append("")
    lines.append(
        "> Auto-generated by `uv run --group bench python -m benchmarks compare`. "
        "Do not edit manually — re-run to update."
    )
    lines.append("")

    # Environment
    lines.append("## Environment")
    lines.append("")
    lines.append(f"- **Python**: {system.get('python_version', 'unknown')}")
    lines.append(f"- **qler**: {system.get('qler_version', 'unknown')}")
    lines.append(f"- **SQLite**: {system.get('sqlite_version', 'unknown')}")
    lines.append(f"- **Platform**: {system.get('platform_system', '')} "
                 f"{system.get('platform_machine', '')} "
                 f"({system.get('cpu_count', '?')} cores)")

    # Celery/Redis versions (best-effort)
    try:
        import celery
        lines.append(f"- **Celery**: {celery.__version__}")
    except ImportError:
        lines.append("- **Celery**: unknown")
    try:
        import redis as redis_mod
        lines.append(f"- **redis-py**: {redis_mod.__version__}")
    except ImportError:
        lines.append("- **redis-py**: unknown")

    lines.append(f"- **Scale**: {config.get('scale', 'unknown')} "
                 f"(warmup={config.get('warmup', '?')}, "
                 f"iterations={config.get('iterations', '?')})")
    lines.append(f"- **Generated**: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}")
    lines.append("")

    # Caveats
    lines.append(CAVEATS)

    # Results by scenario
    lines.append("## Results")
    lines.append("")

    for scenario_key in ["enqueue_latency", "batch_enqueue", "round_trip", "throughput"]:
        if scenario_key not in paired:
            continue

        title, desc = SCENARIO_DESCRIPTIONS.get(
            scenario_key, (scenario_key, "")
        )
        lines.append(f"### {title}")
        lines.append("")
        lines.append(desc)
        lines.append("")

        # Table header
        lines.append(
            "| Jobs | System | Median (ms) | P95 (ms) | Throughput (ops/s) | Δ |"
        )
        lines.append(
            "|-----:|--------|------------:|---------:|-------------------:|---|"
        )

        for value in sorted(paired[scenario_key], key=lambda v: _sort_key(v)):
            pair = paired[scenario_key][value]
            qler_r = pair.get("qler")
            celery_r = pair.get("celery")

            if qler_r:
                t = qler_r["timing"]
                p95 = _format_number(t["p95_ms"]) if t.get("reliable_p95") else "—"
                lines.append(
                    f"| {value} | qler | "
                    f"{_format_number(t['median_ms'])} | "
                    f"{p95} | "
                    f"{_format_throughput(qler_r['throughput'])} | |"
                )

            if celery_r:
                t = celery_r["timing"]
                p95 = _format_number(t["p95_ms"]) if t.get("reliable_p95") else "—"
                delta = ""
                if qler_r:
                    delta = _speedup(
                        qler_r["timing"]["median_ms"],
                        celery_r["timing"]["median_ms"],
                    )
                lines.append(
                    f"| {value} | celery | "
                    f"{_format_number(t['median_ms'])} | "
                    f"{p95} | "
                    f"{_format_throughput(celery_r['throughput'])} | "
                    f"{delta} |"
                )

        lines.append("")

    # Analysis
    lines.append("## Analysis")
    lines.append("")
    lines.append(_generate_analysis(paired))
    lines.append("")

    # When to use
    lines.append(WHEN_TO_USE)

    # Footer
    lines.append("---")
    lines.append("")
    lines.append(
        "*Generated by the qler benchmark suite. "
        "Run `uv run --group bench python -m benchmarks compare` to regenerate.*"
    )
    lines.append("")

    return "\n".join(lines)


def _sort_key(value: str) -> float:
    """Sort scale point values numerically."""
    try:
        return float(value)
    except ValueError:
        return 0


def _scenario_winner(pairs: dict[str, dict]) -> tuple[str, float]:
    """Determine which system wins across scale points.

    Returns: ("qler"|"celery"|"tie", average_ratio)
    """
    qler_wins = 0
    celery_wins = 0
    ratios: list[float] = []

    for pair in pairs.values():
        q = pair.get("qler", {}).get("timing", {}).get("median_ms", 0)
        c = pair.get("celery", {}).get("timing", {}).get("median_ms", 0)
        if q > 0 and c > 0:
            ratios.append(c / q)
            if q < c:
                qler_wins += 1
            elif c < q:
                celery_wins += 1

    avg_ratio = sum(ratios) / len(ratios) if ratios else 1.0

    if qler_wins > celery_wins:
        return "qler", avg_ratio
    elif celery_wins > qler_wins:
        return "celery", avg_ratio
    return "tie", avg_ratio


def _generate_analysis(paired: dict[str, dict[str, dict]]) -> str:
    """Generate narrative analysis from paired results."""
    parts: list[str] = []

    # Enqueue analysis
    if "enqueue_latency" in paired:
        winner, ratio = _scenario_winner(paired["enqueue_latency"])
        if winner == "qler":
            parts.append(
                f"**Enqueue latency**: qler's `enqueue()` is faster than Celery's "
                f"`apply_async()` at most scale points (~{ratio:.1f}x average). "
                f"A local SQLite write avoids TCP round-trips to Redis."
            )
        elif winner == "celery":
            parts.append(
                f"**Enqueue latency**: Celery's `apply_async()` outperforms qler's "
                f"`enqueue()` at most scale points (~{1/ratio:.1f}x average). "
                f"Redis's in-memory writes overcome the TCP overhead."
            )
        else:
            parts.append(
                "**Enqueue latency**: Both systems show similar enqueue performance "
                "at these scales."
            )

    # Batch analysis
    if "batch_enqueue" in paired:
        winner, ratio = _scenario_winner(paired["batch_enqueue"])
        if winner == "qler":
            parts.append(
                f"**Batch enqueue**: qler's `enqueue_many()` dominates (~{ratio:.1f}x average). "
                f"A single SQLite transaction for the entire batch beats individual Redis "
                f"publishes. The advantage grows with batch size."
            )
        elif winner == "celery":
            parts.append(
                f"**Batch enqueue**: Celery's `group()` outperforms qler's `enqueue_many()` "
                f"(~{1/ratio:.1f}x average). Redis pipelining scales better than SQLite "
                f"batch inserts at these sizes."
            )
        else:
            parts.append(
                "**Batch enqueue**: Both systems show similar batch performance."
            )

    # Round-trip analysis
    if "round_trip" in paired:
        winner, ratio = _scenario_winner(paired["round_trip"])
        if winner == "qler":
            parts.append(
                f"**Round-trip**: qler's same-process architecture wins (~{ratio:.1f}x average). "
                f"Zero network hops for enqueue → claim → complete gives it a structural "
                f"advantage over Celery's broker round-trips."
            )
        elif winner == "celery":
            parts.append(
                f"**Round-trip**: Celery wins the end-to-end race (~{1/ratio:.1f}x average). "
                f"qler's 3 SQLite writes per job (enqueue + claim + complete) add up. "
                f"Celery's Redis-backed broker + in-memory result backend is faster per operation, "
                f"even with TCP overhead."
            )
        else:
            parts.append(
                "**Round-trip**: Both systems show similar end-to-end latency."
            )

    # Throughput analysis
    if "throughput" in paired:
        winner, ratio = _scenario_winner(paired["throughput"])
        if winner == "qler":
            parts.append(
                f"**Throughput**: qler sustains higher throughput (~{ratio:.1f}x average). "
                f"Same-process SQLite operations avoid the per-job broker overhead "
                f"that Celery pays."
            )
        elif winner == "celery":
            parts.append(
                f"**Throughput**: Celery sustains higher throughput (~{1/ratio:.1f}x average). "
                f"Redis's in-memory operations handle sustained load better than SQLite's "
                f"disk-backed writes. Note: Celery uses `--pool solo` (c=1) here — "
                f"real deployments with `prefork` would widen this gap further."
            )
        else:
            parts.append(
                "**Throughput**: Both systems show similar sustained throughput."
            )

    if not parts:
        return "No comparison data available for analysis."

    return "\n\n".join(parts)


def main() -> None:
    """Entry point for the compare subcommand."""
    data = _find_comparison_results(RESULTS_DIR)
    if data is None:
        print(
            "\n  No comparison results found in benchmarks/results/.\n"
            "  Run the comparison suite first:\n"
            "    uv run --group bench python -m benchmarks run --suite comparison\n"
        )
        sys.exit(1)

    report = generate_report(data)
    OUTPUT_PATH.write_text(report)
    print(f"\n  Comparison report written to {OUTPUT_PATH}")
    print(f"  ({len(data['results'])} measurements from {len(set(r['scenario'] for r in data['results']))} scenarios)")


if __name__ == "__main__":
    main()
