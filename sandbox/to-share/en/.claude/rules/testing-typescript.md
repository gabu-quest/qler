---
paths:
  - "**/*.ts"
  - "**/*.tsx"
  - "**/*.vue"
  - "**/*.js"
  - "**/*.jsx"
---

# TypeScript/JavaScript Testing Patterns

Extends `testing-core.md` with TypeScript/Vitest/Jest-specific guidance.

## Softball Patterns to NEVER Use

```typescript
// BAD: Type-only checks
expect(result).toBeInstanceOf(Object)
expect(typeof result).toBe('object')

// BAD: Existence without behavior
expect(result).toBeDefined()
expect(result).not.toBeNull()

// BAD: Empty arrays pass silently
result.items.forEach(item => {
  expect(item.status).toBe('active')  // Never executes if empty!
})

// BAD: Truthy checks hide bugs
expect(result.data).toBeTruthy()  // Empty string, 0, [] all fail but {} passes

// BAD: `or` logic in assertions
expect(result.type === 'A' || result.type === 'B').toBe(true)
```

## Correct Patterns

```typescript
// GOOD: Assert specific values
expect(result.status).toBe('success')

// GOOD: Assert exact counts before iterating
expect(result.items).toHaveLength(5)
result.items.forEach(item => {
  expect(item.status).toBe('active')
})

// GOOD: Use test.each for parameterized tests
test.each([
  ['ERROR', 25],
  ['INFO', 25],
  ['DEBUG', 25],
])('filters by level %s', (level, expectedCount) => {
  const result = search({ level })
  expect(result.items).toHaveLength(expectedCount)
  result.items.forEach(item => {
    expect(item.level).toBe(level)
  })
})

// GOOD: Snapshot for complex objects (but verify manually first!)
expect(result).toMatchInlineSnapshot(`
  {
    "status": "success",
    "count": 5,
  }
`)
```

## Vue Component Testing

```typescript
import { mount } from '@vue/test-utils'
import { mountWithApp } from '@/test-utils/mountWithApp'

// GOOD: Test user interactions, not implementation
it('emits save when button clicked', async () => {
  const wrapper = mountWithApp(MyComponent)
  await wrapper.find('[data-testid="save-btn"]').trigger('click')
  expect(wrapper.emitted('save')).toHaveLength(1)
})

// BAD: Accessing internal state
it('bad test', () => {
  const wrapper = mount(MyComponent)
  expect(wrapper.vm.internalState).toBe(true)  // Implementation detail!
})

// GOOD: Test through the DOM
it('shows error message when validation fails', async () => {
  const wrapper = mountWithApp(MyComponent)
  await wrapper.find('input').setValue('')
  await wrapper.find('form').trigger('submit')
  expect(wrapper.find('[data-testid="error"]').text()).toContain('Required')
})
```

## Vitest/Jest Best Practices

- Use `data-testid` attributes for reliable selectors
- Never use `wrapper.vm` - test through the public interface (DOM)
- Use `mountWithApp` helpers that include providers (Pinia, i18n, router)
- Run with `npm run test` or `vitest`
- Use `toThrow()` or `rejects.toThrow()` with specific error messages
