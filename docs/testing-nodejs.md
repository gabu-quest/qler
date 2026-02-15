# Testing Doctrine: Node.js/TypeScript
## Testing patterns for Node.js applications and libraries

This document extends [docs/testing.md](./testing.md) with Node.js and TypeScript-specific testing patterns.

**Prerequisites:** Read [docs/testing.md](./testing.md) first for core testing principles.

Uses **MUST / MUST NOT / SHOULD** normatively (RFC 2119).

---

## 1. Test Framework: Vitest

You MUST use **Vitest** for all Node.js testing:

**Why Vitest:**
- ✅ Fast - Vite-powered, instant HMR
- ✅ ESM-native - Works with modern `import/export`
- ✅ TypeScript-first - No config needed
- ✅ Jest-compatible API - Easy migration
- ✅ Built-in coverage - Native V8 coverage
- ✅ Watch mode - Instant feedback

You MUST NOT use:
- ❌ Jest (unless maintaining legacy project) - Slow, CommonJS-focused
- ❌ Mocha + Chai - Too much boilerplate
- ❌ AVA - Less ecosystem support

---

## 2. Configuration

### 2.1 Vitest Config

**File: `vitest.config.ts`**

```typescript
import { defineConfig } from 'vitest/config'

export default defineConfig({
  test: {
    // Globals (optional - prefer explicit imports)
    globals: false, // Explicit imports preferred

    // Environment
    environment: 'node', // or 'jsdom' for browser-like

    // Coverage
    coverage: {
      provider: 'v8',
      reporter: ['text', 'json', 'html'],
      exclude: [
        'dist/**',
        '**/*.test.ts',
        '**/*.config.*',
        '**/types/**'
      ],
      thresholds: {
        lines: 80,
        functions: 80,
        branches: 75,
        statements: 80
      }
    },

    // Timeouts
    testTimeout: 5000,
    hookTimeout: 10000,

    // Isolation
    isolate: true,
    pool: 'forks', // or 'threads'
  }
})
```

### 2.2 TypeScript Config for Tests

**File: `tsconfig.test.json`**

```json
{
  "extends": "./tsconfig.json",
  "compilerOptions": {
    "types": ["vitest/globals", "node"]
  },
  "include": ["src/**/*.test.ts", "tests/**/*"]
}
```

---

## 3. Test Structure & Naming

### 3.1 File Organization

**Option A: Colocated (Recommended)**

```
src/
├── parser.ts
├── parser.test.ts         # Tests next to implementation
├── analyzer.ts
└── analyzer.test.ts
```

**Option B: Separate Directory**

```
tests/
├── unit/
│   ├── parser.test.ts
│   └── analyzer.test.ts
├── integration/
│   └── pipeline.test.ts
└── fixtures/
    ├── valid-code.ts
    └── invalid-code.ts
```

You MUST:
- ✅ Use `.test.ts` suffix (not `.spec.ts`)
- ✅ Mirror source structure if using separate directory
- ✅ Keep fixtures in version control

### 3.2 Test Naming

```typescript
import { describe, it, expect } from 'vitest'

describe('parseSource', () => {
  it('parses valid JavaScript into AST', () => {
    const source = 'const x = 1'
    const ast = parseSource(source)

    expect(ast.type).toBe('Program')
    expect(ast.body).toHaveLength(1)
  })

  it('throws ParseError when source has syntax errors', () => {
    const invalidSource = 'const const'

    expect(() => parseSource(invalidSource)).toThrow(ParseError)
  })

  it('preserves source locations when sourceType is module', () => {
    const source = 'export const x = 1'
    const ast = parseSource(source, { sourceType: 'module' })

    expect(ast.body[0].loc).toBeDefined()
  })
})
```

**Naming conventions:**
- `describe` - Function/class/module name
- `it` - Behavior description (Given-When-Then)
- Use present tense ("parses", "throws", "preserves")

---

## 4. Realistic Testing (No Mocks)

### 4.1 Real File I/O

```typescript
import { mkdir, writeFile, readFile, rm } from 'node:fs/promises'
import { join } from 'node:path'
import { tmpdir } from 'node:os'
import { describe, it, expect, beforeEach, afterEach } from 'vitest'

describe('FileAnalyzer', () => {
  let testDir: string

  beforeEach(async () => {
    // Create real temp directory
    testDir = join(tmpdir(), `test-${Date.now()}`)
    await mkdir(testDir, { recursive: true })
  })

  afterEach(async () => {
    // Clean up real files
    await rm(testDir, { recursive: true, force: true })
  })

  it('analyzes TypeScript files and returns diagnostics', async () => {
    // Write real file
    const filePath = join(testDir, 'test.ts')
    await writeFile(filePath, 'const x: number = "not a number"')

    // Test with real file
    const analyzer = new FileAnalyzer()
    const diagnostics = await analyzer.analyze(filePath)

    expect(diagnostics).toHaveLength(1)
    expect(diagnostics[0].message).toContain('Type \'string\' is not assignable')
  })
})
```

You MUST:
- ✅ Use real filesystem via `fs/promises`
- ✅ Create temp directories with unique names
- ✅ Clean up in `afterEach`

You MUST NOT:
- ❌ Mock `fs` module
- ❌ Leave temp files behind
- ❌ Write to project directory during tests

### 4.2 Real HTTP Requests (Integration Tests)

```typescript
import { describe, it, expect, beforeAll, afterAll } from 'vitest'
import type { Server } from 'node:http'

describe('API Server', () => {
  let server: Server
  let baseUrl: string

  beforeAll(async () => {
    // Start real server
    server = await startServer({ port: 0 }) // Random port
    const addr = server.address()
    baseUrl = `http://localhost:${addr.port}`
  })

  afterAll(async () => {
    // Stop real server
    await new Promise(resolve => server.close(resolve))
  })

  it('returns 200 OK for GET /health', async () => {
    // Real HTTP request
    const response = await fetch(`${baseUrl}/health`)

    expect(response.status).toBe(200)
    expect(await response.json()).toEqual({ status: 'healthy' })
  })

  it('returns 400 when request body is invalid', async () => {
    const response = await fetch(`${baseUrl}/api/users`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ invalid: 'data' })
    })

    expect(response.status).toBe(400)
  })
})
```

You MUST:
- ✅ Start real server on random port
- ✅ Use native `fetch` for requests
- ✅ Stop server in `afterAll`

### 4.3 Real Databases (SQLite for Tests)

```typescript
import Database from 'better-sqlite3'
import { beforeEach, afterEach, it, expect } from 'vitest'

describe('UserRepository', () => {
  let db: Database.Database
  let repo: UserRepository

  beforeEach(() => {
    // In-memory SQLite database
    db = new Database(':memory:')

    // Apply schema
    db.exec(`
      CREATE TABLE users (
        id INTEGER PRIMARY KEY,
        email TEXT UNIQUE NOT NULL
      )
    `)

    repo = new UserRepository(db)
  })

  afterEach(() => {
    db.close()
  })

  it('inserts user and retrieves by email', () => {
    const user = repo.create({ email: 'test@example.com' })

    expect(user.id).toBeGreaterThan(0)

    const found = repo.findByEmail('test@example.com')
    expect(found).toEqual(user)
  })

  it('throws error when email already exists', () => {
    repo.create({ email: 'test@example.com' })

    expect(() => {
      repo.create({ email: 'test@example.com' })
    }).toThrow('UNIQUE constraint failed')
  })
})
```

You MUST:
- ✅ Use in-memory SQLite for fast tests
- ✅ Apply full schema in `beforeEach`
- ✅ Close database in `afterEach`

You SHOULD:
- ⚠️ Use same DB type as production (Postgres → pg + testcontainers)

---

## 5. Deterministic Testing

### 5.1 Time Travel (Not sleep)

```typescript
import { describe, it, expect, vi } from 'vitest'

// ❌ BAD - Non-deterministic delay
it('retries after 100ms', async () => {
  const start = Date.now()
  await retryWithBackoff(failingFn)
  const elapsed = Date.now() - start
  expect(elapsed).toBeGreaterThan(100) // Flaky!
})

// ✅ GOOD - Mock timers
it('retries after 100ms using fake timers', async () => {
  vi.useFakeTimers()

  const promise = retryWithBackoff(failingFn)

  await vi.advanceTimersByTimeAsync(100)

  await expect(promise).resolves.toBe('success')

  vi.useRealTimers()
})
```

You MUST:
- ✅ Use `vi.useFakeTimers()` for time-dependent code
- ✅ Use `vi.advanceTimersByTimeAsync()` not `vi.advanceTimersToNextTimer()`
- ✅ Restore real timers with `vi.useRealTimers()`

You MUST NOT:
- ❌ Use `setTimeout` or `sleep` in tests
- ❌ Test actual wall-clock durations

### 5.2 Randomness (Seeded)

```typescript
import { describe, it, expect } from 'vitest'

// ❌ BAD - Non-deterministic
it('generates random ID', () => {
  const id = generateId() // Uses Math.random()
  expect(id).toMatch(/^[a-z0-9]{8}$/) // Different every run
})

// ✅ GOOD - Inject randomness
it('generates deterministic ID with seeded RNG', () => {
  const rng = () => 0.5 // Fixed value
  const id = generateId({ rng })

  expect(id).toBe('gggggggg') // Always same
})

// ✅ BETTER - Test distribution properties
import { createRandomGenerator } from './test-utils'

it('generates IDs with uniform distribution', () => {
  const rng = createRandomGenerator(12345) // Seeded
  const ids = Array.from({ length: 1000 }, () => generateId({ rng }))

  // Test properties, not exact values
  expect(new Set(ids).size).toBe(1000) // All unique
  expect(ids.every(id => /^[a-z0-9]{8}$/.test(id))).toBe(true)
})
```

### 5.3 Current Date/Time

```typescript
import { describe, it, expect, vi } from 'vitest'

describe('ExpirationChecker', () => {
  it('marks token as expired when current time exceeds expiry', () => {
    // Mock current time
    vi.setSystemTime(new Date('2024-01-15T12:00:00Z'))

    const token = {
      value: 'abc',
      expiresAt: new Date('2024-01-15T11:00:00Z')
    }

    expect(isExpired(token)).toBe(true)

    // Restore
    vi.useRealTimers()
  })
})
```

You MUST:
- ✅ Use `vi.setSystemTime()` to fix current time
- ✅ Inject date/time dependencies for complex logic
- ✅ Restore timers after test

---

## 6. Async Testing

### 6.1 Async/Await (Preferred)

```typescript
import { it, expect } from 'vitest'

it('fetches user data from API', async () => {
  const response = await fetch('/api/users/123')
  const user = await response.json()

  expect(user.id).toBe(123)
})
```

### 6.2 Promise Assertions

```typescript
import { it, expect } from 'vitest'

it('rejects when user not found', async () => {
  await expect(fetchUser(999)).rejects.toThrow('User not found')
})

it('resolves with user data', async () => {
  await expect(fetchUser(123)).resolves.toMatchObject({
    id: 123,
    email: expect.stringContaining('@')
  })
})
```

### 6.3 Concurrent Tests

```typescript
import { describe, it } from 'vitest'

describe.concurrent('Independent API tests', () => {
  it('GET /users returns list', async () => {
    // Test 1
  })

  it('GET /products returns list', async () => {
    // Test 2 - runs in parallel
  })
})
```

You SHOULD use `.concurrent` when:
- ⚠️ Tests are truly independent
- ⚠️ No shared mutable state
- ⚠️ Tests are I/O bound (not CPU)

You MUST NOT use `.concurrent` when:
- ❌ Tests modify shared resources (DB, files)
- ❌ Tests have race conditions

---

## 7. Type Testing

### 7.1 Test Type Safety

```typescript
import { it, expect, expectTypeOf } from 'vitest'
import type { User, AdminUser } from './types'

it('type narrowing works correctly', () => {
  const user: User | AdminUser = getUser()

  if (user.role === 'admin') {
    // TypeScript should narrow to AdminUser
    expectTypeOf(user).toMatchTypeOf<AdminUser>()
    expectTypeOf(user.permissions).toBeArray()
  }
})

it('generic function infers types correctly', () => {
  const result = mapArray([1, 2, 3], n => n.toString())

  expectTypeOf(result).toEqualTypeOf<string[]>()
})
```

### 7.2 Runtime Validation Testing

```typescript
import { z } from 'zod'
import { it, expect } from 'vitest'

const UserSchema = z.object({
  id: z.number().positive(),
  email: z.string().email()
})

it('validates correct user object', () => {
  const valid = { id: 1, email: 'test@example.com' }

  expect(() => UserSchema.parse(valid)).not.toThrow()
  expect(UserSchema.safeParse(valid).success).toBe(true)
})

it('rejects invalid user object', () => {
  const invalid = { id: -1, email: 'not-an-email' }

  const result = UserSchema.safeParse(invalid)
  expect(result.success).toBe(false)
  if (!result.success) {
    expect(result.error.issues).toHaveLength(2)
  }
})
```

---

## 8. Error Testing

### 8.1 Error Classes

```typescript
import { it, expect } from 'vitest'

class ParseError extends Error {
  constructor(
    message: string,
    public readonly line: number,
    public readonly column: number
  ) {
    super(message)
    this.name = 'ParseError'
  }
}

it('throws ParseError with location info', () => {
  expect(() => parse('invalid')).toThrow(ParseError)

  try {
    parse('invalid')
    expect.fail('Should have thrown')
  } catch (error) {
    expect(error).toBeInstanceOf(ParseError)
    if (error instanceof ParseError) {
      expect(error.line).toBe(1)
      expect(error.column).toBe(0)
    }
  }
})
```

### 8.2 Error Messages

```typescript
it('provides helpful error message for syntax errors', () => {
  const source = 'const x ='

  expect(() => parse(source)).toThrow('Unexpected end of input')
  expect(() => parse(source)).toThrow(/line 1, column 9/)
})
```

You MUST:
- ✅ Test error types (not just that it throws)
- ✅ Test error messages contain useful info
- ✅ Test error properties (line, column, etc.)

---

## 9. Snapshot Testing (Use Sparingly)

```typescript
import { it, expect } from 'vitest'

it('generates expected AST structure', () => {
  const ast = parse('const x = 1')

  // Snapshot - only for stable output
  expect(ast).toMatchSnapshot()
})
```

You SHOULD use snapshots for:
- ⚠️ Complex object structures (ASTs, configs)
- ⚠️ Stable output formats
- ⚠️ Regression detection

You MUST NOT use snapshots for:
- ❌ Dates, timestamps, IDs (non-deterministic)
- ❌ Long text (use inline snapshots instead)
- ❌ As substitute for proper assertions

**Inline snapshots (Better):**

```typescript
it('generates expected output', () => {
  expect(format(ast)).toMatchInlineSnapshot(`
    "const x = 1;
    "
  `)
})
```

---

## 10. Coverage Requirements

You MUST achieve:
- ✅ **80%+ line coverage** for application code
- ✅ **75%+ branch coverage** for complex logic
- ✅ **100% coverage** for critical paths (auth, security, payments)

You MUST NOT:
- ❌ Write tests just to hit coverage numbers
- ❌ Skip edge cases to save time
- ❌ Ignore uncovered error handling

**Check coverage:**

```bash
pnpm test --coverage
```

**View HTML report:**

```bash
open coverage/index.html
```

---

## 11. Test Performance

### 11.1 Fast Tests

You MUST keep tests fast:
- ✅ Unit tests: < 100ms each
- ✅ Integration tests: < 1s each
- ✅ Full suite: < 30s total

You SHOULD:
- ⚠️ Use `describe.skip` to temporarily disable slow tests
- ⚠️ Use `it.only` during development
- ⚠️ Profile slow tests: `pnpm test --reporter=verbose`

### 11.2 Parallel Execution

Vitest runs tests in parallel by default. You MUST ensure:
- ✅ Tests are isolated (no shared state)
- ✅ Temp files use unique names
- ✅ Servers use random ports

```typescript
// Good - Unique temp directory
const testDir = join(tmpdir(), `test-${process.pid}-${Date.now()}`)

// Good - Random port
const server = await startServer({ port: 0 })
```

---

## 12. CI Integration

**File: `.github/workflows/test.yml`**

```yaml
name: Test

on: [push, pull_request]

jobs:
  test:
    runs-on: ubuntu-latest

    steps:
      - uses: actions/checkout@v4

      - uses: pnpm/action-setup@v2
        with:
          version: 8

      - uses: actions/setup-node@v4
        with:
          node-version: 20
          cache: 'pnpm'

      - run: pnpm install --frozen-lockfile

      - run: pnpm run typecheck
      - run: pnpm run lint
      - run: pnpm test --coverage

      - uses: codecov/codecov-action@v3
        if: always()
```

You MUST:
- ✅ Run tests on every push
- ✅ Upload coverage to Codecov or similar
- ✅ Use frozen lockfile in CI
- ✅ Test on Node.js LTS versions

---

## 13. Common Patterns

### 13.1 Test Utilities

**File: `tests/utils.ts`**

```typescript
import { randomBytes } from 'node:crypto'

export function createRandomGenerator(seed: number) {
  let state = seed
  return function random() {
    state = (state * 9301 + 49297) % 233280
    return state / 233280
  }
}

export function createTempFile(content: string): Promise<string> {
  const path = join(tmpdir(), `test-${randomBytes(8).toString('hex')}.tmp`)
  return writeFile(path, content).then(() => path)
}
```

### 13.2 Custom Matchers

```typescript
import { expect } from 'vitest'

expect.extend({
  toBeValidEmail(received: string) {
    const pass = /^[^@]+@[^@]+\.[^@]+$/.test(received)
    return {
      pass,
      message: () => `Expected ${received} to be valid email`,
      actual: received
    }
  }
})

// Usage
it('validates email format', () => {
  expect('test@example.com').toBeValidEmail()
})
```

---

## 14. Anti-Patterns

### ❌ Bad: Global Mocks

```typescript
vi.mock('node:fs/promises', () => ({
  readFile: vi.fn().mockResolvedValue('mocked content')
}))
```

### ✅ Good: Dependency Injection

```typescript
interface FileReader {
  read(path: string): Promise<string>
}

class RealFileReader implements FileReader {
  async read(path: string) {
    return readFile(path, 'utf-8')
  }
}

// In tests
class FakeFileReader implements FileReader {
  async read(path: string) {
    return 'test content'
  }
}
```

---

## 15. References

**Related Doctrines:**
- [Core Testing Doctrine](./testing.md) - Read this first
- [Node.js Stack Doctrine](./doctrine/nodejs.md) - Tech stack standards
- [MCP Testing Guide](./testing-mcp.md) - MCP server patterns
- [Static Analysis Testing](./testing-static-analysis.md) - AST/parser testing

**Tools:**
- [Vitest Documentation](https://vitest.dev)
- [better-sqlite3](https://github.com/WiseLibs/better-sqlite3)
- [Zod](https://zod.dev)
