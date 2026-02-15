/**
 * Example: Keyboard navigation testing.
 *
 * Demonstrates:
 * - Tab navigation through forms
 * - Enter key submission
 * - Escape key for modals/dialogs
 * - Arrow key navigation in lists
 */

import { test, expect } from '@playwright/test';

// ============================================================================
// Form Navigation with Tab
// ============================================================================

test('user can navigate form with Tab key', async ({ page }) => {
  await page.goto('http://localhost:3000/contact');

  // Tab to first field
  await page.keyboard.press('Tab');
  await expect(page.getByLabel('Full name')).toBeFocused();

  // Type and tab to next field
  await page.keyboard.type('Alice Smith');
  await page.keyboard.press('Tab');
  await expect(page.getByLabel('Email')).toBeFocused();

  // Type and tab to message field
  await page.keyboard.type('alice@example.com');
  await page.keyboard.press('Tab');
  await expect(page.getByLabel('Message')).toBeFocused();

  // Type message
  await page.keyboard.type('This is a test message.');

  // Tab to checkbox
  await page.keyboard.press('Tab');
  await expect(page.getByLabel('I agree to terms')).toBeFocused();

  // Check with Space
  await page.keyboard.press('Space');
  await expect(page.getByLabel('I agree to terms')).toBeChecked();

  // Tab to submit button
  await page.keyboard.press('Tab');
  await expect(page.getByRole('button', { name: 'Submit' })).toBeFocused();

  // Submit with Enter
  await page.keyboard.press('Enter');

  await expect(page.getByText('Thank you for your message')).toBeVisible();
});

// ============================================================================
// Shift+Tab (Reverse Navigation)
// ============================================================================

test('user can navigate backwards with Shift+Tab', async ({ page }) => {
  await page.goto('http://localhost:3000/form');

  // Tab to second field
  await page.keyboard.press('Tab');
  await page.keyboard.press('Tab');
  await expect(page.getByLabel('Email')).toBeFocused();

  // Shift+Tab to go back to first field
  await page.keyboard.press('Shift+Tab');
  await expect(page.getByLabel('Name')).toBeFocused();
});

// ============================================================================
// Enter Key Submission
// ============================================================================

test('user can submit form with Enter key', async ({ page }) => {
  await page.goto('http://localhost:3000/login');

  await page.getByLabel('Email').fill('alice@example.com');
  await page.getByLabel('Password').fill('secure123');

  // Press Enter from any field to submit
  await page.getByLabel('Password').press('Enter');

  await expect(page).toHaveURL(/\/dashboard$/);
});

// ============================================================================
// Escape Key for Modals
// ============================================================================

test('user can close modal with Escape key', async ({ page }) => {
  await page.goto('http://localhost:3000');

  // Open modal
  await page.getByRole('button', { name: 'Open settings' }).click();
  await expect(page.getByRole('dialog')).toBeVisible();

  // Close modal with Escape
  await page.keyboard.press('Escape');
  await expect(page.getByRole('dialog')).not.toBeVisible();
});

// ============================================================================
// Arrow Key Navigation in Dropdowns
// ============================================================================

test('user can navigate dropdown with arrow keys', async ({ page }) => {
  await page.goto('http://localhost:3000/form');

  // Open dropdown
  await page.getByRole('combobox', { name: 'Country' }).click();

  // Navigate with arrow keys
  await page.keyboard.press('ArrowDown');
  await page.keyboard.press('ArrowDown');
  await page.keyboard.press('Enter'); // Select

  // Verify selection
  await expect(page.getByRole('combobox', { name: 'Country' })).toHaveValue('canada');
});

// ============================================================================
// Arrow Key Navigation in Lists
// ============================================================================

test('user can navigate list with arrow keys', async ({ page }) => {
  await page.goto('http://localhost:3000/products');

  // Focus first item
  await page.getByRole('listitem').first().focus();

  // Navigate down
  await page.keyboard.press('ArrowDown');
  await page.keyboard.press('ArrowDown');

  // Press Enter to select
  await page.keyboard.press('Enter');

  await expect(page).toHaveURL(/\/products\/\d+$/);
});

// ============================================================================
// Focus Trap in Modals
// ============================================================================

test('modal traps focus (accessibility requirement)', async ({ page }) => {
  await page.goto('http://localhost:3000');

  // Open modal
  await page.getByRole('button', { name: 'Delete account' }).click();
  const modal = page.getByRole('dialog');
  await expect(modal).toBeVisible();

  // Focus should be trapped in modal
  const cancelButton = modal.getByRole('button', { name: 'Cancel' });
  const confirmButton = modal.getByRole('button', { name: 'Confirm' });

  // Tab through modal elements
  await page.keyboard.press('Tab');
  await expect(cancelButton).toBeFocused();

  await page.keyboard.press('Tab');
  await expect(confirmButton).toBeFocused();

  // Tabbing again should cycle back to first focusable element
  await page.keyboard.press('Tab');
  await expect(cancelButton).toBeFocused();
});

// ============================================================================
// Skip Links (Accessibility)
// ============================================================================

test('skip link allows jumping to main content', async ({ page }) => {
  await page.goto('http://localhost:3000');

  // First Tab should focus skip link
  await page.keyboard.press('Tab');
  await expect(page.getByRole('link', { name: 'Skip to main content' })).toBeFocused();

  // Activate skip link
  await page.keyboard.press('Enter');

  // Focus should jump to main content
  await expect(page.getByRole('main')).toBeFocused();
});

// ============================================================================
// Complete Keyboard-Only Workflow
// ============================================================================

test('user can complete entire workflow with keyboard only', async ({ page }) => {
  await page.goto('http://localhost:3000');

  // Navigate to login (Tab through header nav)
  await page.keyboard.press('Tab'); // Skip link
  await page.keyboard.press('Tab'); // Logo
  await page.keyboard.press('Tab'); // Home
  await page.keyboard.press('Tab'); // Login link
  await page.keyboard.press('Enter'); // Activate link

  await expect(page).toHaveURL(/\/login$/);

  // Fill login form with keyboard
  await page.keyboard.press('Tab'); // Email field
  await page.keyboard.type('alice@example.com');
  await page.keyboard.press('Tab'); // Password field
  await page.keyboard.type('secure123');
  await page.keyboard.press('Tab'); // Submit button
  await page.keyboard.press('Enter'); // Submit

  await expect(page).toHaveURL(/\/dashboard$/);
  await expect(page.getByRole('heading', { name: 'Dashboard' })).toBeVisible();
});
