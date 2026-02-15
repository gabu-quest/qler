# Testing

> "A failing test is a gift."

This rule has been split into focused files:

- **testing-core.md** - Universal philosophy and anti-patterns (always loads)
- **testing-python.md** - Python/pytest patterns (loads for `**/*.py`)
- **testing-typescript.md** - TypeScript/Vitest/Vue patterns (loads for `**/*.ts`, `**/*.vue`)

See those files for complete guidance.

## Quick Reference

Tests are proof, not ceremony. When a test fails:

1. Fix the bug (preferred)
2. Fix the wrong expectation (with explanation)
3. Update test AND docs (if requirements changed)

NEVER weaken assertions, skip tests, or blame "flakiness" without proof.

Every test MUST fail when the output is wrong.
