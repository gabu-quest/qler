# The Standard

**Version 1.0.0** | [Changelog](./CHANGELOG.md) | [Quick Start](#quick-start) | [Adoption Guide](./adoption/CHECKLIST.md)

**A comprehensive, LLM-friendly engineering doctrine and design system for modern software development.**

This repository defines the authoritative standards, practices, and design patterns for building high-quality software with AI agents and human engineers working together.

## What's Inside

### 🤖 Core Agent Doctrine

**[`agents.md`](./agents.md)** — The authoritative operating rules for AI agents working in codebases.

Defines how agents should:
- Execute work decisively without artificial limitations
- Make senior-engineer quality decisions
- Handle tasks end-to-end with proper testing
- Maintain clean Git history
- Follow modern tooling standards (Python 3.12+, Vue 3, etc.)
- Work in specialized roles (Planner, Dev, Test Engineer, Security Reviewer, etc.)

**Key principle:** Fast, decisive execution with rigorous correctness and minimal drama.

---

### 📋 Engineering Doctrines

Located in **[`docs/doctrine/`](./docs/doctrine/)** — Specialized doctrine documents covering all aspects of software engineering:

- **[testing.md](./docs/testing.md)** — Modern, strict testing rules for deterministic, realistic, documentary tests
- **[design.md](./docs/doctrine/design.md)** — When to write design docs and ADRs; how to avoid architecture drift
- **[git.md](./docs/doctrine/git.md)** — Branching, commits, merges, conflict handling, `.gitignore` policy
- **[ci.md](./docs/doctrine/ci.md)** — Fast vs full test suites, caching, artifacts, flake policy, quality gates
- **[security.md](./docs/doctrine/security.md)** — Secure defaults, OWASP-aligned baseline, security testing readiness
- **[style.md](./docs/doctrine/style.md)** — Code layout, naming, patterns, API envelopes, UI/A11Y consistency
- **[handoff.md](./docs/doctrine/handoff.md)** — Handoff format and protocols for orchestrators and multi-session work

All doctrine documents use **MUST / MUST NOT / SHOULD** in the normative standards sense (RFC 2119 style).

---

### 🎨 Vue 3 Design Systems

**For Vue 3 projects:** See **[The Style](https://github.com/gabu-quest/the-style)** - A separate repository with two complete Naive UI design systems (Goshuin & Cyberpunk editions).

---

## 📚 Resources

### Quick References
One-page printable summaries for quick reference:
- **[Agent Doctrine Quick Ref](./docs/quick-reference/agents-quick-ref.md)** - Core principles on one page
- **[Testing Quick Ref](./docs/quick-reference/testing-quick-ref.md)** - Testing rules summary

### Examples
Real-world examples of artifacts and workflows:
- **[Planning Artifacts](./examples/planning-artifacts/)** - Sample SPEC, TASKS, DESIGN, PLAN
- **[Pull Requests](./examples/pull-request/)** - Example PR descriptions
- **[ADRs](./examples/adr/)** - Architecture Decision Record examples

### For AI Agents
- **[CLAUDE.md](./CLAUDE.md)** - Context file for AI agents working in this repository
- Use this to understand how to maintain and improve The Standard itself

### Local Settings (DO NOT SHARE)
- `.claude/settings.local.json` is personal and must not be committed or shared
- Use `.claude/settings.local.example.json` as a template and keep local settings in `.gitignore`

### Adoption
- **[Adoption Checklist](./adoption/CHECKLIST.md)** - Step-by-step guide to adopting The Standard
- **[GitHub Templates](./.github/)** - Issue and PR templates enforcing the standard

---

## Quick Start

### Using as a GitHub Template (Recommended)

This repository is a **GitHub template**. To use it:

1. **Click "Use this template"** at the top of this repo
2. **Create your new repository** from the template
3. **Follow the [Template Usage Guide](./TEMPLATE_USAGE.md)** for setup

See **[TEMPLATE_USAGE.md](./TEMPLATE_USAGE.md)** for complete instructions.

---

### For AI Agents

1. Read **[`agents.md`](./agents.md)** first — this defines your core operating rules
2. Review **[`docs/testing.md`](./docs/testing.md)** — non-negotiable testing standards
3. Reference other doctrine docs as needed for specialized work
4. Use **[`.claude/skills/`](./.claude/skills/)** — Reusable skills for code review, design, testing, handoffs

### For Human Engineers

1. Start with **[`agents.md`](./agents.md)** to understand the development philosophy
2. Browse **[`docs/doctrine/`](./docs/doctrine/)** for domain-specific guidance
3. For Vue 3 UI, see **[The Style](https://github.com/gabu-quest/the-style)** design systems

### Manual Adoption (Alternative to Template)

If you prefer to copy specific files:

```bash
# Core agent doctrine
cp agents.md your-repo/

# Testing doctrine (highly recommended)
cp docs/testing.md your-repo/docs/

# Full doctrine suite
cp -r docs/doctrine your-repo/docs/

# Claude skills
cp -r .claude/skills your-repo/.claude/

# Vue 3 design system (separate repo)
# See https://github.com/gabu-quest/the-style
```

Then update your project's README to reference these standards.

---

## Philosophy

This standard is built on these principles:

1. **Execution over discussion** — Do the work, don't debate time limits
2. **Quality is non-negotiable** — Tests must be meaningful, code must be correct
3. **Clarity over cleverness** — Boring, stable solutions win
4. **Documentation as code** — Tests, types, and artifacts are documentation
5. **AI-human collaboration** — Standards designed for both agents and engineers
6. **Modern tooling** — No legacy tech debt; use actively maintained, boring tools

---

## Standards Hierarchy

When instructions conflict, follow this precedence (highest → lowest):

1. **Current user request** (explicit instruction in this session)
2. **This repository's doctrine** (`agents.md` + doctrine files)
3. **Repository conventions** (existing architecture, patterns)
4. **Everything else** (default habits, tool suggestions)

---

## Technology Standards

### Python
- Python **3.12+**
- **`uv`** for environment and dependency management
- **`pyproject.toml`** as source of truth (PEP 621)
- **Pydantic v2** only
- Modern **FastAPI** patterns
- **pytest** for testing
- **httpx** for HTTP clients

### Node.js/TypeScript
- **Node.js 20 LTS+** with ESM (not CommonJS)
- **TypeScript 5.x** with strict mode
- **pnpm** for package management (or npm/yarn consistently)
- **Vitest** for testing (not Jest)
- **Zod** for runtime validation
- **tsup** or **esbuild** for bundling

### Frontend (Vue)
- **Vue 3** with Composition API + `<script setup>`
- **Pinia** for state management
- **Vitest** for unit/component tests
- **Playwright** for E2E tests (mandatory for all UI features)
- **Naive UI** for component library
- **Phosphor Icons** for iconography

---

## Contributing

This is a living standard. Improvements welcome, but changes must:

1. Maintain the core philosophy of decisive execution and rigorous quality
2. Be LLM-friendly (clear, explicit, normative language)
3. Include rationale (why, not just what)
4. Not add complexity without clear value

---

## License

See [LICENSE](./LICENSE) for details.

---

## Adoption

To adopt this standard in your repository:

1. Copy the relevant doctrine files
2. Link them from your main README
3. Ensure your agents and engineers reference them
4. Adapt as needed for your domain, but maintain the core principles

**Remember:** These are guidelines designed to produce senior-engineer quality results with minimal friction. Follow them unless you have a compelling reason not to.
