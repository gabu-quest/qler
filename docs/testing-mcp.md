# Testing Doctrine: MCP Servers
## Testing Model Context Protocol servers and tools

This document provides testing patterns for **MCP (Model Context Protocol)** servers built with Node.js/TypeScript.

**Prerequisites:**
- Read [docs/testing.md](./testing.md) - Core testing principles
- Read [docs/testing-nodejs.md](./testing-nodejs.md) - Node.js testing patterns

Uses **MUST / MUST NOT / SHOULD** normatively (RFC 2119).

---

## 1. MCP Server Overview

MCP servers expose **tools**, **resources**, and **prompts** to AI models like Claude. Testing them requires:

1. **Protocol compliance** - Correct JSON-RPC messages
2. **Tool correctness** - Tools work as specified
3. **Error handling** - Graceful failures with helpful messages
4. **Schema validation** - Input/output match declared schemas
5. **Integration** - Works with real MCP clients (Claude)

---

## 2. Test Categories

### 2.1 Unit Tests - Tool Logic

Test individual tool implementations without the MCP transport layer.

```typescript
import { describe, it, expect } from 'vitest'
import { analyzeSyntax } from './tools/analyze-syntax'

describe('analyzeSyntax tool', () => {
  it('returns syntax errors for invalid code', async () => {
    const result = await analyzeSyntax({
      code: 'const const',
      language: 'javascript'
    })

    expect(result.errors).toHaveLength(1)
    expect(result.errors[0]).toMatchObject({
      message: expect.stringContaining('Unexpected token'),
      line: 1,
      column: 6
    })
  })

  it('returns empty errors for valid code', async () => {
    const result = await analyzeSyntax({
      code: 'const x = 1',
      language: 'javascript'
    })

    expect(result.errors).toHaveLength(0)
    expect(result.ast).toBeDefined()
  })

  it('handles unsupported language gracefully', async () => {
    const result = await analyzeSyntax({
      code: 'print("hello")',
      language: 'brainfuck'
    })

    expect(result.errors).toHaveLength(1)
    expect(result.errors[0].message).toContain('Unsupported language')
  })
})
```

**You MUST:**
- ✅ Test tool logic independently
- ✅ Test all error paths
- ✅ Test edge cases (empty input, huge input, special chars)

### 2.2 Integration Tests - MCP Protocol

Test the MCP server's protocol implementation.

```typescript
import { describe, it, expect, beforeAll, afterAll } from 'vitest'
import { Client } from '@modelcontextprotocol/sdk/client/index.js'
import { StdioClientTransport } from '@modelcontextprotocol/sdk/client/stdio.js'
import type { ChildProcess } from 'node:child_process'
import { spawn } from 'node:child_process'

describe('MCP Server Integration', () => {
  let serverProcess: ChildProcess
  let client: Client
  let transport: StdioClientTransport

  beforeAll(async () => {
    // Start MCP server as subprocess
    serverProcess = spawn('node', ['dist/index.js'], {
      stdio: ['pipe', 'pipe', 'inherit']
    })

    // Connect MCP client
    transport = new StdioClientTransport({
      command: 'node',
      args: ['dist/index.js']
    })

    client = new Client({
      name: 'test-client',
      version: '1.0.0'
    }, {
      capabilities: {}
    })

    await client.connect(transport)
  })

  afterAll(async () => {
    await client.close()
    serverProcess.kill()
  })

  it('lists available tools', async () => {
    const response = await client.listTools()

    expect(response.tools).toEqual(
      expect.arrayContaining([
        expect.objectContaining({
          name: 'analyze_syntax',
          description: expect.any(String),
          inputSchema: expect.any(Object)
        })
      ])
    )
  })

  it('calls tool with valid input', async () => {
    const result = await client.callTool({
      name: 'analyze_syntax',
      arguments: {
        code: 'const x = 1',
        language: 'javascript'
      }
    })

    expect(result.content).toEqual([
      expect.objectContaining({
        type: 'text',
        text: expect.stringContaining('"errors": []')
      })
    ])
  })

  it('returns error for invalid tool name', async () => {
    await expect(
      client.callTool({
        name: 'nonexistent_tool',
        arguments: {}
      })
    ).rejects.toThrow(/Tool not found/)
  })

  it('validates tool input against schema', async () => {
    await expect(
      client.callTool({
        name: 'analyze_syntax',
        arguments: {
          code: 123, // Invalid - should be string
          language: 'javascript'
        }
      })
    ).rejects.toThrow(/Invalid input/)
  })
})
```

**You MUST:**
- ✅ Test with real MCP client (not mocked)
- ✅ Start server as subprocess
- ✅ Test all MCP operations (listTools, callTool, etc.)
- ✅ Validate protocol compliance

### 2.3 End-to-End Tests - Real Usage

Test how Claude would actually use your MCP server.

```typescript
describe('E2E: Code Analysis Workflow', () => {
  it('analyzes file, detects errors, and suggests fixes', async () => {
    // Step 1: Read file (via MCP resource or tool)
    const fileContent = await client.callTool({
      name: 'read_file',
      arguments: { path: 'test-fixtures/invalid.js' }
    })

    // Step 2: Analyze syntax
    const analysis = await client.callTool({
      name: 'analyze_syntax',
      arguments: {
        code: extractText(fileContent),
        language: 'javascript'
      }
    })

    const errors = JSON.parse(extractText(analysis)).errors
    expect(errors).toHaveLength(1)

    // Step 3: Get fix suggestions
    const fixes = await client.callTool({
      name: 'suggest_fixes',
      arguments: {
        code: extractText(fileContent),
        errors
      }
    })

    expect(extractText(fixes)).toContain('Replace "const const" with')
  })
})
```

---

## 3. Testing Tool Schemas

### 3.1 Schema Definition Testing

```typescript
import { describe, it, expect } from 'vitest'
import { z } from 'zod'
import { toolSchemas } from './schemas'

describe('Tool Schemas', () => {
  it('analyze_syntax schema validates correct input', () => {
    const validInput = {
      code: 'const x = 1',
      language: 'javascript'
    }

    expect(() => {
      toolSchemas.analyze_syntax.parse(validInput)
    }).not.toThrow()
  })

  it('rejects invalid language', () => {
    const invalid = {
      code: 'test',
      language: 'not-a-language'
    }

    const result = toolSchemas.analyze_syntax.safeParse(invalid)
    expect(result.success).toBe(false)
  })

  it('schema matches MCP inputSchema format', () => {
    // Convert Zod schema to JSON Schema
    const jsonSchema = zodToJsonSchema(toolSchemas.analyze_syntax)

    expect(jsonSchema).toMatchObject({
      type: 'object',
      properties: {
        code: { type: 'string' },
        language: { type: 'string', enum: ['javascript', 'typescript'] }
      },
      required: ['code', 'language']
    })
  })
})
```

**You MUST:**
- ✅ Test schema validation with valid input
- ✅ Test schema rejection with invalid input
- ✅ Ensure schema converts correctly to JSON Schema

### 3.2 Input Validation in Tools

```typescript
import { z } from 'zod'

const AnalyzeSyntaxInput = z.object({
  code: z.string().min(1).max(100000),
  language: z.enum(['javascript', 'typescript', 'python']),
  options: z.object({
    strict: z.boolean().default(false)
  }).optional()
})

export async function analyzeSyntax(
  rawInput: unknown
): Promise<AnalysisResult> {
  // Validate and parse input
  const input = AnalyzeSyntaxInput.parse(rawInput)

  // Now TypeScript knows exact types
  return performAnalysis(input.code, input.language, input.options)
}
```

---

## 4. Testing Error Handling

### 4.1 Tool Errors

```typescript
describe('Tool Error Handling', () => {
  it('returns structured error for parse failures', async () => {
    const result = await client.callTool({
      name: 'analyze_syntax',
      arguments: {
        code: 'const const const',
        language: 'javascript'
      }
    })

    const parsed = JSON.parse(extractText(result))

    expect(parsed.success).toBe(false)
    expect(parsed.error).toMatchObject({
      type: 'ParseError',
      message: expect.any(String),
      location: {
        line: expect.any(Number),
        column: expect.any(Number)
      }
    })
  })

  it('handles file not found errors gracefully', async () => {
    const result = await client.callTool({
      name: 'read_file',
      arguments: { path: '/nonexistent/file.js' }
    })

    const parsed = JSON.parse(extractText(result))

    expect(parsed.success).toBe(false)
    expect(parsed.error.type).toBe('FileNotFoundError')
    expect(parsed.error.message).toContain('/nonexistent/file.js')
  })

  it('returns helpful message for schema validation errors', async () => {
    await expect(
      client.callTool({
        name: 'analyze_syntax',
        arguments: { code: '' } // Missing language
      })
    ).rejects.toThrow(/Required field: language/)
  })
})
```

**You MUST:**
- ✅ Return structured errors (not just strings)
- ✅ Include error type and helpful message
- ✅ Include location info for parse/syntax errors
- ✅ Never expose internal stack traces to client

### 4.2 MCP Protocol Errors

```typescript
it('handles malformed JSON-RPC gracefully', async () => {
  // Send invalid JSON-RPC message
  const response = await sendRawMessage(transport, 'not json')

  expect(response).toMatchObject({
    jsonrpc: '2.0',
    error: {
      code: -32700, // Parse error
      message: 'Parse error'
    }
  })
})

it('returns method not found for unknown methods', async () => {
  const response = await sendRawMessage(transport, {
    jsonrpc: '2.0',
    method: 'unknown_method',
    id: 1
  })

  expect(response.error.code).toBe(-32601) // Method not found
})
```

---

## 5. Testing Async Operations

### 5.1 Long-Running Tools

```typescript
describe('Long-running analysis', () => {
  it('completes analysis within timeout', async () => {
    const result = await client.callTool({
      name: 'full_codebase_analysis',
      arguments: { path: 'test-fixtures/large-project' }
    }, {
      timeout: 30000 // 30s timeout
    })

    expect(result.content[0].type).toBe('text')
  }, 35000) // Vitest timeout slightly longer
})
```

### 5.2 Progress Reporting (Future MCP Feature)

```typescript
it('reports progress for long operations', async () => {
  const progressUpdates: number[] = []

  const result = await client.callTool({
    name: 'analyze_directory',
    arguments: { path: 'test-fixtures/large-dir' },
    onProgress: (progress) => {
      progressUpdates.push(progress.percentage)
    }
  })

  expect(progressUpdates).toEqual([25, 50, 75, 100])
})
```

---

## 6. Testing Resources

### 6.1 Resource Listing

```typescript
describe('MCP Resources', () => {
  it('lists available project files as resources', async () => {
    const resources = await client.listResources()

    expect(resources.resources).toEqual(
      expect.arrayContaining([
        expect.objectContaining({
          uri: 'file:///project/src/index.ts',
          name: 'src/index.ts',
          mimeType: 'text/typescript'
        })
      ])
    )
  })
})
```

### 6.2 Resource Reading

```typescript
it('reads resource content', async () => {
  const content = await client.readResource({
    uri: 'file:///project/src/index.ts'
  })

  expect(content.contents).toEqual([
    expect.objectContaining({
      type: 'text',
      text: expect.stringContaining('export'),
      mimeType: 'text/typescript'
    })
  ])
})
```

---

## 7. Testing Prompts

### 7.1 Prompt Templates

```typescript
describe('MCP Prompts', () => {
  it('lists available prompts', async () => {
    const prompts = await client.listPrompts()

    expect(prompts.prompts).toContainEqual({
      name: 'analyze_codebase',
      description: 'Analyze entire codebase for issues',
      arguments: [
        {
          name: 'path',
          description: 'Path to analyze',
          required: true
        }
      ]
    })
  })

  it('generates prompt with arguments', async () => {
    const result = await client.getPrompt({
      name: 'analyze_codebase',
      arguments: { path: '/project/src' }
    })

    expect(result.messages).toEqual([
      expect.objectContaining({
        role: 'user',
        content: {
          type: 'text',
          text: expect.stringContaining('Analyze the codebase at /project/src')
        }
      })
    ])
  })
})
```

---

## 8. Test Fixtures

### 8.1 Code Samples

**File: `tests/fixtures/valid-code.js`**

```javascript
// Valid JavaScript for testing
export function add(a, b) {
  return a + b
}
```

**File: `tests/fixtures/invalid-code.js`**

```javascript
// Invalid JavaScript for error testing
const const = 1
export default {
  missing: 'comma'
  another: 'field'
}
```

### 8.2 AST Snapshots

```typescript
import { readFileSync } from 'node:fs'
import { join } from 'node:path'

const fixtures = {
  valid: readFileSync(join(__dirname, 'fixtures/valid-code.js'), 'utf-8'),
  invalid: readFileSync(join(__dirname, 'fixtures/invalid-code.js'), 'utf-8')
}

it('parses valid code into expected AST', () => {
  const ast = parse(fixtures.valid)

  expect(ast).toMatchSnapshot()
})
```

---

## 9. Performance Testing

### 9.1 Tool Response Time

```typescript
describe('Performance', () => {
  it('analyzes small file in under 100ms', async () => {
    const start = performance.now()

    await client.callTool({
      name: 'analyze_syntax',
      arguments: {
        code: fixtures.small,
        language: 'javascript'
      }
    })

    const elapsed = performance.now() - start
    expect(elapsed).toBeLessThan(100)
  })

  it('handles 10 concurrent requests', async () => {
    const requests = Array.from({ length: 10 }, (_, i) =>
      client.callTool({
        name: 'analyze_syntax',
        arguments: {
          code: `const x${i} = ${i}`,
          language: 'javascript'
        }
      })
    )

    const results = await Promise.all(requests)
    expect(results).toHaveLength(10)
  })
})
```

### 9.2 Memory Usage

```typescript
it('does not leak memory on repeated calls', async () => {
  const initialMemory = process.memoryUsage().heapUsed

  // Make 1000 requests
  for (let i = 0; i < 1000; i++) {
    await client.callTool({
      name: 'analyze_syntax',
      arguments: { code: 'const x = 1', language: 'javascript' }
    })
  }

  // Force GC if available
  if (global.gc) global.gc()

  const finalMemory = process.memoryUsage().heapUsed
  const leak = finalMemory - initialMemory

  // Allow some growth, but not unbounded
  expect(leak).toBeLessThan(10 * 1024 * 1024) // < 10MB
})
```

---

## 10. Testing with Real Claude

### 10.1 Manual Integration Test

Create a test MCP server config:

**File: `claude_desktop_config.json`**

```json
{
  "mcpServers": {
    "syntax-analyzer": {
      "command": "node",
      "args": ["dist/index.js"]
    }
  }
}
```

**Manual test checklist:**
1. ✅ Server appears in Claude's MCP servers list
2. ✅ Tools are discoverable
3. ✅ Claude can call tools successfully
4. ✅ Errors are reported clearly
5. ✅ Response formatting is readable

### 10.2 Automated Claude Integration (Advanced)

```typescript
// Requires Claude API access
import Anthropic from '@anthropic-ai/sdk'

describe('Claude Integration', () => {
  it('uses MCP tool to analyze code', async () => {
    const anthropic = new Anthropic()

    const response = await anthropic.messages.create({
      model: 'claude-3-5-sonnet-20241022',
      max_tokens: 1024,
      messages: [{
        role: 'user',
        content: 'Analyze this code for errors: const const = 1'
      }],
      tools: [
        // Your MCP tool definition
      ]
    })

    const toolUse = response.content.find(c => c.type === 'tool_use')
    expect(toolUse).toBeDefined()
    expect(toolUse.name).toBe('analyze_syntax')
  })
})
```

---

## 11. CI/CD Integration

**File: `.github/workflows/mcp-test.yml`**

```yaml
name: MCP Server Tests

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
          cache: 'pnpm'

      - run: pnpm install --frozen-lockfile

      # Build before testing
      - run: pnpm run build

      # Unit tests (tool logic)
      - run: pnpm test src/**/*.test.ts

      # Integration tests (MCP protocol)
      - run: pnpm test tests/integration/**

      # E2E tests (full workflows)
      - run: pnpm test tests/e2e/**

      - run: pnpm test --coverage
```

---

## 12. Common Patterns

### 12.1 Test Helper: MCP Client Setup

**File: `tests/helpers/mcp-client.ts`**

```typescript
import { Client } from '@modelcontextprotocol/sdk/client/index.js'
import { StdioClientTransport } from '@modelcontextprotocol/sdk/client/stdio.js'

export async function createTestClient(): Promise<Client> {
  const transport = new StdioClientTransport({
    command: 'node',
    args: ['dist/index.js']
  })

  const client = new Client({
    name: 'test-client',
    version: '1.0.0'
  }, {
    capabilities: {}
  })

  await client.connect(transport)
  return client
}

export function extractText(result: any): string {
  return result.content[0].text
}
```

### 12.2 Test Helper: Fixture Loader

```typescript
import { readFileSync } from 'node:fs'
import { join } from 'node:path'

const FIXTURES_DIR = join(__dirname, '../fixtures')

export function loadFixture(name: string): string {
  return readFileSync(join(FIXTURES_DIR, name), 'utf-8')
}

export const fixtures = {
  validJS: loadFixture('valid.js'),
  invalidJS: loadFixture('invalid.js'),
  largeFile: loadFixture('large-file.js')
}
```

---

## 13. Anti-Patterns

### ❌ Bad: Mocking MCP SDK

```typescript
// Don't do this!
vi.mock('@modelcontextprotocol/sdk', () => ({
  Client: vi.fn()
}))
```

### ✅ Good: Real MCP Client

```typescript
// Test with actual MCP client
const client = await createTestClient()
const result = await client.callTool({ ... })
```

---

### ❌ Bad: Not Testing Error Cases

```typescript
it('analyzes code', async () => {
  const result = await analyzeSyntax({ code: 'const x = 1' })
  expect(result.errors).toHaveLength(0)
})
// Missing: What happens with invalid code?
```

### ✅ Good: Test All Paths

```typescript
it('returns errors for invalid code', async () => {
  const result = await analyzeSyntax({ code: 'const const' })
  expect(result.errors.length).toBeGreaterThan(0)
})
```

---

## 14. References

**MCP Resources:**
- [MCP Specification](https://spec.modelcontextprotocol.io/)
- [MCP TypeScript SDK](https://github.com/modelcontextprotocol/typescript-sdk)
- [MCP Server Examples](https://github.com/modelcontextprotocol/servers)

**Related Doctrines:**
- [Core Testing](./testing.md) - Testing principles
- [Node.js Testing](./testing-nodejs.md) - Node.js patterns
- [Static Analysis Testing](./testing-static-analysis.md) - AST/parser testing

**Tools:**
- [MCP Inspector](https://github.com/modelcontextprotocol/inspector) - Debug MCP servers
