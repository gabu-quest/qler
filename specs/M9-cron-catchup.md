# M9: Cron Catchup

**Status:** Spec ready
**Depends on:** M6 (cron), M8 (count fix)

---

## Problem

qler's cron scheduler looks back only 60 seconds for missed ticks (`worker.py:294`). If the worker is down for longer than that — process restart, laptop sleep, deploy window — missed cron runs are silently dropped.

This is wrong for many real use cases:
- Weekly cleanup scripts (run once on wake, not never)
- Daily report generation (must run even if deploy happened at 3am)
- Hourly data syncs (catch up the last few missed intervals)

## Solution

Add a `catchup` parameter to `@cron` that controls how missed runs are handled when the worker starts or recovers.

```python
@cron(q, "0 3 * * 0", catchup="latest")   # run most recent missed only
@cron(q, "0 * * * *", catchup=3)           # catch up to 3 missed runs
@cron(q, "*/5 * * * *", catchup=False)     # skip missed (default, current behavior)
```

## Design

### `catchup` parameter values

| Value | Meaning |
|-------|---------|
| `False` (default) | Current behavior. Skip missed runs. |
| `"latest"` | Enqueue only the most recent missed run. Sugar for `catchup=1`. |
| `int` (1-100) | Enqueue up to N missed runs, oldest first. |

`catchup=True` is intentionally not supported — unbounded catchup is almost always a bug.

### Determining "missed" runs

The scheduler needs to know when the last run was enqueued. Two options:

**Option A: Query the DB** (recommended)
```python
last_job = await Job.query().filter(
    (F("task") == path) & F("idempotency_key").startswith(f"cron:{path}:")
).order_by("-created_at").first()
```

Extract the timestamp from the idempotency key (`cron:{path}:{ts}`) or from `created_at`. This requires no schema changes — the data already exists.

**Option B: Persist last-enqueued timestamp**
Add a `CronState` model or similar. More explicit but adds a new table for one field.

Option A is preferred — zero new infrastructure, uses existing idempotency keys.

### Catchup logic (per cron task, per scheduler tick)

```
1. If catchup is False: use current 60s lookback (no change)
2. If catchup is set:
   a. Find last_enqueued_ts from DB (latest job with matching cron idempotency key prefix)
   b. If no previous job exists: treat as first run, enqueue current tick only
   c. Walk croniter forward from last_enqueued_ts
   d. Collect up to `catchup` missed timestamps where ts <= now
   e. Enqueue each with its idempotency key (dedup is automatic)
   f. After catchup is done, resume normal tick-based scheduling
```

### Idempotency safety

The existing `cron:{task_path}:{run_ts}` idempotency key already prevents duplicates. If a catchup run was already enqueued (e.g., by another worker), the enqueue is a no-op. This makes the catchup logic safe to run on every scheduler tick — it's idempotent.

### `max_running` interaction

The existing `max_running` guard applies normally. If `max_running=1` and a previous catchup job is still running, additional catchup jobs won't be enqueued until it completes. This prevents pile-up even with aggressive catchup.

### Ordering

Catchup jobs should be enqueued oldest-first so they execute in chronological order. Priority should match the cron task's configured priority (no special handling).

## API Changes

### `@cron` decorator

```python
def cron(
    queue: Queue,
    expression: str,
    *,
    max_running: int = 1,
    catchup: int | Literal["latest"] | Literal[False] = False,  # NEW
    timezone_name: str = "UTC",
    # ... existing params ...
) -> Callable[[Callable], CronWrapper]:
```

### `CronSchedule` dataclass

```python
@dataclass(frozen=True)
class CronSchedule:
    expression: str
    max_running: int = 1
    timezone: str = "UTC"
    catchup: int = 0  # NEW: 0 = disabled, N = max missed runs to catch up
```

### `CronWrapper` additions

```python
class CronWrapper:
    async def _find_last_enqueued_ts(self) -> int | None:
        """Query DB for the most recent cron job's scheduled timestamp."""
        ...

    def missed_runs(self, since_ts: int, now_ts: int) -> list[int]:
        """Walk croniter forward from since_ts, return up to catchup timestamps."""
        ...
```

## Constraints

- `catchup` max value: 100 (prevent accidental flood)
- `catchup="latest"` normalizes to `catchup=1` internally
- `catchup=True` raises `ConfigurationError` ("use an integer or 'latest'")
- First-ever run (no previous jobs): enqueue current tick only, don't try to catch up from epoch
- Catchup runs respect `max_running` — no pile-up

## Test Plan

| Test | What it verifies |
|------|-----------------|
| `test_catchup_false_skips_missed` | Default behavior unchanged — missed runs don't fire |
| `test_catchup_latest_enqueues_one` | Only most recent missed run is enqueued |
| `test_catchup_n_enqueues_up_to_n` | Exactly N oldest-first missed runs enqueued |
| `test_catchup_respects_max_running` | Catchup jobs blocked by max_running guard |
| `test_catchup_idempotent` | Running catchup twice doesn't create duplicates |
| `test_catchup_first_run_no_history` | No previous jobs → enqueue current tick only |
| `test_catchup_true_rejected` | `catchup=True` raises ConfigurationError |
| `test_catchup_exceeds_cap_rejected` | `catchup=101` raises ConfigurationError |
| `test_missed_runs_chronological` | Catchup jobs enqueued oldest-first |
| `test_catchup_with_immediate_mode` | Catchup works in immediate mode (enqueue only, no scheduling) |

## Files to Modify

| File | Changes |
|------|---------|
| `src/qler/cron.py` | Add `catchup` to `CronSchedule`, `@cron` decorator, `CronWrapper` methods |
| `src/qler/worker.py` | Update `_cron_scheduler_loop` with catchup logic |
| `tests/test_cron.py` | Add 10+ catchup tests |
| `ROADMAP.md` | Add M9 entry |

## Out of Scope

- Persistent cron state table (unnecessary — idempotency keys are sufficient)
- Calendar-aware scheduling (e.g., "last business day") — use croniter extensions later
- Distributed cron lock (single-worker assumption, same as today)
