# Quick Reference: Playwright (Advanced E2E)

**One-page summary of [`testing-playwright.md`](../testing-playwright.md) — Print this for quick reference**

---

## Prime Directive

**Accessibility selectors first. Test user behavior, not implementation.**

---

## Selector Hierarchy (Use in Order)

| Priority | Selector | Example | When |
|----------|----------|---------|------|
| **1. Best** | Role + name | `getByRole('button', { name: 'Submit' })` | Always try first |
| **2. Good** | Label | `getByLabel('Email address')` | Forms |
| **3. OK** | Placeholder | `getByPlaceholder('Search...')` | Inputs |
| **4. Last resort** | Test ID | `getByTestId('user-card')` | Dynamic content |
| **5. Avoid** | CSS | `.btn-primary` | Never (brittle) |

---

## Good Selectors

```typescript
// ✅ GOOD: Role + accessible name
page.getByRole('button', { name: 'Login' })
page.getByRole('textbox', { name: 'Email' })
page.getByRole('link', { name: 'Products' })

// ✅ GOOD: Label (forms)
page.getByLabel('Username')
page.getByLabel('Accept terms')

// ⚠️ ACCEPTABLE: Test ID (when needed)
page.getByTestId('product-123')
```

---

## Bad Selectors (Avoid)

```typescript
// ❌ BAD: CSS classes
page.locator('.btn-primary')

// ❌ BAD: Deep CSS
page.locator('div > div > span:nth-child(3)')

// ❌ BAD: XPath
page.locator('xpath=//div[@class="container"]//button')
```

---

## Waiting Strategies

### Auto-Wait (Default, Preferred)
```typescript
// ✅ Playwright auto-waits
await page.getByRole('button', { name: 'Submit' }).click();
await expect(page.getByText('Success')).toBeVisible();
```

### Explicit Waits (When Needed)
```typescript
// Wait for API response
await page.waitForResponse(
  res => res.url().includes('/api/users') && res.status() === 200
);

// Wait for state change
await page.waitForSelector('[data-testid="loading"]', { state: 'hidden' });
```

### Never Use
```typescript
// ❌ NEVER: Arbitrary timeouts
await page.waitForTimeout(3000);  // Flaky!
```

---

## Authentication State Reuse

**Setup once, reuse everywhere:**

```typescript
// tests/setup/auth.setup.ts
setup('authenticate', async ({ page }) => {
  await page.goto('/login');
  await page.getByLabel('Username').fill('test-user');
  await page.getByLabel('Password').fill('password');
  await page.getByRole('button', { name: 'Login' }).click();

  await page.context().storageState({ path: 'tests/.auth/user.json' });
});

// playwright.config.ts
export default defineConfig({
  use: { storageState: 'tests/.auth/user.json' }
});
```

---

## Multi-Step Flows (Documentary Style)

```typescript
test('user can complete checkout', async ({ page }) => {
  // Given: User on products page
  await page.goto('/products');

  // When: User adds item to cart
  await page.getByRole('button', { name: 'Add to cart' }).first().click();

  // And: User proceeds to checkout
  await page.getByRole('link', { name: 'Cart' }).click();
  await page.getByRole('button', { name: 'Checkout' }).click();

  // And: User fills shipping info
  await page.getByLabel('Full name').fill('Alice Smith');
  await page.getByLabel('Address').fill('123 Main St');

  // Then: Purchase completes
  await page.getByRole('button', { name: 'Complete' }).click();
  await expect(page.getByRole('heading')).toHaveText('Order confirmed');
});
```

---

## Keyboard Navigation

```typescript
test('form is keyboard accessible', async ({ page }) => {
  await page.goto('/form');

  // Tab navigation
  await page.keyboard.press('Tab');
  await expect(page.getByLabel('Email')).toBeFocused();

  await page.keyboard.type('alice@example.com');
  await page.keyboard.press('Tab');

  // Submit with Enter
  await page.keyboard.press('Enter');
});
```

---

## Network Mocking

**Only for error states:**
```typescript
test('handles API error', async ({ page }) => {
  await page.route('**/api/users', route => {
    route.fulfill({ status: 500, body: '...' });
  });

  await page.goto('/users');
  await expect(page.getByText('Failed to load')).toBeVisible();
});
```

---

## Accessibility Testing

```typescript
import AxeBuilder from '@axe-core/playwright';

test('page has no a11y violations', async ({ page }) => {
  await page.goto('/dashboard');

  const results = await new AxeBuilder({ page }).analyze();
  expect(results.violations).toEqual([]);
});
```

---

## Visual Regression (Selective)

```typescript
// Only for pixel-perfect components
test('button matches design', async ({ page }) => {
  await page.goto('/storybook/button');

  const button = page.getByRole('button', { name: 'Primary' });
  await expect(button).toHaveScreenshot('primary-button.png');
});
```

---

## Artifacts on Failure

```typescript
// playwright.config.ts
export default defineConfig({
  use: {
    screenshot: 'only-on-failure',
    video: 'retain-on-failure',
    trace: 'retain-on-failure',
  },
});
```

---

## Directory Layout

```
tests/e2e/
├── setup/
│   └── auth.setup.ts           # Global auth
├── authenticated/
│   ├── dashboard.spec.ts
│   └── checkout.spec.ts
├── unauthenticated/
│   └── login.spec.ts
└── helpers/
    └── flows.ts                # Reusable helpers
```

---

## Common Pitfalls

❌ **Don't** use CSS selectors (brittle)
✅ **Do** use role + accessible name

❌ **Don't** use `waitForTimeout()`
✅ **Do** rely on auto-wait

❌ **Don't** test implementation details
✅ **Do** test user behavior

❌ **Don't** login for every test
✅ **Do** reuse auth state

---

## Quick Checklist

- [ ] Read [testing.md](../testing.md) first (core principles)
- [ ] Use accessibility selectors (role, label)
- [ ] Rely on auto-wait (no arbitrary timeouts)
- [ ] Test user behavior, not DOM internals
- [ ] Reuse auth state for performance
- [ ] Test keyboard navigation
- [ ] Write tests as user stories
- [ ] Capture artifacts on failure

---

**Full Docs:** [testing-playwright.md](../testing-playwright.md) | **Examples:** [examples/testing/playwright/](../../examples/testing/playwright/)
