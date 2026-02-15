---
paths:
  - "**/*.py"
---

# Python Testing Patterns

Extends `testing-core.md` with Python/pytest-specific guidance.

## Softball Patterns to NEVER Use

```python
# BAD: Type-only checks pass on ANY dict
assert isinstance(result, dict)

# BAD: Existence without behavior
assert x is not None

# BAD: Try/except that accepts any outcome
try:
    result = function()
    assert isinstance(result, dict)
except (ValueError, RuntimeError):
    pass  # "Acceptable" - NO! Define the contract!

# BAD: Empty loops pass when results are empty
for item in result.get("results", []):
    assert item["level"] == "ERROR"  # Never executes if empty!

# BAD: `or` chains accept contradictory behaviors
assert "entries" in result or "timeline" in result or isinstance(result, dict)

# WORST: Tautology - literally cannot fail
assert result is not None or result is None
```

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

## pytest Best Practices

- Use `pytest.raises(SpecificError, match="pattern")` not bare `Exception`
- Use `@pytest.fixture` for test data, not inline setup
- Use `@pytest.mark.parametrize` for testing multiple inputs
- Run with `uv run pytest` (see `python.md`)
