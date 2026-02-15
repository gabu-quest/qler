# Playwright (E2E) Testing Examples

This directory contains complete, working examples of advanced Playwright E2E testing patterns from [`docs/testing-playwright.md`](../../../docs/testing-playwright.md).

## Examples Included

1. **`accessibility-selectors.spec.ts`** — Accessibility-first selector patterns
2. **`auth-state-reuse.spec.ts`** — Authentication state reuse for performance
3. **`keyboard-navigation.spec.ts`** — Keyboard accessibility testing
4. **`multi-step-flow.spec.ts`** — Documentary multi-step user flows

## Running These Examples

These are standalone examples for educational purposes. To run them in a real project:

1. Install dependencies:
   ```bash
   npm install -D @playwright/test
   npx playwright install
   ```

2. Run E2E tests:
   ```bash
   npx playwright test examples/testing/playwright/
   ```

## Key Principles

These examples demonstrate:
- ✅ **Accessibility-first selectors** (roles, labels > test IDs)
- ✅ **Auto-wait patterns** (no arbitrary timeouts)
- ✅ **User behavior testing** (test what users do, not implementation)
- ✅ **Documentary style** (tests read like user stories)
- ✅ **Meaningful assertions** (visible behavior, not DOM internals)

## Structure

Each example shows:
- Good selector patterns (role, label, placeholder)
- Bad patterns to avoid (CSS selectors, XPath)
- Proper waiting strategies
- Clear test narratives

## Reference

See [`docs/testing-playwright.md`](../../../docs/testing-playwright.md) for complete Playwright testing doctrine.
