# Hypothesis (Property-Based Testing) Examples

This directory contains complete, working examples of property-based testing patterns from [`docs/testing-hypothesis.md`](../../../docs/testing-hypothesis.md).

## Examples Included

1. **`test_query_builder_properties.py`** — SQL query builder with injection prevention
2. **`test_email_normalization.py`** — Idempotence and validation properties
3. **`test_json_roundtrip.py`** — Serialization roundtrip properties
4. **`test_parser_properties.py`** — Parser acceptance/rejection properties

## Running These Examples

These are standalone examples for educational purposes. To run them in a real project:

1. Install dependencies:
   ```bash
   pip install hypothesis pytest
   ```

2. Run property tests:
   ```bash
   pytest examples/testing/hypothesis/
   ```

## When to Use These Patterns

Property-based tests are powerful but not always needed. Use them when:
1. ✅ The code is pure or nearly pure (deterministic)
2. ✅ You can state clear properties that ALWAYS hold
3. ✅ Failures are actionable (shrunk examples reveal bugs)

See [`docs/testing-hypothesis.md`](../../../docs/testing-hypothesis.md) for complete decision criteria.

## Structure

Each example demonstrates:
- ✅ Clear property statements
- ✅ Bounded strategies (max_size, min/max values)
- ✅ Realistic domains (not unbounded generation)
- ✅ Actionable failure messages

## Reference

See [`docs/testing-hypothesis.md`](../../../docs/testing-hypothesis.md) for complete Hypothesis testing doctrine.
