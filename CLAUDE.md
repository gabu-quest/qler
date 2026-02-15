# Claude Context: The Standard Repository

**Version:** 1.2.0 | **Type:** Engineering Doctrine Repository

**This file provides context for AI agents working ON this repository.**

---

## What This Repo Is

**The Standard** is an engineering doctrine repository for AI-assisted software development. It defines opinionated standards for tooling, testing, and code quality.

**This is a documentation repository, not a code repository.** When working here, you're maintaining the doctrine itself.

---

## Repository Structure

```
/
├── CLAUDE.md              ← This file (for contributors)
├── agents.md              ← Core agent doctrine (START HERE)
├── README.md              ← Repo introduction
├── CHANGELOG.md           ← Version history
├── LICENSE
├── docs/                  ← Doctrine documents
│   ├── testing.md         ← Testing doctrine (critical)
│   ├── doctrine/          ← Domain-specific doctrines
│   ├── testing-*.md       ← Framework-specific testing guides
│   └── quick-reference/   ← One-page summaries
├── examples/              ← Reference implementations
├── adoption/              ← Adoption checklist
├── .github/               ← Issue/PR templates
└── global/                ← Personal global Claude config (see below)
    └── .claude/           ← Symlinked to ~/.claude
```

---

## Key Files

| File | Purpose |
|------|---------|
| [`agents.md`](./agents.md) | Core operating rules, workflow, roles |
| [`docs/testing.md`](./docs/testing.md) | Testing doctrine - "A failing test is a gift" |
| [`docs/doctrine/README.md`](./docs/doctrine/README.md) | Index of all domain doctrines |
| [`examples/`](./examples/) | Reference implementations |

---

## Working in This Repository

### Principles

1. **Clarity is paramount** - Docs must be clear for both humans and AI
2. **Normative language** - Use MUST/MUST NOT/SHOULD (RFC 2119)
3. **Opinionated with rationale** - Strong opinions, justified
4. **Internal consistency** - All doctrine docs must align

### Common Tasks

**Adding doctrine:** Create in `docs/doctrine/`, update `docs/doctrine/README.md`, add quick-reference.

**Adding examples:** Put in `examples/` with README explaining what it shows.

See existing files for templates and patterns.

---

## Instruction Hierarchy

When editing this repo:

1. User's explicit request
2. This CLAUDE.md
3. agents.md (our own standards apply to us)
4. Existing patterns in this repo

---

## ADRs (Architectural Decisions)

### ADR-001: Normative Language

All doctrine uses RFC 2119 style (MUST/MUST NOT/SHOULD) to remove ambiguity for AI agents.

### ADR-002: Flat Doctrine Structure

Doctrine lives in `docs/doctrine/` as flat files, not nested. Easy to link and reference.

### ADR-003: Separation of Concerns

The Standard separates two concerns:

1. **Doctrine** (top-level) - Engineering standards for adopters/contributors
2. **Personal Config** (`global/`) - User's Claude agents, rules, skills

The `global/.claude/` directory is symlinked to `~/.claude` for personal use. It contains agents, rules, and skills that apply to ALL projects, not just this repo.

---

## Versioning

Semantic versioning in `CHANGELOG.md`:
- **MAJOR:** Breaking changes to core doctrine
- **MINOR:** New doctrines or significant additions
- **PATCH:** Clarifications, typo fixes

---

## Before Committing

- [ ] Does this align with existing doctrine?
- [ ] Are cross-references updated?
- [ ] Is CHANGELOG.md updated?
- [ ] Would both a human and AI understand this?

---

## About `global/.claude/`

The `global/` directory contains personal Claude configuration:

- `global/.claude/CLAUDE.md` - Global instructions (multilingual, delegation, testing philosophy)
- `global/.claude/agents/` - Reusable agents (code-reviewer, test-runner, etc.)
- `global/.claude/rules/` - Rules that apply to all projects
- `global/.claude/skills/` - Skills (memory, commit, etc.)

This directory is symlinked to `~/.claude` so these configurations apply globally.

**Do not edit `global/` when contributing to The Standard doctrine.** It's personal config, not shareable doctrine.
