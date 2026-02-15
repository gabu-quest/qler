# Testing Doctrine: Static Analysis Tools
## Testing parsers, AST analyzers, and code intelligence tools

This document provides testing patterns for **static analysis tools** - parsers, linters, formatters, type checkers, and AST-based code intelligence.

**Prerequisites:**
- Read [docs/testing.md](./testing.md) - Core testing principles
- Read [docs/testing-nodejs.md](./testing-nodejs.md) - Node.js testing patterns

Uses **MUST / MUST NOT / SHOULD** normatively (RFC 2119).

---

## 1. Static Analysis Tool Categories

### 1.1 Tool Types

**Parsers:**
- Convert source code → Abstract Syntax Tree (AST)
- Examples: Babel parser, TypeScript parser, Acorn

**Analyzers:**
- Traverse AST to find patterns/issues
- Examples: ESLint rules, type checkers

**Transformers:**
- Modify AST and generate new code
- Examples: Babel transforms, code formatters

**Code Intelligence:**
- Provide IDE features (completions, hover, references)
- Examples: Language servers, code navigation tools

---

## 2. Testing Parsers

### 2.1 Valid Input Tests

```typescript
import { describe, it, expect } from 'vitest'
import { parse } from './parser'

describe('Parser: Valid Input', () => {
  it('parses variable declaration', () => {
    const ast = parse('const x = 1')

    expect(ast.type).toBe('Program')
    expect(ast.body).toHaveLength(1)
    expect(ast.body[0].type).toBe('VariableDeclaration')
    expect(ast.body[0].declarations[0].id.name).toBe('x')
  })

  it('parses function with parameters and return', () => {
    const ast = parse('function add(a, b) { return a + b }')

    const funcDecl = ast.body[0]
    expect(funcDecl.type).toBe('FunctionDeclaration')
    expect(funcDecl.id.name).toBe('add')
    expect(funcDecl.params).toHaveLength(2)
    expect(funcDecl.params[0].name).toBe('a')
    expect(funcDecl.params[1].name).toBe('b')
  })

  it('parses async/await syntax', () => {
    const ast = parse('async function fetch() { await getData() }')

    expect(ast.body[0].async).toBe(true)
  })
})
```

**You MUST:**
- ✅ Test all language constructs
- ✅ Test modern syntax (async/await, destructuring, etc.)
- ✅ Assert on AST structure, not just "it doesn't throw"

### 2.2 Invalid Input Tests

```typescript
describe('Parser: Invalid Input', () => {
  it('throws on unexpected token', () => {
    expect(() => parse('const const')).toThrow(SyntaxError)
  })

  it('provides location information in error', () => {
    try {
      parse('function foo()\n  const x = 1\n}')
      expect.fail('Should have thrown')
    } catch (error) {
      expect(error).toBeInstanceOf(SyntaxError)
      expect(error.line).toBe(2)
      expect(error.column).toBe(2)
      expect(error.message).toContain('Unexpected token')
    }
  })

  it('handles unclosed string literal', () => {
    expect(() => parse('const x = "unclosed')).toThrow(/Unterminated string/)
  })

  it('handles mismatched brackets', () => {
    expect(() => parse('function foo() { return 1')).toThrow(/Expected }/)
  })
})
```

**You MUST:**
- ✅ Test common syntax errors
- ✅ Verify error messages are helpful
- ✅ Include line/column in errors
- ✅ Test edge cases (unclosed strings, mismatched parens)

### 2.3 Location/Position Testing

```typescript
describe('Parser: Source Locations', () => {
  it('tracks accurate line and column numbers', () => {
    const source = `
function add(a, b) {
  return a + b
}
    `.trim()

    const ast = parse(source, { locations: true })
    const funcDecl = ast.body[0]

    expect(funcDecl.loc).toMatchObject({
      start: { line: 1, column: 0 },
      end: { line: 3, column: 1 }
    })

    expect(funcDecl.id.loc).toMatchObject({
      start: { line: 1, column: 9 },
      end: { line: 1, column: 12 }
    })
  })

  it('maintains source ranges for error recovery', () => {
    const ast = parse('const x = 1; const y = 2', { ranges: true })

    expect(ast.body[0].range).toEqual([0, 11]) // 'const x = 1'
    expect(ast.body[1].range).toEqual([13, 24]) // 'const y = 2'
  })
})
```

**Why:** Accurate locations are critical for:
- Error messages
- Syntax highlighting
- Go-to-definition
- Refactoring tools

### 2.4 Edge Cases & Stress Tests

```typescript
describe('Parser: Edge Cases', () => {
  it('parses empty source', () => {
    const ast = parse('')
    expect(ast.body).toHaveLength(0)
  })

  it('parses file with only comments', () => {
    const ast = parse('// comment\n/* block */')
    expect(ast.body).toHaveLength(0)
  })

  it('handles deeply nested structures', () => {
    const nested = Array(100).fill('{ x: ').join('') +
                   '1' +
                   Array(100).fill(' }').join('')

    expect(() => parse(`const obj = ${nested}`)).not.toThrow()
  })

  it('handles large source files efficiently', () => {
    // Generate 10,000 line file
    const source = Array(10000)
      .fill(0)
      .map((_, i) => `const var${i} = ${i}`)
      .join('\n')

    const start = performance.now()
    const ast = parse(source)
    const elapsed = performance.now() - start

    expect(ast.body).toHaveLength(10000)
    expect(elapsed).toBeLessThan(1000) // < 1s for 10k lines
  })

  it('handles unicode identifiers', () => {
    const ast = parse('const café = 1')
    expect(ast.body[0].declarations[0].id.name).toBe('café')
  })

  it('preserves exact source text in ranges', () => {
    const source = 'const   x  =   1  '
    const ast = parse(source, { ranges: true })

    const [start, end] = ast.body[0].range
    expect(source.slice(start, end)).toBe('const   x  =   1')
  })
})
```

---

## 3. Testing AST Analyzers

### 3.1 Rule/Check Testing

```typescript
import { analyzeAST } from './analyzer'
import { parse } from './parser'

describe('Analyzer: No Console Rule', () => {
  it('detects console.log usage', () => {
    const ast = parse('console.log("hello")')
    const issues = analyzeAST(ast, { rules: ['no-console'] })

    expect(issues).toHaveLength(1)
    expect(issues[0]).toMatchObject({
      rule: 'no-console',
      severity: 'warning',
      message: 'Unexpected console statement',
      line: 1,
      column: 0
    })
  })

  it('allows console.error and console.warn', () => {
    const ast = parse('console.error("error")\nconsole.warn("warning")')
    const issues = analyzeAST(ast, {
      rules: ['no-console'],
      ruleOptions: {
        'no-console': { allow: ['error', 'warn'] }
      }
    })

    expect(issues).toHaveLength(0)
  })

  it('detects console in nested scopes', () => {
    const ast = parse(`
      function foo() {
        if (debug) {
          console.log("debug")
        }
      }
    `)

    const issues = analyzeAST(ast, { rules: ['no-console'] })
    expect(issues).toHaveLength(1)
  })
})
```

### 3.2 Pattern Matching Tests

```typescript
describe('Analyzer: Unused Variables', () => {
  it('detects variable declared but never used', () => {
    const ast = parse(`
      const used = 1
      const unused = 2
      console.log(used)
    `)

    const issues = analyzeAST(ast, { rules: ['no-unused-vars'] })

    expect(issues).toHaveLength(1)
    expect(issues[0].message).toContain('unused')
  })

  it('ignores variables prefixed with underscore', () => {
    const ast = parse('const _ignored = 1')
    const issues = analyzeAST(ast, { rules: ['no-unused-vars'] })

    expect(issues).toHaveLength(0)
  })

  it('handles function parameters', () => {
    const ast = parse(`
      function add(a, b) {
        return a // b is unused
      }
    `)

    const issues = analyzeAST(ast, { rules: ['no-unused-vars'] })
    expect(issues.some(i => i.message.includes('b'))).toBe(true)
  })
})
```

### 3.3 Scope Analysis Tests

```typescript
describe('Analyzer: Scope Resolution', () => {
  it('resolves variable in outer scope', () => {
    const ast = parse(`
      const x = 1
      function foo() {
        return x // References outer x
      }
    `)

    const scope = analyzeScope(ast)
    const reference = scope.findReference('x')

    expect(reference.resolved).toBe(true)
    expect(reference.scope).toBe('module')
  })

  it('detects shadowing', () => {
    const ast = parse(`
      const x = 1
      function foo() {
        const x = 2 // Shadows outer x
      }
    `)

    const issues = analyzeAST(ast, { rules: ['no-shadow'] })
    expect(issues).toHaveLength(1)
  })

  it('handles block scoping correctly', () => {
    const ast = parse(`
      const x = 1
      {
        const x = 2 // Different binding
      }
      console.log(x) // Uses first x
    `)

    const scope = analyzeScope(ast)
    expect(scope.bindings.get('x').references).toHaveLength(2)
  })
})
```

---

## 4. Testing Code Transformers

### 4.1 Transform Correctness

```typescript
import { transform } from './transformer'

describe('Transformer: Arrow Function → Regular Function', () => {
  it('converts arrow function to function declaration', () => {
    const input = 'const add = (a, b) => a + b'
    const output = transform(input)

    expect(output).toBe('const add = function(a, b) { return a + b }')
  })

  it('preserves async functions', () => {
    const input = 'const fetch = async () => await getData()'
    const output = transform(input)

    expect(output).toContain('async function')
  })

  it('handles destructuring parameters', () => {
    const input = 'const fn = ({ x, y }) => x + y'
    const output = transform(input)

    expect(output).toMatch(/function\s*\(\s*{\s*x,\s*y\s*}\s*\)/)
  })
})
```

### 4.2 Idempotency Tests

```typescript
describe('Transformer: Idempotency', () => {
  it('produces same output when run twice', () => {
    const input = 'const x = (a) => a * 2'

    const output1 = transform(input)
    const output2 = transform(output1)

    expect(output1).toBe(output2)
  })
})
```

### 4.3 Whitespace/Formatting Preservation

```typescript
describe('Transformer: Formatting', () => {
  it('preserves indentation', () => {
    const input = `
function foo() {
  const x = (a) => a + 1
  return x(5)
}
    `.trim()

    const output = transform(input)

    expect(output).toContain('  const x = function')
  })

  it('preserves comments', () => {
    const input = `
// Important function
const add = (a, b) => a + b
    `.trim()

    const output = transform(input)
    expect(output).toContain('// Important function')
  })
})
```

---

## 5. Testing Type Checkers

### 5.1 Type Inference Tests

```typescript
describe('Type Checker: Inference', () => {
  it('infers number type from literal', () => {
    const source = 'const x = 42'
    const types = inferTypes(parse(source))

    expect(types.get('x')).toBe('number')
  })

  it('infers function return type', () => {
    const source = 'function add(a: number, b: number) { return a + b }'
    const types = inferTypes(parse(source))

    expect(types.get('add')).toMatchObject({
      type: 'function',
      params: ['number', 'number'],
      return: 'number'
    })
  })

  it('infers union type from conditional', () => {
    const source = `
      let x = Math.random() > 0.5 ? 'hello' : 42
    `
    const types = inferTypes(parse(source))

    expect(types.get('x')).toBe('string | number')
  })
})
```

### 5.2 Type Error Detection

```typescript
describe('Type Checker: Errors', () => {
  it('detects type mismatch in assignment', () => {
    const source = 'const x: number = "string"'
    const errors = checkTypes(parse(source))

    expect(errors).toHaveLength(1)
    expect(errors[0].message).toContain('Type \'string\' is not assignable to type \'number\'')
  })

  it('detects wrong number of function arguments', () => {
    const source = `
      function add(a: number, b: number) { return a + b }
      add(1) // Missing argument
    `
    const errors = checkTypes(parse(source))

    expect(errors).toContainEqual(
      expect.objectContaining({
        message: expect.stringContaining('Expected 2 arguments, but got 1')
      })
    )
  })
})
```

---

## 6. Snapshot Testing for ASTs

### 6.1 When to Use Snapshots

Use snapshots for:
- ✅ Complex AST structures
- ✅ Regression detection
- ✅ Golden test files

Do NOT use for:
- ❌ Simple assertions
- ❌ Frequently changing output
- ❌ Non-deterministic data

### 6.2 AST Snapshot Pattern

```typescript
describe('Parser: Snapshot Tests', () => {
  it('parses class declaration', () => {
    const ast = parse(`
      class Person {
        constructor(name) {
          this.name = name
        }

        greet() {
          return \`Hello, \${this.name}\`
        }
      }
    `)

    // Remove location info for stable snapshots
    const cleanAST = removeLocations(ast)

    expect(cleanAST).toMatchSnapshot()
  })
})
```

**Helper:**

```typescript
function removeLocations(node: any): any {
  if (!node || typeof node !== 'object') return node

  const cleaned = Array.isArray(node) ? [...node] : { ...node }

  delete cleaned.loc
  delete cleaned.range
  delete cleaned.start
  delete cleaned.end

  for (const key in cleaned) {
    cleaned[key] = removeLocations(cleaned[key])
  }

  return cleaned
}
```

---

## 7. Property-Based Testing

### 7.1 Roundtrip Testing (Parse → Generate → Parse)

```typescript
import { fc, test } from '@fast-check/vitest'

test.prop([fc.javascript()])('parser roundtrip', (code) => {
  const ast1 = parse(code)
  const generated = generate(ast1)
  const ast2 = parse(generated)

  // ASTs should be equivalent (ignoring locations)
  expect(removeLocations(ast2)).toEqual(removeLocations(ast1))
})
```

### 7.2 Invariant Testing

```typescript
import { fc, test } from '@fast-check/vitest'

test.prop([fc.javascript()])('parser never crashes', (code) => {
  // Parser should either succeed or throw expected error
  try {
    const ast = parse(code)
    expect(ast.type).toBe('Program')
  } catch (error) {
    expect(error).toBeInstanceOf(SyntaxError)
    expect(error.line).toBeGreaterThan(0)
  }
})

test.prop([fc.javascript({ validOnly: true })])(
  'valid code always parses',
  (code) => {
    expect(() => parse(code)).not.toThrow()
  }
)
```

### 7.3 Custom Generators

```typescript
import { fc } from '@fast-check/vitest'

const identifier = fc.stringMatching(/^[a-zA-Z_][a-zA-Z0-9_]*$/)

const variableDeclaration = fc.record({
  kind: fc.constantFrom('const', 'let', 'var'),
  name: identifier,
  value: fc.oneof(
    fc.integer(),
    fc.string(),
    fc.boolean()
  )
}).map(({ kind, name, value }) =>
  `${kind} ${name} = ${JSON.stringify(value)}`
)

test.prop([variableDeclaration])(
  'parses variable declarations',
  (code) => {
    const ast = parse(code)
    expect(ast.body[0].type).toBe('VariableDeclaration')
  }
)
```

---

## 8. Performance Testing

### 8.1 Benchmarking

```typescript
import { describe, it, expect } from 'vitest'
import { readFileSync } from 'node:fs'

describe('Parser Performance', () => {
  const realWorldFiles = {
    small: readFileSync('fixtures/small.js', 'utf-8'),    // ~100 LOC
    medium: readFileSync('fixtures/medium.js', 'utf-8'),  // ~1000 LOC
    large: readFileSync('fixtures/large.js', 'utf-8')     // ~10000 LOC
  }

  it('parses small file in < 10ms', () => {
    const start = performance.now()
    parse(realWorldFiles.small)
    const elapsed = performance.now() - start

    expect(elapsed).toBeLessThan(10)
  })

  it('parses medium file in < 50ms', () => {
    const start = performance.now()
    parse(realWorldFiles.medium)
    const elapsed = performance.now() - start

    expect(elapsed).toBeLessThan(50)
  })

  it('parses large file in < 500ms', () => {
    const start = performance.now()
    parse(realWorldFiles.large)
    const elapsed = performance.now() - start

    expect(elapsed).toBeLessThan(500)
  })
})
```

### 8.2 Memory Usage

```typescript
it('does not leak memory on repeated parses', () => {
  const source = 'const x = 1'

  const initialMem = process.memoryUsage().heapUsed

  for (let i = 0; i < 10000; i++) {
    parse(source)
  }

  if (global.gc) global.gc()

  const finalMem = process.memoryUsage().heapUsed
  const leak = finalMem - initialMem

  expect(leak).toBeLessThan(5 * 1024 * 1024) // < 5MB
})
```

---

## 9. Golden Tests (Reference Files)

### 9.1 Test Suite Structure

```
tests/
├── fixtures/
│   ├── valid/
│   │   ├── arrow-functions.js
│   │   ├── async-await.js
│   │   └── classes.js
│   ├── invalid/
│   │   ├── syntax-error.js
│   │   └── unexpected-token.js
│   └── expected/
│       ├── arrow-functions.json   # Expected AST
│       ├── async-await.json
│       └── classes.json
└── golden.test.ts
```

### 9.2 Golden Test Implementation

```typescript
import { readdirSync, readFileSync } from 'node:fs'
import { join } from 'node:path'

describe('Golden Tests: Valid Inputs', () => {
  const validDir = join(__dirname, 'fixtures/valid')
  const expectedDir = join(__dirname, 'fixtures/expected')

  const testFiles = readdirSync(validDir).filter(f => f.endsWith('.js'))

  for (const file of testFiles) {
    it(`parses ${file} correctly`, () => {
      const source = readFileSync(join(validDir, file), 'utf-8')
      const ast = parse(source)

      const expectedFile = file.replace('.js', '.json')
      const expectedPath = join(expectedDir, expectedFile)

      if (existsSync(expectedPath)) {
        const expected = JSON.parse(readFileSync(expectedPath, 'utf-8'))
        expect(removeLocations(ast)).toEqual(expected)
      } else {
        // Generate expected file (for new tests)
        writeFileSync(
          expectedPath,
          JSON.stringify(removeLocations(ast), null, 2)
        )
      }
    })
  }
})
```

---

## 10. Testing Error Recovery

### 10.1 Partial Parsing

```typescript
describe('Parser: Error Recovery', () => {
  it('recovers from syntax error and continues parsing', () => {
    const source = `
      const x = 1
      const const // Syntax error here
      const y = 2
    `

    const result = parseWithRecovery(source)

    expect(result.ast.body).toHaveLength(2) // x and y
    expect(result.errors).toHaveLength(1)
    expect(result.errors[0].line).toBe(3)
  })

  it('provides suggestions for common mistakes', () => {
    const source = 'fucntion add() {}' // Typo

    const result = parseWithRecovery(source)

    expect(result.errors[0].message).toContain('Did you mean "function"?')
  })
})
```

---

## 11. Testing Code Intelligence Features

### 11.1 Go-to-Definition

```typescript
describe('Code Intelligence: Go-to-Definition', () => {
  it('finds variable definition', () => {
    const source = `
      const x = 1
      console.log(x)
    `
    const position = { line: 3, column: 18 } // On 'x' in console.log

    const definition = findDefinition(parse(source), position)

    expect(definition).toMatchObject({
      line: 2,
      column: 12, // Start of 'x' in declaration
      name: 'x'
    })
  })

  it('finds function definition across scopes', () => {
    const source = `
      function helper() {}

      function main() {
        helper()
      }
    `
    const position = { line: 5, column: 8 } // On 'helper' call

    const definition = findDefinition(parse(source), position)
    expect(definition.line).toBe(2)
  })
})
```

### 11.2 Find References

```typescript
describe('Code Intelligence: Find References', () => {
  it('finds all references to variable', () => {
    const source = `
      const x = 1
      console.log(x)
      const y = x + 2
    `

    const references = findReferences(parse(source), 'x')

    expect(references).toHaveLength(3)
    expect(references).toContainEqual(
      expect.objectContaining({ line: 2 }) // Declaration
    )
    expect(references).toContainEqual(
      expect.objectContaining({ line: 3 }) // First use
    )
  })
})
```

---

## 12. Common Anti-Patterns

### ❌ Bad: Not Testing Error Cases

```typescript
// Only tests happy path
it('parses code', () => {
  expect(() => parse('const x = 1')).not.toThrow()
})
```

### ✅ Good: Test Both Valid and Invalid

```typescript
it('parses valid code', () => {
  const ast = parse('const x = 1')
  expect(ast.body).toHaveLength(1)
})

it('throws on invalid code with location', () => {
  try {
    parse('const const')
  } catch (error) {
    expect(error.line).toBeDefined()
    expect(error.column).toBeDefined()
  }
})
```

---

### ❌ Bad: Brittle AST Assertions

```typescript
// Too specific, breaks on minor changes
expect(ast).toEqual({
  type: 'Program',
  body: [
    {
      type: 'VariableDeclaration',
      declarations: [
        {
          type: 'VariableDeclarator',
          id: { type: 'Identifier', name: 'x', loc: { ... } },
          // ... every single property
        }
      ]
    }
  ]
})
```

### ✅ Good: Focus on Important Properties

```typescript
expect(ast.type).toBe('Program')
expect(ast.body[0].type).toBe('VariableDeclaration')
expect(ast.body[0].declarations[0].id.name).toBe('x')
```

---

## 13. CI/CD for Static Analysis Tools

```yaml
name: Parser Tests

on: [push, pull_request]

jobs:
  test:
    runs-on: ubuntu-latest

    steps:
      - uses: actions/checkout@v4
      - uses: pnpm/action-setup@v2
      - uses: actions/setup-node@v4
        with:
          node-version: 20

      - run: pnpm install --frozen-lockfile
      - run: pnpm run build

      # Unit tests
      - run: pnpm test src/

      # Golden tests
      - run: pnpm test tests/golden/

      # Performance benchmarks
      - run: pnpm run benchmark

      # Coverage
      - run: pnpm test --coverage
      - uses: codecov/codecov-action@v3
```

---

## 14. References

**Tools & Libraries:**
- [Babel Parser](https://babeljs.io/docs/babel-parser)
- [TypeScript Compiler API](https://github.com/microsoft/TypeScript/wiki/Using-the-Compiler-API)
- [@typescript-eslint/typescript-estree](https://github.com/typescript-eslint/typescript-eslint/tree/main/packages/typescript-estree)
- [Oxc Parser](https://oxc-project.github.io/) - Fast Rust-based parser
- [fast-check](https://fast-check.dev/) - Property-based testing

**Related Doctrines:**
- [Core Testing](./testing.md) - Testing principles
- [Node.js Testing](./testing-nodejs.md) - Node.js patterns
- [MCP Testing](./testing-mcp.md) - MCP server testing

**AST Resources:**
- [ESTree Spec](https://github.com/estree/estree) - JavaScript AST standard
- [AST Explorer](https://astexplorer.net/) - Interactive AST viewer
