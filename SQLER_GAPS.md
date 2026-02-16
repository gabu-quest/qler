# sqler Gaps for qler

**Purpose:** Features sqler needs before qler implementation begins.  
**Status:** Pre-implementation  
**Priority:** Must-have for qler v0.1

---

## Context

qler's spec was originally written with raw SQL (`execute_raw`) for all state transitions because ChatGPT didn't have sqler's API in context. Per the -ler Stack principle, qler MUST use sqler's native API—not raw SQL through `db.adapter.execute()`. That defeats the entire purpose of the ecosystem.

sqler currently stores everything as `(_id INTEGER, data JSON, _version INTEGER)`. This works for most CRUD, but qler's job queue needs 4 capabilities that sqler doesn't have yet.

---

## Gap 1: Promoted Columns

### What

Declare certain model fields as real SQLite columns alongside the JSON blob.

### Why qler needs it

- **CHECK constraints:** `status IN ('pending','running','completed','failed','cancelled')` — prevents ghost states at the DB level, not just in Python
- **Efficient indexes:** `CREATE INDEX idx_claimable ON jobs(queue_name, priority DESC, eta, ulid) WHERE status = 'pending'` — indexes on real columns, not `json_extract()` expressions
- **Custom primary keys:** qler uses ULIDs (`TEXT UNIQUE`), not auto-increment integers
- **Cross-table references:** `job_attempts.job_ulid` references `jobs.ulid` — can't do referential integrity on JSON fields
- **Query performance:** `WHERE status = 'pending'` on a real column is faster than `WHERE json_extract(data, '$.status') = 'pending'`, especially with partial indexes

### sqler already does this

`_id` and `_version` are already named columns outside the JSON blob. This feature generalizes that pattern.

### Proposed API (aspirational — exact shape TBD)

```python
class Job(AsyncSQLerSafeModel):
    __promoted__ = {
        # ONLY fields that need real columns for indexes, CHECK, or WHERE performance
        "ulid": "TEXT UNIQUE NOT NULL",          # UNIQUE constraint, cross-table FK
        "status": "TEXT NOT NULL DEFAULT 'pending'",  # CHECK + partial index + every query
        "queue_name": "TEXT NOT NULL DEFAULT 'default'",  # claim query WHERE
        "priority": "INTEGER NOT NULL DEFAULT 0",  # claim query ORDER BY
        "eta": "INTEGER NOT NULL",               # claim query WHERE + ORDER BY
        "lease_expires_at": "INTEGER",           # recovery query WHERE
    }
    
    __checks__ = {
        "status": "status IN ('pending','running','completed','failed','cancelled')",
    }
    
    # Promoted fields — column only, NOT in JSON (same as _id and _version)
    ulid: str
    status: str = "pending"
    queue_name: str = "default"
    priority: int = 0
    eta: int = 0
    lease_expires_at: int | None = None
    
    # Everything else stays in JSON blob
    task: str = ""                       # checked after loading, not in WHERE
    worker_id: str | None = None         # ownership checks are Python-side
    finished_at: int | None = None       # purge queries are infrequent
    correlation_id: str = ""             # logler lookups can use json_extract index
    last_attempt_id: str | None = None
    payload_json: str = "{}"
    result_json: str | None = None
    last_error: str | None = None
    # ...
```

### Generated schema

```sql
CREATE TABLE jobs (
    _id INTEGER PRIMARY KEY AUTOINCREMENT,
    data JSON NOT NULL,
    _version INTEGER NOT NULL DEFAULT 0,
    
    -- Promoted columns (NOT in JSON — same as _id and _version)
    ulid TEXT UNIQUE NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending'
        CHECK(status IN ('pending','running','completed','failed','cancelled')),
    queue_name TEXT NOT NULL DEFAULT 'default',
    priority INTEGER NOT NULL DEFAULT 0,
    eta INTEGER NOT NULL,
    lease_expires_at INTEGER
);
```

### Key behaviors

- Promoted fields are **column only** — excluded from the JSON blob. Same treatment as `_id` and `_version` already get.
- `F("status") == "pending"` compiles to `status = ?` (column) instead of `json_extract(data, '$.status') = ?`
- `F("task") == "myapp.send_email"` compiles to `json_extract(data, '$.task') = ?` (JSON field — not promoted)
- `.save()` writes promoted fields to their columns, everything else to `data JSON`
- `.from_id()` / `.query().all()` reconstruct the full model from columns + JSON

### Why so few promoted fields?

Only 6 out of ~20 fields are promoted. The rest stay in JSON because:
- They're checked after loading the model (Python-side), not in SQL WHERE clauses
- Promoting everything defeats the purpose of sqler's document store — at that point just use raw SQLite
- Each promoted column is schema you need to migrate; fewer = simpler

---

## Gap 2: F-expressions in queryset.update()

### What

Support field references and expressions as values in `.update()`, not just literals.

### Why qler needs it

```python
# Currently possible (literal values only):
await Job.query().filter(F("status") == "pending").update(status="cancelled")

# Needed for qler (atomic increment):
await Job.query().filter(F("ulid") == job_ulid).update(
    attempts=F("attempts") + 1,     # Field reference + arithmetic
    retry_count=F("retry_count") + 1,
    updated_at=now_ts,               # Literal is fine here
)
```

Without this, every "increment a counter" operation requires a full read-modify-write cycle through SafeModel, which works but adds a round-trip and version contention.

### Current limitation

`queryset.update()` compiles to:
```sql
UPDATE jobs SET data = json_set(data, '$.field', ?) WHERE ...
```

It only accepts literal `?` parameter values. There's no way to reference the current value of a field.

### Proposed API

```python
from sqler import F

# F() as a value in update = field expression
await Job.query().filter(expr).update(
    attempts=F("attempts") + 1,          # json_extract + arithmetic
    eta=F("retry_delay") * 2 + now_ts,   # compound expression
    status="pending",                     # literal (unchanged behavior)
)
```

### Generated SQL (with promoted columns)

```sql
-- For promoted columns:
UPDATE jobs SET
    attempts = attempts + 1,
    eta = retry_delay * 2 + ?,
    status = 'pending'
WHERE ...

-- For JSON-only fields:
UPDATE jobs SET data = json_set(data,
    '$.attempts', json_extract(data, '$.attempts') + 1,
    '$.eta', json_extract(data, '$.retry_delay') * 2 + ?,
    '$.status', 'pending'
) WHERE ...
```

### Scope

- `F("field")` → reference to current row's field value
- `F("field") + n`, `F("field") - n`, `F("field") * n` → arithmetic
- Literal values → unchanged behavior (keep working as today)
- No need for complex expressions (CASE, subqueries) — those are niche enough that if ever needed, sqler can add them later

---

## Gap 3: Atomic Update-and-Return

### What

A queryset method that atomically updates matching rows AND returns the modified model instance(s).

### Why qler needs it

The core job claim pattern: "find the first pending job, mark it running, return it." With read-then-write, multiple workers can SELECT the same job and race on the UPDATE. With atomic update-and-return, only one worker gets the job.

```python
# Current approach (read-then-write, race-prone under high concurrency):
job = await Job.query().filter(F("status") == "pending").first()
job.status = "running"
await job.save()  # StaleVersionError if another worker got it first

# Needed (atomic, no thundering herd):
job = await Job.query().filter(
    F("status") == "pending"
    & F("queue_name").in_list(queues)
    & F("eta") <= now_ts
).order_by("-priority", "eta", "ulid").update_one(
    status="running",
    worker_id=worker_id,
    attempts=F("attempts") + 1,
    lease_expires_at=now_ts + F("lease_duration"),
)
# Returns Job instance or None. Only one worker wins.
```

### Under the hood

Uses SQLite 3.35+ `UPDATE ... RETURNING`:

```sql
UPDATE jobs SET
    status = 'running',
    worker_id = ?,
    attempts = attempts + 1,
    lease_expires_at = ? + lease_duration,
    _version = _version + 1
WHERE _id = (
    SELECT _id FROM jobs
    WHERE status = 'pending'
      AND queue_name IN (?, ?)
      AND eta <= ?
    ORDER BY priority DESC, eta ASC, ulid ASC
    LIMIT 1
)
RETURNING *
```

### Proposed API

```python
# Update first matching row, return model instance
job = await Job.query().filter(expr).order_by(...).update_one(**fields)
# Returns: Job | None

# Update all matching rows, return list of model instances
jobs = await Job.query().filter(expr).update_all_returning(**fields)
# Returns: list[Job]
```

### Relationship with Gap 2

`update_one()` naturally supports F-expressions from Gap 2 in its field values.

### Versioning

`update_one()` MUST auto-bump `_version` (same as `.save()`). The returned model instance has the updated version.

---

## Gap 4: Multi-field Ordering

### What

Support `ORDER BY field1 DESC, field2 ASC, field3 ASC` in queries.

### Why qler needs it

The claim query must be deterministic:
```sql
ORDER BY priority DESC, eta ASC, ulid ASC
```

Currently, `query.order_by(field, desc=False)` accepts one field and replaces any previous ordering.

### Proposed API

```python
# Option A: chaining appends instead of replaces
Job.query().order_by("priority", desc=True).order_by("eta").order_by("ulid")

# Option B: string syntax (Django-style, "-" prefix = DESC)
Job.query().order_by("-priority", "eta", "ulid")

# Option C: tuple syntax
Job.query().order_by(("priority", "DESC"), ("eta", "ASC"), ("ulid", "ASC"))
```

Recommendation: **Option B** — concise, widely understood from Django/Peewee.

### Impact

Low implementation effort. Internally, store a list of `(field, desc)` tuples instead of a single field.

---

## Implementation Priority

| Gap | Effort | qler Blocked? | Notes |
|-----|--------|:---:|-------|
| **Multi-field ordering** | Small | **Yes** | Claim query needs deterministic ordering |
| **Promoted columns** | Large | **Yes** | CHECK constraints + index performance are non-negotiable for correctness |
| **F-expressions in update** | Medium | **Yes** | Atomic counter increments, no safe workaround |
| **Update-and-return** | Medium | **Soft blocker** | SafeModel claim works but thundering herds under any real concurrency — see below |

### Update-and-return: Soft Blocker (Revised Assessment)

Originally classified as "No" (not blocking). Revised to **soft blocker**.

The SafeModel fallback (read → modify → save, retry on `StaleVersionError`) works in single-worker or low-contention scenarios. But with 2+ concurrent workers, every claim attempt becomes:
1. Both workers SELECT the same pending job
2. Both try UPDATE with version check
3. One wins, one retries (wasted round-trip)
4. Under sustained load, this compounds into thundering herd behavior

This is acceptable for getting started but will be the first thing you hit in integration testing with `concurrency > 1`. Prioritize shipping `update_one()` in sqler alongside or immediately after the hard blockers.

### Suggested order

1. Multi-field ordering (unblocks basic queries)
2. Promoted columns (unblocks proper schema)
3. F-expressions in update (unblocks atomic counters)
4. **Update-and-return (prioritize — soft blocker for real-world concurrency)**

---

## What qler DOESN'T need from sqler

- PostgreSQL support (SQLite is the design center)
- Migration framework (qler uses `PRAGMA user_version` + `qler init`)
- Relationship resolution (qler manages Job→Attempt references explicitly)
- Full-text search (qler uses exact-match queries)
- Caching (qler's queries are always fresh)
