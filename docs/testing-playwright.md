# Testing Doctrine: Playwright (Advanced E2E Patterns)
## Accessibility-First, Stable, Documentary E2E Tests

**Part of The Standard** | [Core Testing Doctrine](./testing.md) | **Version 1.0.0**
**Date:** 2025-12-14

---

## Prerequisites

**You MUST read [testing.md](./testing.md) first.**

This guide assumes you understand:
- Core E2E testing principles (testing.md section 1.4)
- Frontend testing requirements (testing.md section 9.2)
- Determinism and stability requirements

This guide provides **advanced patterns** for Playwright E2E testing beyond the basics.

**In case of conflict, core doctrine wins.**

---

## Prime Directive

**Accessibility selectors first. Test user behavior, not implementation.**

If you can't select an element by its accessible role or label, fix the component before writing the test.

---

## 1. Selector Strategy (The Hierarchy)

### 1.1 Selector precedence

**Rule:** You MUST use selectors in this order (most preferred → least preferred):

1. **Role + accessible name** (best)
   ```typescript
   page.getByRole('button', { name: 'Submit' })
   page.getByRole('textbox', { name: 'Email address' })
   ```

2. **Label text** (good for forms)
   ```typescript
   page.getByLabel('Username')
   page.getByLabel('Accept terms and conditions')
   ```

3. **Placeholder** (acceptable for inputs)
   ```typescript
   page.getByPlaceholder('Search...')
   ```

4. **Test ID** (last resort, but acceptable)
   ```typescript
   page.getByTestId('user-profile-card')
   ```

5. **CSS selectors** (avoid unless necessary)
   ```typescript
   page.locator('.user-card:nth-child(2)')  // Brittle!
   ```

**Rationale:**
- Roles + names test accessibility AND behavior
- If an element lacks accessible roles/labels, the component needs fixing
- Tests become documentation of how users interact with the app

### 1.2 When to use test IDs

**Rule:** Use `data-testid` ONLY when:
- The element has no semantic role (e.g., decorative containers)
- Multiple identical elements need differentiation
- The element is dynamically generated

**Pattern:**
```vue
<!-- Good: semantic button needs no test ID -->
<button>Submit</button>

<!-- Acceptable: dynamic list items need IDs -->
<li v-for="item in items" :data-testid="`item-${item.id}`">
  {{ item.name }}
</li>

<!-- Bad: adding test ID to avoid fixing accessibility -->
<div data-testid="submit-button" @click="submit">Submit</div>
<!-- Fix: Use <button> instead -->
```

### 1.3 Forbidden selectors

You MUST NOT use:
- XPath (fragile, hard to read)
- Deep CSS selectors (`div > div > span:nth-child(3)`)
- Class names that are styling-dependent (`.btn-primary`)
- IDs unless they're semantic (`#main-navigation` is OK)

---

## 2. Waiting Strategies

### 2.1 Auto-wait (default, preferred)

**Rule:** Rely on Playwright's auto-wait. Do NOT add manual waits unless necessary.

**Good (auto-wait):**
```typescript
test('user can submit form', async ({ page }) => {
  await page.goto('/form');
  await page.getByLabel('Email').fill('alice@example.com');
  await page.getByRole('button', { name: 'Submit' }).click();

  // Auto-waits for element to appear
  await expect(page.getByText('Form submitted successfully')).toBeVisible();
});
```

**Bad (manual wait):**
```typescript
test('user can submit form', async ({ page }) => {
  await page.goto('/form');
  await page.waitForTimeout(1000);  // ❌ Arbitrary wait
  await page.getByLabel('Email').fill('alice@example.com');
  await page.waitForTimeout(500);   // ❌ Flaky!
  // ...
});
```

### 2.2 Explicit waits (when needed)

**Rule:** Use explicit waits only for:
- Network requests (wait for specific API calls)
- Complex animations (wait for animation to complete)
- Specific state changes

**Pattern (wait for API):**
```typescript
test('data loads after navigation', async ({ page }) => {
  await page.goto('/dashboard');

  // Wait for specific API call
  await page.waitForResponse(
    response => response.url().includes('/api/users') && response.status() === 200
  );

  // Now assert on loaded data
  await expect(page.getByRole('list')).toContainText('Alice');
});
```

**Pattern (wait for state):**
```typescript
test('loading spinner disappears', async ({ page }) => {
  await page.goto('/slow-page');

  // Wait for loading state to finish
  await page.waitForSelector('[data-testid="loading-spinner"]', { state: 'hidden' });

  // Now interact with loaded content
  await expect(page.getByRole('heading')).toBeVisible();
});
```

### 2.3 Never use timeouts for synchronization

**Forbidden:**
```typescript
await page.waitForTimeout(3000);  // ❌ Flaky, arbitrary
await page.waitForTimeout(500);   // ❌ Race condition
```

**Alternative:** Wait for specific conditions, not time.

---

## 3. State Management Testing

### 3.1 Testing Pinia stores through UI

**Rule:** E2E tests MUST interact through the UI, not by directly manipulating stores.

**Good (through UI):**
```typescript
test('adding item updates cart count', async ({ page }) => {
  await page.goto('/products');

  // Interact through UI
  await page.getByRole('button', { name: 'Add to cart' }).first().click();

  // Verify UI reflects store state
  await expect(page.getByTestId('cart-count')).toHaveText('1');
});
```

**Bad (direct store manipulation):**
```typescript
test('cart displays items', async ({ page }) => {
  // ❌ Don't do this in E2E tests
  await page.evaluate(() => {
    window.$pinia.state.cart.items = [...];
  });

  await page.goto('/cart');
  // ...
});
```

**Exception:** Setup for complex test scenarios (see section 3.2).

### 3.2 Pre-populating state for complex scenarios

**Rule:** When testing requires complex state setup, you MAY use helper functions to pre-populate data, but you SHOULD prefer API calls over direct store manipulation.

**Good (via API):**
```typescript
test('user can edit existing article', async ({ page, request }) => {
  // Setup: Create article via API
  const article = await request.post('/api/articles', {
    data: { title: 'Test Article', content: 'Content' }
  });

  // Test: Edit through UI
  await page.goto(`/articles/${article.id}/edit`);
  await page.getByLabel('Title').fill('Updated Title');
  await page.getByRole('button', { name: 'Save' }).click();

  await expect(page.getByRole('heading')).toHaveText('Updated Title');
});
```

**Acceptable (for isolated frontend tests):**
```typescript
test('cart handles large number of items', async ({ page }) => {
  // Pre-populate for performance
  await page.addInitScript(() => {
    localStorage.setItem('cart', JSON.stringify({
      items: Array(50).fill({ id: 'item', name: 'Test', price: 10 })
    }));
  });

  await page.goto('/cart');
  await expect(page.getByTestId('cart-count')).toHaveText('50');
});
```

---

## 4. Authentication Testing

### 4.1 Authentication state reuse

**Rule:** Authenticate ONCE, reuse state across tests for performance.

**Pattern (global auth setup):**
```typescript
// tests/setup/auth.setup.ts
import { test as setup } from '@playwright/test';

setup('authenticate', async ({ page }) => {
  await page.goto('/login');
  await page.getByLabel('Username').fill('test-user');
  await page.getByLabel('Password').fill('secure-password');
  await page.getByRole('button', { name: 'Login' }).click();

  await expect(page).toHaveURL('/dashboard');

  // Save authentication state
  await page.context().storageState({ path: 'tests/.auth/user.json' });
});
```

**Configuration:**
```typescript
// playwright.config.ts
export default defineConfig({
  projects: [
    { name: 'setup', testMatch: /.*\.setup\.ts/ },
    {
      name: 'chromium',
      use: {
        ...devices['Desktop Chrome'],
        storageState: 'tests/.auth/user.json'
      },
      dependencies: ['setup'],
    },
  ],
});
```

**Usage:**
```typescript
test('authenticated user can access dashboard', async ({ page }) => {
  // Already authenticated via setup!
  await page.goto('/dashboard');
  await expect(page.getByRole('heading')).toHaveText('Dashboard');
});
```

**Rationale:** Logging in for every test is slow. Reuse auth state.

### 4.2 Testing unauthenticated flows

**Rule:** For testing login/logout, use a separate project without pre-authentication.

**Configuration:**
```typescript
// playwright.config.ts
export default defineConfig({
  projects: [
    {
      name: 'authenticated',
      use: { storageState: 'tests/.auth/user.json' },
      testMatch: /authenticated\/.*/,
    },
    {
      name: 'unauthenticated',
      use: { storageState: { cookies: [], origins: [] } },
      testMatch: /unauthenticated\/.*/,
    },
  ],
});
```

---

## 5. Network Mocking

### 5.1 When to mock network calls

**Rule:** Mock network calls in E2E tests ONLY when:
- Testing error states (500, 404, timeout)
- Testing specific edge cases (large payloads, specific data shapes)
- External dependencies are unavailable in CI

**You SHOULD NOT mock for happy-path tests if you have a real backend.**

**Rationale:** E2E tests are most valuable when testing the full stack. Mocking reduces confidence.

### 5.2 Network mocking pattern

**Pattern (mock specific endpoints):**
```typescript
test('handles API error gracefully', async ({ page }) => {
  // Mock API to return error
  await page.route('**/api/users', route => {
    route.fulfill({
      status: 500,
      body: JSON.stringify({ error: 'Internal server error' })
    });
  });

  await page.goto('/users');

  // Verify error state
  await expect(page.getByText('Failed to load users')).toBeVisible();
  await expect(page.getByRole('button', { name: 'Retry' })).toBeVisible();
});
```

**Pattern (mock for offline testing):**
```typescript
test('shows offline message when disconnected', async ({ page, context }) => {
  await page.goto('/dashboard');

  // Simulate offline
  await context.setOffline(true);
  await page.reload();

  await expect(page.getByText('You are offline')).toBeVisible();
});
```

---

## 6. Multi-Step User Flows

### 6.1 Feature tests as documentation

**Rule:** Feature tests MUST read like user stories.

**Good (narrative style):**
```typescript
test('user can complete checkout flow', async ({ page }) => {
  // Given: User is on the product page
  await page.goto('/products');

  // When: User adds item to cart
  await page.getByRole('button', { name: 'Add to cart' }).first().click();
  await expect(page.getByTestId('cart-count')).toHaveText('1');

  // And: User proceeds to checkout
  await page.getByRole('link', { name: 'Cart' }).click();
  await page.getByRole('button', { name: 'Checkout' }).click();

  // And: User fills shipping information
  await page.getByLabel('Full name').fill('Alice Smith');
  await page.getByLabel('Address').fill('123 Main St');
  await page.getByLabel('City').fill('Portland');
  await page.getByRole('button', { name: 'Continue to payment' }).click();

  // And: User enters payment details
  await page.getByLabel('Card number').fill('4242424242424242');
  await page.getByLabel('Expiry').fill('12/25');
  await page.getByLabel('CVC').fill('123');

  // When: User completes purchase
  await page.getByRole('button', { name: 'Complete purchase' }).click();

  // Then: User sees confirmation
  await expect(page.getByRole('heading')).toHaveText('Order confirmed');
  await expect(page.getByText(/Order #\d+/)).toBeVisible();
});
```

**Rationale:** Tests serve as living documentation of user workflows.

### 6.2 Breaking long flows into steps

**Rule:** For very long flows, use helper functions with descriptive names.

**Pattern:**
```typescript
async function addProductToCart(page, productName: string) {
  await page.getByRole('link', { name: 'Products' }).click();
  await page.getByRole('button', { name: `Add ${productName}` }).click();
  await expect(page.getByText('Added to cart')).toBeVisible();
}

async function completeCheckout(page, details: CheckoutDetails) {
  await page.getByRole('link', { name: 'Cart' }).click();
  await page.getByRole('button', { name: 'Checkout' }).click();
  await page.getByLabel('Full name').fill(details.name);
  // ...
  await page.getByRole('button', { name: 'Complete purchase' }).click();
}

test('user can buy multiple products', async ({ page }) => {
  await addProductToCart(page, 'Blue T-Shirt');
  await addProductToCart(page, 'Black Jeans');

  await completeCheckout(page, {
    name: 'Alice Smith',
    address: '123 Main St',
    // ...
  });

  await expect(page.getByRole('heading')).toHaveText('Order confirmed');
});
```

---

## 7. Accessibility Testing Integration

### 7.1 Automated accessibility checks

**Rule:** E2E tests SHOULD include basic accessibility validation.

**Pattern (using axe-core):**
```typescript
import { test, expect } from '@playwright/test';
import AxeBuilder from '@axe-core/playwright';

test('dashboard has no accessibility violations', async ({ page }) => {
  await page.goto('/dashboard');

  const accessibilityScanResults = await new AxeBuilder({ page }).analyze();

  expect(accessibilityScanResults.violations).toEqual([]);
});
```

**Pattern (checking specific violations):**
```typescript
test('form has proper labels', async ({ page }) => {
  await page.goto('/contact');

  const results = await new AxeBuilder({ page })
    .include('.contact-form')
    .analyze();

  const labelViolations = results.violations.filter(v =>
    v.id === 'label' || v.id === 'label-title-only'
  );

  expect(labelViolations).toEqual([]);
});
```

### 7.2 Keyboard navigation testing

**Rule:** Interactive features MUST have keyboard navigation tests.

**Pattern:**
```typescript
test('user can navigate form with keyboard', async ({ page }) => {
  await page.goto('/form');

  // Tab to first field
  await page.keyboard.press('Tab');
  await expect(page.getByLabel('Email')).toBeFocused();

  // Fill and tab to next field
  await page.keyboard.type('alice@example.com');
  await page.keyboard.press('Tab');
  await expect(page.getByLabel('Password')).toBeFocused();

  // Submit with Enter
  await page.keyboard.type('password123');
  await page.keyboard.press('Enter');

  await expect(page).toHaveURL('/dashboard');
});
```

---

## 8. Visual Regression Testing

### 8.1 When to use screenshots

**Rule:** Use screenshots for visual regression ONLY when:
- Testing pixel-perfect layouts (design system components)
- Verifying charts/graphs/visualizations
- Testing responsive breakpoints

**You SHOULD NOT screenshot entire pages as primary assertions.**

**Pattern (component screenshot):**
```typescript
test('button styles match design system', async ({ page }) => {
  await page.goto('/storybook/button');

  const button = page.getByRole('button', { name: 'Primary' });
  await expect(button).toHaveScreenshot('primary-button.png');
});
```

**Pattern (responsive testing):**
```typescript
test('navigation is responsive', async ({ page }) => {
  await page.goto('/');

  // Desktop
  await page.setViewportSize({ width: 1920, height: 1080 });
  await expect(page.getByRole('navigation')).toHaveScreenshot('nav-desktop.png');

  // Mobile
  await page.setViewportSize({ width: 375, height: 667 });
  await expect(page.getByRole('navigation')).toHaveScreenshot('nav-mobile.png');
});
```

---

## 9. Failure Debugging

### 9.1 Artifacts on failure

**Rule:** Configure Playwright to capture artifacts on failure.

**Configuration:**
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

### 9.2 Using traces for debugging

**Rule:** When tests fail in CI, download trace and analyze locally.

**Pattern:**
```bash
# Download trace from CI artifacts
npx playwright show-trace trace.zip
```

**Traces include:**
- Screenshots at each step
- Network requests
- Console logs
- DOM snapshots

---

## 10. Performance Considerations

### 10.1 Parallel execution

**Rule:** E2E tests SHOULD run in parallel for speed.

**Configuration:**
```typescript
// playwright.config.ts
export default defineConfig({
  workers: process.env.CI ? 4 : 2,  // Parallel workers
  fullyParallel: true,
});
```

**Ensure tests are isolated (no shared state between tests).**

### 10.2 Test sharding (for large suites)

**Rule:** For suites >100 tests, use sharding in CI.

**Pattern:**
```bash
# Split tests across 4 machines
npx playwright test --shard=1/4
npx playwright test --shard=2/4
npx playwright test --shard=3/4
npx playwright test --shard=4/4
```

---

## 11. Directory Layout

**Recommended structure:**
```
tests/e2e/
├── setup/
│   └── auth.setup.ts           # Global authentication
├── authenticated/
│   ├── dashboard.spec.ts       # Tests requiring auth
│   ├── profile.spec.ts
│   └── checkout.spec.ts
├── unauthenticated/
│   ├── login.spec.ts           # Tests without auth
│   └── signup.spec.ts
├── fixtures/
│   └── test-data.ts            # Shared test data
└── helpers/
    └── checkout-flow.ts        # Reusable flow helpers
```

---

## 12. Integration with Core Doctrine

### 12.1 Determinism

Per [core doctrine section 3.1](./testing.md#31-determinism-flakiness-is-a-bug):

Playwright tests MUST:
- Use auto-wait, not arbitrary timeouts
- Mock time-dependent logic (use Playwright's `clock.install()`)
- Avoid dependence on external services

### 12.2 Meaningful assertions

Per [core doctrine section 3.2](./testing.md#32-assertions-must-be-meaningful):

Playwright tests MUST assert:
- Visible user-facing behavior (text, buttons, navigation)
- Side effects (URL changes, storage, network requests)
- Accessibility (roles, labels, keyboard navigation)

**Forbidden:**
```typescript
await expect(page.locator('div')).toBeVisible();  // Too vague
```

**Required:**
```typescript
await expect(page.getByRole('alert')).toHaveText('Login successful');
```

---

## 13. Examples

See [`examples/testing/playwright/`](../examples/testing/playwright/) for complete working examples:
- Accessibility-first selectors
- Authentication state reuse
- Multi-step checkout flow
- Network mocking patterns
- Keyboard navigation testing

---

## References

- [Core Testing Doctrine](./testing.md) — Universal testing principles
- [Playwright Documentation](https://playwright.dev/)
- [Playwright Best Practices](https://playwright.dev/docs/best-practices)
- [axe-core Playwright Integration](https://github.com/dequelabs/axe-core-npm/tree/develop/packages/playwright)

---

**Version History:**
- **1.0.0** (2025-12-14) — Initial release, advanced patterns for The Standard
