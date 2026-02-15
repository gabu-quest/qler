---
description: "Testing doctrine index — points to core, language-specific, and e2e rules"
---

# Testing

> "A failing test is a gift."

This rule has been split into focused files:

- **testing-core.md** - Universal philosophy and anti-patterns (loads for test files)
- **testing-python.md** - Python/pytest patterns (loads for `**/*.py`)
- **testing-typescript.md** - TypeScript/Vitest/Vue patterns (loads for `**/*.ts`, `**/*.vue`)
- **testing-go.md** - Go table-driven test patterns (loads for `**/*.go`)
- **testing-rust.md** - Rust assert_eq!/proptest patterns (loads for `**/*.rs`)
- **testing-e2e.md** - E2E/Playwright patterns (loads for `**/*.spec.ts`, `**/e2e/**`, `**/playwright*`)

See those files for complete guidance.

## Quick Reference

Tests are proof, not ceremony. When a test fails:

1. Fix the bug (preferred)
2. Fix the wrong expectation (with explanation)
3. Update test AND docs (if requirements changed)

NEVER weaken assertions, skip tests, or blame "flakiness" without proof.

Every test MUST fail when the output is wrong.
