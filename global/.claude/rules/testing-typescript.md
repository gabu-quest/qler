---
description: "TypeScript/Vitest/Jest testing patterns — concrete bad-to-good rewrites, data-testid selectors"
paths:
  - "**/*.ts"
  - "**/*.tsx"
  - "**/*.vue"
  - "**/*.js"
  - "**/*.jsx"
---

# TypeScript/JavaScript Testing Patterns

Extends `testing-core.md` with TypeScript/Vitest/Jest-specific guidance. See core for universal anti-patterns and banned assertions.

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

## CONCRETE REWRITE EXAMPLES (from real audit findings)

Every banned pattern found in audits, with the exact rewrite:

| Found in audit | Rewrite to |
|----------------|-----------|
| `expect(x).toBeDefined()` (alone) | `expect(x).toBe(expectedValue)` |
| `expect(x).toHaveProperty("key")` (alone) | `expect(x.key).toBe(expectedValue)` |
| `expect(x).toHaveLength(n)` (alone on filtered results) | Add `expect(x[0].field).toBe(expectedValue)` after |
| `result.forEach(...)` (no length check first) | `expect(result).toHaveLength(n)` then `result.forEach(...)` |
| `expect(filtered).toHaveLength(2)` (alone) | Add `expect(filtered[0].message).toBe('exact message')` |

```typescript
// BEFORE (softball - count only):
store.setSearchQuery('ERROR')
expect(store.filteredEntries).toHaveLength(2)

// AFTER (meaningful):
store.setSearchQuery('ERROR')
expect(store.filteredEntries).toHaveLength(2)
expect(store.filteredEntries[0].message).toBe('ERROR occurred')
expect(store.filteredEntries[1].message).toBe('error handled')

// BEFORE (softball - forEach without length):
filtered.forEach(e => {
  expect(e.level).toBe('ERROR')
})

// AFTER (meaningful):
expect(filtered).toHaveLength(3)
filtered.forEach(e => {
  expect(e.level).toBe('ERROR')
})

// BEFORE (softball - just count):
store.setCorrelationFilter('user')
expect(store.filteredEntries).toHaveLength(2)

// AFTER (meaningful):
store.setCorrelationFilter('user')
expect(store.filteredEntries).toHaveLength(2)
expect(store.filteredEntries[0].correlation_id).toBe('user-request-001')
expect(store.filteredEntries[1].correlation_id).toBe('user-request-002')
```

## Vitest/Jest Best Practices

- Use `data-testid` attributes for reliable selectors
- Never use `wrapper.vm` - test through the public interface (DOM)
- Use `mountWithApp` helpers that include providers (Pinia, i18n, router)
- Run with `npm run test` or `vitest`
- Use `toThrow()` or `rejects.toThrow()` with specific error messages
