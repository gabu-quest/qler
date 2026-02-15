/**
 * Example: Accessibility-first selectors in Playwright.
 *
 * Demonstrates the selector hierarchy:
 * 1. Role + accessible name (best)
 * 2. Label text (good for forms)
 * 3. Placeholder (acceptable)
 * 4. Test ID (last resort)
 * 5. CSS selectors (avoid)
 */

import { test, expect } from '@playwright/test';

// ============================================================================
// Good Selectors: Role + Accessible Name
// ============================================================================

test('login with role-based selectors', async ({ page }) => {
  await page.goto('http://localhost:3000/login');

  // ✅ GOOD: Select by role and accessible name
  await page.getByRole('textbox', { name: 'Email' }).fill('alice@example.com');
  await page.getByRole('textbox', { name: 'Password' }).fill('secure123');
  await page.getByRole('button', { name: 'Login' }).click();

  // ✅ GOOD: Assert on visible heading
  await expect(page.getByRole('heading', { name: 'Dashboard' })).toBeVisible();
});

test('navigation with role-based selectors', async ({ page }) => {
  await page.goto('http://localhost:3000');

  // ✅ GOOD: Navigate using accessible names
  await page.getByRole('link', { name: 'Products' }).click();
  await expect(page).toHaveURL(/\/products$/);

  await page.getByRole('link', { name: 'About' }).click();
  await expect(page).toHaveURL(/\/about$/);
});

// ============================================================================
// Good Selectors: Label Text
// ============================================================================

test('form submission with label selectors', async ({ page }) => {
  await page.goto('http://localhost:3000/contact');

  // ✅ GOOD: Select by associated label
  await page.getByLabel('Full name').fill('Alice Smith');
  await page.getByLabel('Email address').fill('alice@example.com');
  await page.getByLabel('Message').fill('Hello, this is a test message.');

  // ✅ GOOD: Checkbox by label
  await page.getByLabel('I agree to the terms').check();

  await page.getByRole('button', { name: 'Submit' }).click();

  await expect(page.getByText('Thank you for your message')).toBeVisible();
});

// ============================================================================
// Acceptable Selectors: Placeholder
// ============================================================================

test('search with placeholder selector', async ({ page }) => {
  await page.goto('http://localhost:3000');

  // ⚠️ ACCEPTABLE: Placeholder when no label exists
  await page.getByPlaceholder('Search products...').fill('laptop');
  await page.keyboard.press('Enter');

  await expect(page.getByRole('heading', { name: 'Search Results' })).toBeVisible();
});

// ============================================================================
// Last Resort: Test ID
// ============================================================================

test('dynamic list items with test IDs', async ({ page }) => {
  await page.goto('http://localhost:3000/products');

  // ⚠️ LAST RESORT: Test ID when semantic selection isn't possible
  // Use case: Dynamically generated list items
  await page.getByTestId('product-123').click();

  await expect(page.getByRole('heading')).toHaveText('Product Details');
});

// ============================================================================
// Anti-Patterns: CSS Selectors (AVOID)
// ============================================================================

test.skip('BAD: brittle CSS selectors', async ({ page }) => {
  await page.goto('http://localhost:3000/login');

  // ❌ BAD: CSS class-based selectors
  // Breaks when styling changes
  await page.locator('.input-field-email').fill('alice@example.com');
  await page.locator('.btn-primary').click();

  // ❌ BAD: Deep CSS selectors
  // Breaks when DOM structure changes
  await page.locator('div.form > div:nth-child(2) > input').fill('text');

  // ❌ BAD: XPath
  // Hard to read, brittle
  await page.locator('xpath=//div[@class="container"]//button').click();
});

// ============================================================================
// Real-World Example: E-Commerce Checkout
// ============================================================================

test('checkout flow with accessibility selectors', async ({ page }) => {
  await page.goto('http://localhost:3000/products');

  // Step 1: Add product to cart
  await page.getByRole('button', { name: 'Add to cart' }).first().click();
  await expect(page.getByTestId('cart-count')).toHaveText('1');

  // Step 2: Navigate to cart
  await page.getByRole('link', { name: 'Cart' }).click();
  await expect(page.getByRole('heading', { name: 'Shopping Cart' })).toBeVisible();

  // Step 3: Proceed to checkout
  await page.getByRole('button', { name: 'Checkout' }).click();

  // Step 4: Fill shipping information (label-based)
  await page.getByLabel('Full name').fill('Alice Smith');
  await page.getByLabel('Street address').fill('123 Main St');
  await page.getByLabel('City').fill('Portland');
  await page.getByLabel('Postal code').fill('97201');

  // Step 5: Continue to payment
  await page.getByRole('button', { name: 'Continue to payment' }).click();

  // Step 6: Fill payment details (label-based)
  await page.getByLabel('Card number').fill('4242424242424242');
  await page.getByLabel('Expiration date').fill('12/25');
  await page.getByLabel('CVC').fill('123');

  // Step 7: Complete purchase
  await page.getByRole('button', { name: 'Complete purchase' }).click();

  // Step 8: Verify confirmation
  await expect(page.getByRole('heading', { name: 'Order confirmed' })).toBeVisible();
  await expect(page.getByText(/Order number:/)).toBeVisible();
});

// ============================================================================
// Selector Debugging Tips
// ============================================================================

test('debugging selectors with Playwright Inspector', async ({ page }) => {
  await page.goto('http://localhost:3000');

  // TIP: Use page.pause() to open Playwright Inspector
  // This helps you test selectors interactively
  // await page.pause();

  // TIP: Use locator.highlight() to visually verify selection
  const button = page.getByRole('button', { name: 'Login' });
  // await button.highlight(); // Uncomment to see highlighting

  // TIP: Use locator.count() to verify unique selection
  const loginButtons = page.getByRole('button', { name: 'Login' });
  const count = await loginButtons.count();
  expect(count).toBe(1); // Should select exactly one element
});
