---
description: "Universal testing philosophy — anti-patterns, meaningful assertions, the Inversion Test"
paths:
  - "**/*test*"
  - "**/*spec*"
  - "**/tests/**"
  - "**/test/**"
  - "**/__tests__/**"
---

# Testing Philosophy

> "A failing test is a gift."

Tests are proof, not ceremony. When a test fails:

1. Fix the bug (preferred)
2. Fix the wrong expectation (with explanation)
3. Update test AND docs (if requirements changed)

NEVER weaken assertions, skip tests, or blame "flakiness" without proof.

## Softball Anti-Patterns

These patterns prove nothing and MUST be avoided:

| Anti-Pattern | Why It's Broken |
|--------------|-----------------|
| Type-only checks (`instanceof`, `typeof`) | ANY object of that type passes |
| Existence checks (`!= null`, `in`) | Doesn't verify the VALUE is correct |
| Try/catch that accepts any outcome | Both success and failure pass |
| Empty loops over results | Never executes if results are empty! |
| `or` chains in assertions | Accepts contradictory behaviors |
| Tautologies | Literally cannot fail |

## Definition of a Meaningful Assertion

1. **Assert specific values, not just types**
   - BAD: Check that result is a dict/object
   - GOOD: Check that `result.status === "success"`

2. **Assert behavior, not existence**
   - BAD: Check that "results" key exists
   - GOOD: Check that `results.length === 5`

3. **Define ONE expected outcome per test**
   - BAD: Accept either true OR boolean
   - GOOD: Expect exactly `true`

## The Litmus Test

Before committing a test, ask:

- If the function returned an empty list, would this pass? → **Broken**
- If all values were 0 or null, would this pass? → **Broken**
- If the function returned completely wrong data, would this pass? → **Broken**

Every test MUST fail when the output is wrong.

## Output Quality: Test Correctness, Not Just Structure

> Tests that verify structure but not correctness are theater.

The most dangerous tests pass when the code returns garbage. This happens when you test "something was returned" instead of "the right thing was returned."

### The Pattern That Hides Bugs

| What You Tested | What You Should Have Tested | Bug Hidden |
|-----------------|----------------------------|------------|
| Key exists | Value equals expected | All values 0 or null |
| Length > 0 | Length equals expected_count | Wrong count, duplicates |
| Name key exists | Name equals expected name | Generic fallback names |
| Loop over results | Assert results.length first | Empty results |

### Parametrize with Exact Expected Values

Use parameterized tests to specify exact inputs and expected outputs. Don't just check types - verify exact values from deterministic fixtures.

### Create Deterministic Test Fixtures

Fixtures with **known, exact values** let you assert exact outputs. Document what the fixture contains so tests can assert specific counts and values.

---

## MANDATORY PRE-WRITE CHECKLIST

Before writing ANY assertion, mentally execute these three checks:

1. **The Inversion Test** — If I flip the function's return value to `None`/`[]`/`0`/`{}`, does this test fail? If no, the assertion is a softball.
2. **The Wrong Data Test** — If the function returns completely wrong but structurally valid data, does this test fail? If no, you're testing structure not correctness.
3. **The Empty Collection Trap** — Am I iterating over results without first asserting the collection is non-empty? If yes, the loop might never execute.

This is not optional. Every assertion must survive all three checks.

## BANNED ASSERTIONS (standalone)

These patterns MUST NEVER appear as the sole assertion for a value. They are only acceptable as a **precondition guard** immediately before a value assertion:

| Banned Standalone | Language |
|-------------------|----------|
| `assert x is not None` | Python |
| `assert isinstance(x, T)` | Python |
| `assert "key" in dict` | Python |
| `assert len(x) > 0` | Python |
| `expect(x).toBeDefined()` | TypeScript |
| `expect(x).toBeInstanceOf(T)` | TypeScript |
| `expect(x).toHaveProperty("key")` | TypeScript |
| `expect(x.length).toBeGreaterThan(0)` | TypeScript |

## GUARD-THEN-ASSERT Pattern

The correct way to use existence/type checks — as a guard before the real assertion:

```python
# Precondition (guard) + Assertion (proof)
assert result is not None          # guard — prevents confusing NoneType errors
assert result.status == "active"   # proof — THIS is the real test
```

```typescript
// Guard + Proof
expect(result).toBeDefined()           // guard
expect(result.status).toBe('active')   // proof — THIS is the real test
```

A guard without a following proof is a softball. Period.
