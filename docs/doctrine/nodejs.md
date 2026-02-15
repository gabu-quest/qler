# Node.js/TypeScript Stack Doctrine
## Modern JavaScript/TypeScript standards for server-side and tooling projects

This doctrine defines the authoritative Node.js and TypeScript standards for The Standard.

Uses **MUST / MUST NOT / SHOULD** normatively (RFC 2119).

---

## 1. Prime Directive

**Use modern Node.js with strict TypeScript, deterministic package management, and production-grade testing.**

---

## 2. Technology Stack

### 2.1 Runtime & Language

You MUST use:
- **Node.js 20 LTS or later** - Active LTS with modern features
- **TypeScript 5.x** - Latest stable with strict mode
- **ESM (ES Modules)** - Native `import/export`, not CommonJS

You MUST NOT use:
- ❌ Node.js < 18 (out of LTS)
- ❌ CommonJS (`require/module.exports`) for new code
- ❌ JavaScript without TypeScript (except build configs)
- ❌ Loose TypeScript mode

**Why:** Modern Node.js provides native ESM, top-level await, and performance improvements. TypeScript strict mode catches bugs at compile time.

### 2.2 Package Management

You MUST use **one** of these (consistently within a project):
- **pnpm** (recommended) - Fast, disk-efficient, strict
- **npm** - Built-in, widely supported
- **yarn** (modern/berry) - Workspaces, PnP

You MUST:
- ✅ Commit lockfiles (`pnpm-lock.yaml`, `package-lock.json`, `yarn.lock`)
- ✅ Use exact versions for production dependencies
- ✅ Use `^` for dev dependencies only
- ✅ Keep dependencies up to date (Renovate, Dependabot)

You MUST NOT:
- ❌ Mix package managers in one project
- ❌ Commit `node_modules/`
- ❌ Use `npm install -g` for project dependencies
- ❌ Use wildcards (`*`, `latest`) in `package.json`

**Why:** Lockfiles ensure reproducible builds. Exact versions prevent supply chain attacks and "works on my machine" bugs.

### 2.3 Build & Bundling

For **libraries/tools:**
- **tsup** or **unbuild** - Zero-config TypeScript bundling
- Output: ESM + CJS for compatibility

For **applications:**
- **esbuild** or **Vite** - Fast, modern bundlers
- Output: ESM only

You MUST:
- ✅ Bundle TypeScript before publishing
- ✅ Generate `.d.ts` declaration files
- ✅ Include source maps for debugging
- ✅ Tree-shake unused code

You MUST NOT:
- ❌ Publish raw TypeScript to npm
- ❌ Use webpack for new projects (legacy only)
- ❌ Omit declaration files from libraries

---

## 3. TypeScript Configuration

### 3.1 Compiler Options

**File: `tsconfig.json`**

```json
{
  "compilerOptions": {
    // Strictness (all MUST be true)
    "strict": true,
    "noUncheckedIndexedAccess": true,
    "noImplicitOverride": true,
    "exactOptionalPropertyTypes": true,

    // Module system
    "module": "ESNext",
    "moduleResolution": "bundler",
    "target": "ES2022",

    // Emit
    "outDir": "./dist",
    "declaration": true,
    "declarationMap": true,
    "sourceMap": true,

    // Quality
    "skipLibCheck": false,
    "forceConsistentCasingInFileNames": true,
    "resolveJsonModule": true,
    "isolatedModules": true
  },
  "include": ["src/**/*"],
  "exclude": ["node_modules", "dist", "**/*.test.ts"]
}
```

### 3.2 Type Safety

You MUST:
- ✅ Enable all strict flags
- ✅ Avoid `any` - use `unknown` for truly unknown types
- ✅ Use branded types for IDs, tokens, etc.
- ✅ Use discriminated unions for state machines

You SHOULD:
- ⚠️ Use Zod or Valibot for runtime validation
- ⚠️ Use branded types for domain primitives
- ⚠️ Prefer `interface` over `type` for object shapes (better errors)

**Example: Branded Types**

```typescript
// Good - Type-safe IDs
type UserId = string & { __brand: 'UserId' }
type ProjectId = string & { __brand: 'ProjectId' }

function getUser(id: UserId): User { ... }

const userId = "user_123" as UserId
const projectId = "proj_456" as ProjectId

getUser(userId) // ✅ OK
getUser(projectId) // ❌ Type error - can't mix IDs
```

---

## 4. Project Structure

### 4.1 Directory Layout

```
my-project/
├── src/
│   ├── index.ts           # Main entry point
│   ├── lib/               # Core logic
│   │   ├── parser.ts
│   │   └── analyzer.ts
│   ├── types/             # Shared types
│   │   └── ast.ts
│   └── utils/             # Utilities
│       └── logger.ts
├── tests/                 # Tests (colocate or separate)
│   ├── unit/
│   ├── integration/
│   └── fixtures/
├── dist/                  # Build output (gitignored)
├── package.json
├── tsconfig.json
├── tsconfig.test.json     # Test-specific config
└── vitest.config.ts       # Test config
```

**Alternative: Colocated Tests**

```
src/
├── parser.ts
├── parser.test.ts         # Next to implementation
├── analyzer.ts
└── analyzer.test.ts
```

You MUST:
- ✅ Use consistent naming (`kebab-case.ts` or `camelCase.ts`, not both)
- ✅ Separate `src/` (source) from `dist/` (build output)
- ✅ Keep test fixtures in version control

### 4.2 Entry Points

**File: `package.json`**

```json
{
  "type": "module",
  "exports": {
    ".": {
      "types": "./dist/index.d.ts",
      "import": "./dist/index.js"
    }
  },
  "files": ["dist"],
  "engines": {
    "node": ">=20"
  }
}
```

You MUST:
- ✅ Use `"type": "module"` for ESM
- ✅ Specify `exports` for package entry points
- ✅ Include `types` field for TypeScript consumers
- ✅ Define `engines` to prevent old Node.js usage

---

## 5. Code Quality Tools

### 5.1 Linting

You MUST use **ESLint 9+** with flat config:

**File: `eslint.config.js`**

```javascript
import js from '@eslint/js'
import typescript from '@typescript-eslint/eslint-plugin'
import tsParser from '@typescript-eslint/parser'

export default [
  js.configs.recommended,
  {
    files: ['src/**/*.ts'],
    languageOptions: {
      parser: tsParser,
      parserOptions: {
        project: './tsconfig.json'
      }
    },
    plugins: { '@typescript-eslint': typescript },
    rules: {
      '@typescript-eslint/no-explicit-any': 'error',
      '@typescript-eslint/no-unused-vars': ['error', {
        argsIgnorePattern: '^_'
      }],
      '@typescript-eslint/strict-boolean-expressions': 'error',
      'no-console': ['warn', { allow: ['error', 'warn'] }]
    }
  }
]
```

### 5.2 Formatting

You MUST use **Prettier** or **Biome**:

**File: `.prettierrc`**

```json
{
  "semi": false,
  "singleQuote": true,
  "trailingComma": "es5",
  "printWidth": 100,
  "tabWidth": 2
}
```

**OR use Biome** (faster, Rust-based):

```json
{
  "formatter": {
    "enabled": true,
    "indentStyle": "space",
    "lineWidth": 100
  },
  "linter": {
    "enabled": true,
    "rules": {
      "recommended": true
    }
  }
}
```

You MUST NOT:
- ❌ Skip formatting checks in CI
- ❌ Debate style preferences (automate it)
- ❌ Mix formatting tools

---

## 6. Dependency Management

### 6.1 Categories

**Production Dependencies:**
```json
{
  "dependencies": {
    "zod": "3.22.4"  // Exact version
  }
}
```

**Development Dependencies:**
```json
{
  "devDependencies": {
    "vitest": "^1.5.0",      // Can auto-update minor
    "typescript": "^5.4.0"
  }
}
```

### 6.2 Recommended Stack

**For MCP Servers:**
- `@modelcontextprotocol/sdk` - Official MCP SDK
- `zod` - Runtime validation and type inference

**For Static Analysis:**
- `@typescript-eslint/typescript-estree` - TypeScript AST parser
- `oxc-parser` - Fast Rust-based parser (for JavaScript/TypeScript)

**For Testing:**
- `vitest` - Fast, Vite-powered test runner
- `@vitest/ui` - Browser-based test UI
- `c8` or `v8` - Native code coverage

**For Logging:**
- `pino` - Fast structured logging
- `consola` - Pretty console output

You SHOULD avoid:
- ⚠️ Moment.js (use native `Temporal` or `date-fns`)
- ⚠️ Lodash (most utils now in native JS)
- ⚠️ Request (deprecated, use `fetch` or `undici`)

---

## 7. Scripts Convention

**File: `package.json`**

```json
{
  "scripts": {
    "dev": "tsx watch src/index.ts",
    "build": "tsup",
    "test": "vitest",
    "test:ui": "vitest --ui",
    "test:coverage": "vitest --coverage",
    "lint": "eslint src/",
    "format": "prettier --write src/",
    "typecheck": "tsc --noEmit",
    "ci": "pnpm run typecheck && pnpm run lint && pnpm run test"
  }
}
```

You MUST:
- ✅ Provide `test`, `build`, `lint` scripts
- ✅ Have a `ci` script that runs all checks
- ✅ Use `tsx` for development (fast TS execution)

---

## 8. Performance & Security

### 8.1 Performance

You MUST:
- ✅ Use native APIs where available (`fetch`, `crypto`, `fs/promises`)
- ✅ Avoid blocking the event loop (use workers for CPU-heavy tasks)
- ✅ Stream large data instead of buffering

**Example: Streaming**

```typescript
// Good - Stream processing
async function processLargeFile(path: string) {
  const stream = createReadStream(path)
  for await (const chunk of stream) {
    await processChunk(chunk)
  }
}

// Bad - Load entire file into memory
async function processLargeFileBad(path: string) {
  const content = await readFile(path, 'utf-8') // May OOM
  await processContent(content)
}
```

### 8.2 Security

You MUST:
- ✅ Validate all external input with Zod or similar
- ✅ Use `node:crypto` for randomness, not `Math.random()`
- ✅ Sanitize paths with `node:path` to prevent traversal
- ✅ Run `npm audit` or `pnpm audit` in CI

You MUST NOT:
- ❌ Use `eval()`, `Function()`, or `vm.runInThisContext()`
- ❌ Trust user input without validation
- ❌ Use deprecated APIs (`url.parse`, `crypto.createCipher`)

---

## 9. Documentation

### 9.1 Code Documentation

You MUST:
- ✅ Use JSDoc for public APIs
- ✅ Document complex types
- ✅ Include examples in JSDoc

**Example:**

```typescript
/**
 * Parses source code into an Abstract Syntax Tree.
 *
 * @param source - The source code to parse
 * @param options - Parser configuration
 * @returns AST representation of the source
 *
 * @example
 * ```typescript
 * const ast = parseSource('const x = 1', { ecmaVersion: 2022 })
 * console.log(ast.type) // 'Program'
 * ```
 */
export function parseSource(
  source: string,
  options: ParseOptions
): AST { ... }
```

### 9.2 README

You MUST include:
- ✅ Installation instructions
- ✅ Quick start example
- ✅ API reference or link to generated docs
- ✅ Development setup

---

## 10. Publishing

### 10.1 npm Package

Before publishing, you MUST:
- ✅ Build and bundle TypeScript
- ✅ Include `.d.ts` files
- ✅ Test package locally with `npm pack`
- ✅ Use semantic versioning

**File: `package.json`**

```json
{
  "name": "@yourscope/package-name",
  "version": "1.0.0",
  "description": "Brief description",
  "repository": "github:user/repo",
  "license": "MIT",
  "type": "module",
  "exports": {
    ".": {
      "types": "./dist/index.d.ts",
      "import": "./dist/index.js"
    }
  },
  "files": ["dist", "README.md", "LICENSE"],
  "scripts": {
    "prepublishOnly": "pnpm run build && pnpm test"
  }
}
```

You MUST NOT:
- ❌ Publish without running tests
- ❌ Include `node_modules/` or `src/` in published package
- ❌ Publish with uncommitted changes

---

## 11. Common Anti-Patterns

### ❌ Bad: Loose Types

```typescript
function processData(data: any): any {
  return data.map((item: any) => item.value)
}
```

### ✅ Good: Strict Types

```typescript
interface DataItem {
  value: string
}

function processData(data: DataItem[]): string[] {
  return data.map(item => item.value)
}
```

---

### ❌ Bad: Unvalidated Input

```typescript
app.post('/api/users', (req, res) => {
  const user = req.body // any
  database.insert(user)
})
```

### ✅ Good: Validated Input

```typescript
import { z } from 'zod'

const UserSchema = z.object({
  name: z.string().min(1),
  email: z.string().email()
})

app.post('/api/users', (req, res) => {
  const result = UserSchema.safeParse(req.body)
  if (!result.success) {
    return res.status(400).json(result.error)
  }
  database.insert(result.data)
})
```

---

### ❌ Bad: Blocking Event Loop

```typescript
function fibonacci(n: number): number {
  if (n <= 1) return n
  return fibonacci(n - 1) + fibonacci(n - 2) // Blocks!
}
```

### ✅ Good: Use Worker Threads

```typescript
import { Worker } from 'node:worker_threads'

async function fibonacci(n: number): Promise<number> {
  return new Promise((resolve, reject) => {
    const worker = new Worker('./fib-worker.js', { workerData: n })
    worker.on('message', resolve)
    worker.on('error', reject)
  })
}
```

---

## 12. References

**Official:**
- [Node.js Documentation](https://nodejs.org/docs/latest/api/)
- [TypeScript Handbook](https://www.typescriptlang.org/docs/)
- [ESLint Rules](https://eslint.org/docs/latest/rules/)

**Related Doctrines:**
- [Testing Doctrine](../testing.md) - Core testing principles
- [Style Doctrine](./style.md) - Code style and patterns
- [Security Doctrine](./security.md) - Security baseline

**Next:**
- See [docs/testing-nodejs.md](../testing-nodejs.md) for Node.js testing patterns
- See [docs/testing-mcp.md](../testing-mcp.md) for MCP server testing
