# Quick Reference: Hypothesis (Property-Based Testing)

**One-page summary of [`testing-hypothesis.md`](../testing-hypothesis.md) — Print this for quick reference**

---

## Prime Directive

**Use Hypothesis to prove invariants across large input spaces. Use example-based tests for everything else.**

---

## When to Use (ALL THREE Required)

| Condition | Must Have | Example |
|-----------|-----------|---------|
| **1. Pure/Nearly Pure** | Deterministic, no I/O, no randomness | Parsers, validators, math |
| **2. Clear Properties** | Invariants that ALWAYS hold | Roundtrip, idempotence, bounds |
| **3. Actionable Failures** | Shrunk examples reveal bugs | "Input `a'b` breaks escaping" |

**If unsure: Don't use Hypothesis.**

---

## Good Properties

| Type | Property | Code Example |
|------|----------|--------------|
| **Roundtrip** | `decode(encode(x)) == x` | Serialization |
| **Idempotence** | `f(f(x)) == f(x)` | Normalization |
| **Invariants** | `len(result) >= 0` | Always-true facts |
| **Relationship** | `sorted(x)` has same elements | Transformations |
| **Rejection** | Invalid input raises error | Validation |

---

## Strong YES Cases

✅ Parsers / serializers (JSON, CSV, query strings)
✅ Query builders (SQL injection prevention)
✅ Validation / normalization (idempotence)
✅ Data structure invariants (BST, sets)
✅ Mathematical transformations

---

## Strong NO Cases

❌ Integration tests (network, DB)
❌ Significant I/O (filesystem, subprocess)
❌ Timing / concurrency / race conditions
❌ CLI subprocess orchestration
❌ Generated inputs don't represent reality

---

## Basic Example

```python
from hypothesis import given
from hypothesis.strategies import text

@given(text())
def test_normalize_is_idempotent(email):
    """Normalizing twice gives same result as once."""
    once = normalize_email(email)
    twice = normalize_email(once)
    assert once == twice
```

---

## Bounded Strategies (REQUIRED)

**Always limit generated sizes:**
```python
text(max_size=100)              # No huge strings
lists(..., max_size=50)         # No huge lists
integers(min_value=0, max_value=1000)  # Reasonable range
```

**Why:** Faster, easier to debug, actionable failures.

---

## Common Strategies

```python
from hypothesis.strategies import (
    text,
    integers,
    lists,
    tuples,
    sampled_from,
    builds,
)

# Simple types
text(max_size=100)
integers(min_value=0, max_value=1000)

# Collections
lists(integers(), max_size=50)
tuples(text(), integers())

# Choices
sampled_from(["admin", "user", "guest"])

# Custom
emails = builds(
    lambda user, domain: f"{user}@{domain}",
    user=text(min_size=1, max_size=20),
    domain=sampled_from(["example.com", "test.org"])
)
```

---

## Containment Rules

**Directory:**
```
tests/
├── unit/            # Regular pytest
├── integration/     # Integration tests
└── properties/      # Hypothesis ONLY
    └── test_*.py
```

**Marker:**
```python
@pytest.mark.property
@given(...)
def test_property(...):
    pass
```

**Run:**
```bash
pytest tests/properties/       # Only property tests
pytest -m "not property"       # Skip property tests
```

---

## Configuration

```toml
# pyproject.toml
[tool.hypothesis]
max_examples = 100      # Run 100 cases per test
derandomize = true      # Deterministic order
deadline = 5000         # Fail if example takes >5s
```

---

## Regression Tests

**When Hypothesis finds a bug, encode it:**
```python
from hypothesis import given, example

@given(text())
@example("edge'case")  # Found by Hypothesis, now permanent
def test_sql_escaping(user_input):
    query = build_query(user_input)
    assert "'" not in query or "\\'" in query
```

---

## Common Pitfalls

❌ **Over-constraining strategies**
- Spending 10 lines on strategy → Use example tests instead

❌ **Testing non-deterministic code**
- Hypothesis finds flaky failures → Make code deterministic first

❌ **Ignoring shrunk examples**
- Hypothesis gives you minimal failing case → Fix that exact bug

---

## Integration with pytest

```python
import pytest
from hypothesis import given
from hypothesis.strategies import text

@pytest.mark.property
@given(text(max_size=100))
def test_property(data):
    """Property must hold for all inputs."""
    result = process(data)
    assert invariant_holds(result)
```

---

## Quick Checklist

- [ ] Read [testing.md](../testing.md) first (core principles)
- [ ] Code is pure/deterministic (no I/O, no randomness)
- [ ] Can state clear property that ALWAYS holds
- [ ] Failures will be actionable (shrunk examples reveal bugs)
- [ ] Use bounded strategies (max_size, min/max values)
- [ ] Tests live in `tests/properties/`
- [ ] Mark with `@pytest.mark.property`
- [ ] Encode found bugs as `@example()` regression tests

---

## Decision Tree

```
Does code have I/O, network, or timing?
├─ YES → Use example-based tests
└─ NO → Can you state a clear property?
    ├─ NO → Use example-based tests
    └─ YES → Will shrunk failures be clear?
        ├─ NO → Use example-based tests
        └─ YES → ✅ Use Hypothesis
```

---

**Full Docs:** [testing-hypothesis.md](../testing-hypothesis.md) | **Examples:** [examples/testing/hypothesis/](../../examples/testing/hypothesis/)
