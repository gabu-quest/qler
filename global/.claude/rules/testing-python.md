---
description: "Python/pytest testing patterns — concrete bad-to-good rewrites, fixtures, parametrization"
paths:
  - "**/*.py"
---

# Python Testing Patterns

Extends `testing-core.md` with Python/pytest-specific guidance. See core for universal anti-patterns and banned assertions.

## Correct Patterns

```python
# GOOD: Assert specific values
assert result["status"] == "success"

# GOOD: Assert exact counts before iterating
assert len(result["results"]) == 5
for item in result["results"]:
    assert item["level"] == "ERROR"

# GOOD: Parametrize with exact expected values
@pytest.mark.parametrize("level,expected_count", [
    ("ERROR", 25),
    ("INFO", 25),
    ("DEBUG", 25),
])
def test_search_level_exact(self, deterministic_log, level, expected_count):
    result = search(files=[deterministic_log], level=level)
    assert len(result["results"]) == expected_count
    for item in result["results"]:
        assert item["level"] == level
```

## Deterministic Fixtures

Create fixtures with **known, exact values**:

```python
@pytest.fixture
def deterministic_log():
    """100 entries: 25 each of INFO/DEBUG/WARN/ERROR.
    Lines 0,4,8...: INFO from worker-0
    Lines 1,5,9...: DEBUG from worker-1
    ...
    """
    # Return path to generated file with exact, known content
```

## CONCRETE REWRITE EXAMPLES (from real audit findings)

Every banned pattern found in audits, with the exact rewrite:

| Found in audit | Rewrite to |
|----------------|-----------|
| `assert result is not None` (alone) | `assert result == expected_path` |
| `assert isinstance(result, dict)` (alone) | `assert result["status"] == "success"` |
| `assert "key" in result` (alone) | `assert result["key"] == expected_value` |
| `assert len(results) > 0` (alone) | `assert len(results) == 5` |
| `for item in results: assert ...` (no length check) | `assert len(results) == 5; for item in results: assert ...` |
| `assert thread is not None` (alone) | `assert thread["thread_id"] == "worker-1"` |
| `assert cluster is not None` (alone) | `assert cluster.count == 3` |

```python
# BEFORE (softball):
result = find_config(nested)
assert result is not None

# AFTER (meaningful):
result = find_config(nested)
assert result is not None          # guard
assert result == config_file       # proof - exact path match

# BEFORE (softball):
thread = tracker.get_thread("worker-1")
assert thread is not None

# AFTER (meaningful):
thread = tracker.get_thread("worker-1")
assert thread is not None                # guard
assert thread["thread_id"] == "worker-1" # proof - correct thread returned
assert thread["log_count"] == 1          # proof - exact count

# BEFORE (softball - iteration without length):
for entry in entries:
    assert entry.get("thread_id") == "worker-1"

# AFTER (meaningful):
assert len(entries) == 20                # guard - known exact count
for entry in entries:
    assert entry.get("thread_id") == "worker-1"
```

## pytest Best Practices

- Use `pytest.raises(SpecificError, match="pattern")` not bare `Exception`
- Use `@pytest.fixture` for test data, not inline setup
- Use `@pytest.mark.parametrize` for testing multiple inputs
- Run with `uv run pytest` (see `python.md`)
