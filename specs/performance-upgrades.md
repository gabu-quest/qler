# Performance Upgrades

Summary of the performance optimization work driven by lerproof benchmarks.

## Baseline (pre-upgrades)

lerproof stress-tested the full -ler stack under chaos:

- **Peak:** 1,230 jobs/min at c=2, 200 orders (SQLite sweet spot)
- **Scale:** 265 jobs/min at c=2, 1000 orders (4.6x degradation)
- **Zero data loss** at all concurrency levels

**Primary bottleneck:** `_resolve_dependencies()` did a full table scan of all pending jobs on every completion.
**Secondary:** No job archival — completed/failed/cancelled jobs stayed in the main table forever.

## Upgrades shipped

| # | Name | Commit | Impact |
|---|------|--------|--------|
| 1 | Job archival | `afa9345` | Keeps working set small (~hundreds vs thousands of rows) |
| 2 | Reverse dependency index | `5659e74` | O(1) child lookups instead of O(n) full table scan |
| 3 | Partial index for dep resolution | `9725301` | SQLite skips non-candidate rows via `WHERE status='pending' AND pending_dep_count > 0` |
| 4 | Batch INSERT in `enqueue_many()` | `1e996d7` | Single `asave_many()` call instead of N individual saves |
| 5 | Batch claim | `1f5d4e1` | `claim_jobs(n)` + greedy semaphore in worker — one UPDATE per poll cycle instead of N |

UPGRADE-5 also required adding `update_n()` to sqler (`5c3ac30` on `feat/qler-prerequisites`).

## UPGRADE-6: Hash-based DB sharding — NOT RECOMMENDED

The original plan proposed hash-based sharding (`shard = hash(queue_name) % N`) as UPGRADE-6. After completing UPGRADE-1 through 5, the recommendation is **do not build this**:

1. **The problem it solves may no longer exist.** The 4.6x degradation at 1000 orders was caused by the issues UPGRADE-1-3 fixed (full table scans, unbounded working set). Re-benchmark before considering sharding.

2. **Complexity cost is high.** Every query path (enqueue, claim, complete, fail, retry, dep resolution, archival, metrics, CLI) would need shard-awareness. Fundamental architectural change touching nearly every file.

3. **Undermines debuggability.** One of qler's non-negotiables. With sharding, debugging requires knowing which shard a job landed on, cross-shard dependency resolution becomes a coordination problem, and CLI/logler integration gets significantly harder.

4. **Better alternatives exist.** If post-upgrade benchmarks still show a gap, connection pool tuning or WAL checkpoint optimization are much simpler levers.

## Next step

Re-benchmark with all five upgrades active:

```bash
cd ../lerproof
uv run python tests/stress.py --orders 1000 --concurrency 2
```

If throughput at 1000 orders approaches the 200-order baseline (~1,200 jobs/min), no further optimization is needed.
