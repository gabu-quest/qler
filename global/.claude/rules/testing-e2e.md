---
description: "E2E and Playwright testing — selector hierarchy, stability, test isolation"
paths:
  - "**/*.spec.ts"
  - "**/*.spec.js"
  - "**/e2e/**"
  - "**/playwright*"
  - "**/cypress/**"
---

# E2E Testing

E2E tests prove that critical user flows work in a real browser or against a real server. They are the most expensive tests to write and maintain — use them wisely.

## Testing Pyramid

| Layer | What to Test | Volume |
|-------|-------------|--------|
| **Unit** | Pure logic, transformations, calculations | Many |
| **Integration** | Boundaries: DB, APIs, filesystem | Moderate |
| **E2E** | Critical user flows end-to-end | Few |

E2E tests sit at the top. They MUST cover the flows that, if broken, would lose users or revenue. They MUST NOT test what unit/integration tests already cover.

## What Deserves E2E

| YES — E2E required | NO — Use lower-level tests |
|---------------------|---------------------------|
| Login / signup / logout | Button styling, hover states |
| Checkout / payment flow | Form field validation (unit test) |
| CRUD lifecycle (create → read → update → delete) | Tooltip positioning |
| Multi-step wizards | API response parsing (integration test) |
| Permission-gated pages | Component rendering in isolation |

## Selector Hierarchy

Prefer selectors that match how users interact with the page:

1. **Role + name** — `getByRole('button', { name: 'Submit' })` (best)
2. **Label** — `getByLabel('Email address')`
3. **Placeholder** — `getByPlaceholder('Search...')`
4. **Test ID** — `getByTestId('checkout-form')` (when semantics fail)
5. **CSS selector** — `.btn-primary` (last resort, fragile)

NEVER use implementation details as selectors (component names, internal IDs, DOM structure).

## Stability Rules

- **Auto-wait always** — Playwright auto-waits for elements. Use it. Never fight it.
- **Never `waitForTimeout`** — Wait for conditions, not time. Use `waitForSelector`, `waitForResponse`, `expect(...).toBeVisible()`.
- **Assert state, not timing** — If a button should be disabled after click, assert `toBeDisabled()`, don't sleep and check.
- **Retry assertions** — Use Playwright's built-in assertion retrying (`expect` with auto-retry).

## Test Isolation

Each test MUST:
- Start from a **known, clean state** (seeded DB, fresh session)
- **Not depend on other tests** running first or in order
- **Clean up after itself** if it creates persistent data
- Use **separate user accounts** per test when testing auth flows

## API E2E Testing

For backend-only E2E tests (no browser):
- **Real server, real database** — Seeded with known fixtures
- **Happy paths with real data** — Test the full request→response cycle
- **Mock only for error states** — Network failures, third-party outages
- **Assert response bodies exactly** — Not just status codes

## Anti-Patterns

| Anti-Pattern | Why It's Broken | Correct Approach |
|--------------|-----------------|------------------|
| `await page.waitForTimeout(3000)` | Slow + flaky | `await expect(el).toBeVisible()` |
| `page.locator('.sc-abc123')` | Breaks on rebuild | `getByRole('button', { name: '...' })` |
| Test B depends on Test A's data | Flaky, order-dependent | Each test seeds its own state |
| Manipulating stores/state directly | Bypasses the UI layer you're testing | Interact through the UI |
| Screenshot comparison as sole assertion | Brittle across environments | Assert DOM state + visual as supplement |
| Testing every permutation E2E | Expensive, slow | E2E for critical paths; unit for permutations |

## Reference

For the complete Playwright guide (700+ lines) including page objects, fixtures, CI configuration, and advanced patterns, see `docs/testing-playwright.md`.
