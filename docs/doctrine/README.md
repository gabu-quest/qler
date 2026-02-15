# Engineering Doctrine Index

This directory contains **domain-specific doctrine documents** that extend the core standards in [`agents.md`](../../agents.md) and [`testing.md`](../testing.md).

---

## Core Doctrines (Start Here)

**Read these first:**
- **[`../../agents.md`](../../agents.md)** — Core agent operating doctrine
- **[`../testing.md`](../testing.md)** — Universal testing principles

---

## Domain-Specific Doctrines

| Doctrine | Prime Directive | When to Load |
|----------|----------------|--------------|
| **[`design.md`](./design.md)** | Change the system intentionally | Planning features, architectural decisions |
| **[`git.md`](./git.md)** | Git history is part of the product | Git operations, branching, commits |
| **[`ci.md`](./ci.md)** | Fast feedback, quality gates | CI/CD setup, pipeline optimization |
| **[`security.md`](./security.md)** | Secure by default | Security-sensitive code, auth, input validation |
| **[`style.md`](./style.md)** | Clarity over cleverness | Code review, API design, naming |
| **[`handoff.md`](./handoff.md)** | Leave clear state for next session | Multi-session work, orchestrator integration |
| **[`nodejs.md`](./nodejs.md)** | Modern Node.js with strict TypeScript | Node.js/TypeScript projects, tooling, MCP servers |

---

## Framework-Specific Testing Doctrines

**Prerequisites:** Read [`../testing.md`](../testing.md) first.

### Python/FastAPI

| Framework Guide | Prime Directive | When to Load |
|----------------|----------------|--------------|
| **[`../testing-fastapi.md`](../testing-fastapi.md)** | Dependency override > mocking | FastAPI web API testing |
| **[`../testing-hypothesis.md`](../testing-hypothesis.md)** | Pure functions, clear properties | Property-based testing with Hypothesis |

**Quick References:**
- [`../quick-reference/testing-fastapi-quick-ref.md`](../quick-reference/testing-fastapi-quick-ref.md)
- [`../quick-reference/testing-hypothesis-quick-ref.md`](../quick-reference/testing-hypothesis-quick-ref.md)

### Node.js/TypeScript

| Framework Guide | Prime Directive | When to Load |
|----------------|----------------|--------------|
| **[`../testing-nodejs.md`](../testing-nodejs.md)** | Vitest with realistic tests | Node.js/TypeScript testing |
| **[`../testing-mcp.md`](../testing-mcp.md)** | Test with real MCP client | MCP server development |
| **[`../testing-static-analysis.md`](../testing-static-analysis.md)** | Test parsers, not just happy paths | Static analysis tools, AST work |

### Frontend

| Framework Guide | Prime Directive | When to Load |
|----------------|----------------|--------------|
| **[`../testing-playwright.md`](../testing-playwright.md)** | Accessibility selectors first | Advanced E2E testing with Playwright |

**Quick References:**
- [`../quick-reference/testing-playwright-quick-ref.md`](../quick-reference/testing-playwright-quick-ref.md)

---

## How to Use

### For AI Agents

1. **Always load:** `agents.md` + `testing.md` (core doctrine)
2. **Load as needed:** Specific doctrine from the table above
3. **Context-efficient:** Framework guides are modular (~600-800 tokens each)

### For Human Engineers

1. Start with [`../../agents.md`](../../agents.md) to understand the development philosophy
2. Review [`../testing.md`](../testing.md) for testing standards
3. Reference specific doctrines as needed for your work

---

## Adoption

To adopt these doctrines in your project:

1. Copy relevant files to your repository:
   ```bash
   cp -r docs/doctrine your-repo/docs/
   ```

2. Link from your main README

3. Reference in your agents.md or project documentation

---

## Standards

All doctrine documents use:
- **MUST / MUST NOT / SHOULD** (RFC 2119 normative language)
- Rationale for each rule (the "why")
- Concrete examples (good vs bad)
- Cross-references to related doctrines

---

**See also:** [`CLAUDE.md`](../../CLAUDE.md) for complete context and reading guide.
