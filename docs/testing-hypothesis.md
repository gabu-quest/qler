# Testing Doctrine: Hypothesis (Property-Based Testing)
## When and How to Use Property-Based Testing

**Part of The Standard** | [Core Testing Doctrine](./testing.md) | **Version 1.0.0**
**Date:** 2025-12-14

---

## Prerequisites

**You MUST read [testing.md](./testing.md) first.**

This guide assumes you understand:
- Core testing principles (determinism, meaningful assertions)
- Example-based testing with pytest
- When tests should fail vs pass

This guide focuses on **when and how** to use property-based testing with Hypothesis.

**In case of conflict, core doctrine wins.**

---

## Prime Directive

**Use Hypothesis to prove invariants across large input spaces. Use example-based tests for everything else.**

Property-based testing is a power tool. Most code does not need it. When you do need it, it's invaluable.

---

## 1. What is Property-Based Testing?

**Definition:** Instead of testing specific examples, you specify **properties** (invariants) that must hold for all inputs, and the framework generates hundreds of test cases.

**Example-based test:**
```python
def test_encode_decode_example():
    """Encoding then decoding should return original value."""
    original = "hello"
    encoded = encode(original)
    decoded = decode(encoded)
    assert decoded == original
```

**Property-based test:**
```python
from hypothesis import given
from hypothesis.strategies import text

@given(text())
def test_encode_decode_roundtrip(original):
    """Encoding then decoding should ALWAYS return original value."""
    encoded = encode(original)
    decoded = decode(encoded)
    assert decoded == original
```

**Key difference:** The property-based test runs hundreds of times with automatically generated strings, including edge cases you wouldn't think of (empty strings, unicode, special characters, very long strings).

---

## 2. When to Use Hypothesis

You MUST satisfy **ALL three conditions** before using Hypothesis:

### 2.1 Condition 1: The code is pure or nearly pure

**Required traits:**
- Deterministic (same input → same output)
- No network I/O
- No filesystem I/O (except controlled temp files)
- No wall-clock time (unless frozen)
- No global state mutations
- No randomness (unless seeded)

**Rationale:** Property-based tests run hundreds of times. Non-determinism makes failures irreproducible, defeating the purpose.

**Examples:**

✅ **Good candidates:**
```python
def parse_query_string(qs: str) -> dict:
    """Parse query string into dict."""
    # Pure function, deterministic
    pass

def normalize_email(email: str) -> str:
    """Normalize email to canonical form."""
    # Pure function, deterministic
    pass

def calculate_tax(amount: Decimal, rate: Decimal) -> Decimal:
    """Calculate tax on amount."""
    # Pure function, deterministic
    pass
```

❌ **Bad candidates:**
```python
async def fetch_user_from_api(user_id: str) -> User:
    """Fetch user from external API."""
    # Network I/O, non-deterministic
    pass

def save_to_file(data: str, path: Path):
    """Save data to filesystem."""
    # Filesystem I/O, side effects
    pass

def get_current_timestamp() -> int:
    """Get current Unix timestamp."""
    # Wall-clock time, non-deterministic
    pass
```

### 2.2 Condition 2: You can state clear properties

**Required:** You must articulate properties (invariants) that ALWAYS hold.

**Good properties:**

| Type | Property Example | Code |
|------|------------------|------|
| **Roundtrip** | `decode(encode(x)) == x` | Serialization, encoding |
| **Idempotence** | `f(f(x)) == f(x)` | Normalization, deduplication |
| **Invariants** | `len(result) >= 0` | Always-true facts |
| **Relationship** | `sorted(x) has same elements as x` | Transformations |
| **Algebraic** | `f(a) + f(b) == f(a + b)` | Mathematical operations |
| **Rejection** | `parse(invalid_input) raises ValueError` | Validation |

**Anti-pattern (vague properties):**
```python
@given(text())
def test_process_does_something(input_text):
    """Process should handle input."""
    result = process(input_text)
    assert result is not None  # Too vague!
```

**Good pattern (clear property):**
```python
@given(text())
def test_normalize_email_is_lowercase(email):
    """Normalized emails are always lowercase."""
    normalized = normalize_email(email)
    assert normalized == normalized.lower()
```

### 2.3 Condition 3: Failures are actionable

**Required:** When Hypothesis finds a failing case, the **shrunk counterexample** must clearly show what broke.

**Hypothesis automatically shrinks failures** to minimal examples:
- Long strings → shortest failing string
- Large numbers → smallest failing number
- Complex structures → simplest failing structure

**Example of actionable failure:**
```
Falsifying example: test_query_builder_placeholders(
    filters=[{'field': 'name', 'op': '=', 'value': "a'b"}]
)
AssertionError: SQL injection possible - unescaped quote
```

This is actionable: you can immediately see that single quotes aren't being escaped.

**Example of non-actionable failure:**
```
Falsifying example: test_api_integration(
    data={...500 fields of generated data...}
)
httpx.ReadTimeout: Request timed out
```

This is NOT actionable: the failure is network flakiness, not a real bug.

**Rule:** If Hypothesis failures are flaky or unclear, you're testing the wrong thing with the wrong tool.

---

## 3. Strong Yes-Cases (Use Hypothesis)

You SHOULD use Hypothesis for these scenarios:

### 3.1 Parsers and serializers

**Why:** Parsing has complex edge cases (empty input, special characters, malformed data).

**Examples:**
- JSON/YAML/TOML parsers
- Custom DSL parsers
- CSV parsers with escaping
- URL query string parsers
- Markdown/rich text parsers

**Property example:**
```python
from hypothesis import given
from hypothesis.strategies import text

@given(text())
def test_json_roundtrip(data):
    """JSON encode→decode preserves all text data."""
    import json
    encoded = json.dumps(data)
    decoded = json.loads(encoded)
    assert decoded == data
```

### 3.2 Query builders and SQL generation

**Why:** SQL generation has security implications (injection) and correctness requirements (binding, escaping).

**Properties to test:**
- Placeholder count matches parameter count
- No SQL injection (quotes/semicolons are escaped)
- Generated SQL is syntactically valid
- Parameter binding is correct

**Example:**
```python
from hypothesis import given
from hypothesis.strategies import lists, text

@given(lists(text()))
def test_query_builder_prevents_sql_injection(filter_values):
    """Query builder must escape all user input."""
    query, params = build_query(filters=filter_values)
    # Check: no raw user input in SQL string
    for value in filter_values:
        assert value not in query
    # Check: all values in params (bound safely)
    assert len(params) >= len(filter_values)
```

### 3.3 Validation and normalization logic

**Why:** Validation has many edge cases. Normalization must be idempotent.

**Properties to test:**
- Idempotence: `normalize(normalize(x)) == normalize(x)`
- Rejection: invalid inputs raise errors
- Acceptance: valid inputs pass

**Example:**
```python
from hypothesis import given
from hypothesis.strategies import emails

@given(emails())
def test_email_normalization_is_idempotent(email):
    """Normalizing twice gives same result as normalizing once."""
    once = normalize_email(email)
    twice = normalize_email(once)
    assert once == twice
```

### 3.4 Data structure invariants

**Why:** Data structures have invariants that must hold across all operations.

**Examples:**
- Binary search trees (left < node < right)
- Priority queues (parent >= children)
- Sets (no duplicates)

**Property example:**
```python
from hypothesis import given
from hypothesis.strategies import lists, integers

@given(lists(integers()))
def test_set_has_no_duplicates(items):
    """Adding items to a set removes duplicates."""
    result = to_set(items)
    assert len(result) == len(set(items))
```

### 3.5 Mathematical transformations

**Why:** Math operations have algebraic properties.

**Examples:**
- Commutative: `f(a, b) == f(b, a)`
- Associative: `f(f(a, b), c) == f(a, f(b, c))`
- Monotonic: `a < b implies f(a) < f(b)`

---

## 4. Strong No-Cases (Do NOT Use Hypothesis)

You MUST NOT use Hypothesis for:

### 4.1 Integration tests

**Why:** Integration tests are non-deterministic (network, database, timing).

**Examples:**
- ❌ Testing real HTTP endpoints
- ❌ Testing real database queries
- ❌ Testing external API calls

**Alternative:** Use example-based integration tests with controlled fixtures.

### 4.2 Tests with significant I/O

**Why:** I/O is expensive and non-deterministic.

**Examples:**
- ❌ Filesystem operations (except controlled temp files)
- ❌ Network calls
- ❌ Subprocess execution

**Alternative:** Test I/O boundaries with example-based tests.

### 4.3 Tests involving timing or concurrency

**Why:** Timing is non-deterministic. Hypothesis can't shrink timing-related failures meaningfully.

**Examples:**
- ❌ Sleep-based synchronization
- ❌ Race condition testing
- ❌ Timeout testing

**Alternative:** Use explicit synchronization primitives and example-based tests.

### 4.4 CLI and subprocess orchestration

**Why:** Subprocesses are slow and environment-dependent.

**Examples:**
- ❌ Testing CLI arg parsing via subprocess
- ❌ Testing shell script execution

**Alternative:** Test CLI logic directly (parse args in-process).

### 4.5 Tests where generated inputs don't represent reality

**Why:** If you spend more time constraining strategies than testing, you're using the wrong tool.

**Example:**
```python
# Anti-pattern: fighting Hypothesis
@given(text(min_size=10, max_size=50, alphabet=string.ascii_letters))
def test_username_validation(username):
    # Spent 10 lines constraining strategy
    # Could have just written 3 example tests
    pass
```

**Alternative:** Use example-based tests with realistic cases.

---

## 5. Containment Rules (Mandatory)

### 5.1 Directory structure

**Rule:** Hypothesis tests MUST live in a dedicated directory.

**Required structure:**
```
tests/
├── unit/               # Regular pytest tests
├── integration/        # Integration tests
└── properties/         # Hypothesis tests ONLY
    ├── test_parsers_properties.py
    ├── test_query_builder_properties.py
    └── test_validation_properties.py
```

**Rationale:**
- Clear separation
- Easy to run/skip property tests
- Prevents accidental mixing

### 5.2 Never mix with I/O

**Rule:** Hypothesis tests MUST NOT touch:
- Real database connections
- The network
- The filesystem (except pytest `tmp_path`)
- Environment-dependent settings

**Rationale:** Property tests run hundreds of times. I/O makes them slow and flaky.

### 5.3 Marking tests

**Rule:** Hypothesis tests SHOULD be marked for selective execution.

**Pattern:**
```python
import pytest
from hypothesis import given

@pytest.mark.property
@given(...)
def test_property(...):
    pass
```

**Run only property tests:**
```bash
pytest tests/properties/ -m property
```

**Skip property tests (faster CI):**
```bash
pytest -m "not property"
```

---

## 6. Style Rules (Keep It Readable)

### 6.1 Prefer small, targeted strategies

**Rule:** Keep generated data simple and bounded.

**Good:**
```python
from hypothesis.strategies import text, integers, lists

@given(text(max_size=100))
def test_short_strings(...):
    pass

@given(lists(integers(min_value=0, max_value=1000), max_size=50))
def test_small_lists(...):
    pass
```

**Bad:**
```python
@given(text())  # Unbounded - can generate huge strings
def test_huge_strings(...):
    # Slow, hard to debug
    pass
```

**Rationale:** Small strategies are faster and easier to debug.

### 6.2 Limit generated sizes

**Rule:** Use `max_size`, `min_value`, `max_value` to keep tests fast.

**Default limits:**
```python
text(max_size=100)           # No strings longer than 100 chars
lists(..., max_size=50)      # No lists longer than 50 items
integers(min_value=-1000, max_value=1000)  # Reasonable range
```

### 6.3 Use normalization in assertions

**Rule:** Compare semantic meaning, not raw formatting.

**Good:**
```python
@given(text())
def test_json_roundtrip(data):
    """JSON roundtrip preserves data."""
    import json
    encoded = json.dumps(data)
    decoded = json.loads(encoded)
    assert decoded == data  # Compare parsed values
```

**Bad:**
```python
@given(text())
def test_json_formatting(data):
    """JSON format is stable."""
    import json
    encoded = json.dumps(data)
    re_encoded = json.dumps(json.loads(encoded))
    assert encoded == re_encoded  # Fragile! Whitespace, key order, etc.
```

### 6.4 Document your strategies

**Rule:** Complex strategies MUST have comments explaining constraints.

**Good:**
```python
from hypothesis.strategies import builds, text, integers

# Strategy: valid email addresses (simplified)
emails = builds(
    lambda user, domain: f"{user}@{domain}",
    user=text(min_size=1, max_size=20, alphabet=string.ascii_letters),
    domain=text(min_size=3, max_size=20, alphabet=string.ascii_letters)
)

@given(emails)
def test_email_validation(email):
    """Validator accepts all generated emails."""
    assert is_valid_email(email)
```

---

## 7. Integration with pytest

### 7.1 Configuration

**Rule:** Configure Hypothesis in `pyproject.toml` or `pytest.ini`.

**Recommended settings:**
```toml
# pyproject.toml
[tool.hypothesis]
max_examples = 100           # Run 100 cases per test (default)
derandomize = true           # Deterministic test order
deadline = 5000              # Fail if single example takes >5s
```

### 7.2 Reproducing failures

**Rule:** Hypothesis failures are deterministic. Use `@example()` to encode failing cases.

**Pattern:**
```python
from hypothesis import given, example
from hypothesis.strategies import text

@given(text())
@example("edge'case")  # Hypothesis found this failing case
def test_sql_escaping(user_input):
    """User input must be escaped."""
    query = build_query(user_input)
    assert "'" not in query or "\\'" in query
```

**Rationale:** Failing cases become regression tests.

### 7.3 Stateful testing (advanced)

**Rule:** For stateful systems, use `RuleBasedStateMachine` sparingly.

**When to use:**
- Testing stateful APIs (e.g., database transactions)
- Testing state machines
- Testing caches with eviction

**Example:**
```python
from hypothesis.stateful import RuleBasedStateMachine, rule

class CacheStateMachine(RuleBasedStateMachine):
    def __init__(self):
        super().__init__()
        self.cache = Cache()
        self.model = {}

    @rule(key=text(), value=integers())
    def set(self, key, value):
        self.cache.set(key, value)
        self.model[key] = value

    @rule(key=text())
    def get(self, key):
        cached = self.cache.get(key)
        expected = self.model.get(key)
        assert cached == expected
```

**Warning:** Stateful tests are complex. Only use when simpler property tests are insufficient.

---

## 8. Common Pitfalls

### 8.1 Over-constraining strategies

**Problem:** Spending more time on strategy than on the actual test.

**Anti-pattern:**
```python
# 20 lines of strategy constraints
valid_usernames = text(...).filter(...).map(...).filter(...)

@given(valid_usernames)
def test_username_validation(username):
    assert validate_username(username)
```

**Fix:** Use example-based tests with 5 realistic cases instead.

### 8.2 Testing non-deterministic code

**Problem:** Hypothesis finds flaky failures that aren't real bugs.

**Anti-pattern:**
```python
@given(integers())
def test_random_generator(seed):
    result = generate_random_id()  # Uses time.time() internally
    # Fails randomly because function is non-deterministic
```

**Fix:** Make code deterministic (seed randomness, freeze time) or use example tests.

### 8.3 Ignoring shrunk examples

**Problem:** Hypothesis gives you minimal failing examples. If you ignore them, you miss the bug.

**Pattern:**
```
Falsifying example: test_parse(input='\\')
```

This tells you: backslashes break your parser. Fix it!

**Anti-pattern:**
```python
@given(text())
def test_parse(input):
    try:
        parse(input)
    except Exception:
        pass  # Swallowing the failure defeats the purpose
```

---

## 9. Default Decision

**If unsure: do NOT add Hypothesis.**

Add it only when:
1. A bug happened that example tests didn't catch, OR
2. The code is obviously combinatorial (parsers, validators, query builders), AND
3. All three conditions (pure, clear properties, actionable failures) are met

**Start with example-based tests. Graduate to property tests when needed.**

---

## 10. Examples

See [`examples/testing/hypothesis/`](../examples/testing/hypothesis/) for complete working examples:
- Query builder property tests
- Parser roundtrip tests
- Email normalization idempotence
- Validation property tests

---

## References

- [Core Testing Doctrine](./testing.md) — Universal testing principles
- [Hypothesis Documentation](https://hypothesis.readthedocs.io/)
- [Property-Based Testing Guide](https://hypothesis.works/articles/what-is-property-based-testing/)
- [Fast Property Testing Makes Easy](https://www.youtube.com/watch?v=mg5BeeYGjY0)

---

**Version History:**
- **1.0.0** (2025-12-14) — Initial release, extracted from ChatGPT patterns and The Standard
