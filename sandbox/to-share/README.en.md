# The Standard - Shareable Configuration

Pre-configured Claude Code setup implementing The Standard's engineering doctrine.

## What's Included

```
.claude/
├── CLAUDE.md           # Main instructions (delegation, testing, tooling)
├── agents/             # Specialized agents for delegation
│   ├── code-reviewer.md
│   ├── commit-drafter.md
│   ├── security-auditor.md
│   ├── test-auditor.md
│   ├── test-runner.md
│   └── ux-auditor.md
├── rules/              # Context-aware rules (load by file type)
│   ├── delegation.md
│   ├── planning.md
│   ├── security.md
│   ├── testing-core.md
│   ├── testing-python.md
│   └── testing-typescript.md
└── skills/             # User-invocable slash commands
    ├── commit/
    └── handoff/
```

## Installation

Copy the `.claude/` directory to your project root:

```bash
cp -r en/.claude /path/to/your/project/
```

Or for global installation (applies to all projects):

```bash
cp -r en/.claude ~/.claude
```

## Key Principles

### Mandatory Delegation
Opus delegates routine work to specialized agents:
- `commit-drafter` for git commits (runs as Haiku)
- `test-runner` for running tests (runs as Haiku)
- `code-reviewer` for code review (runs as Sonnet)

### Testing Doctrine
"A failing test is a gift." Tests must be:
- Deterministic (no sleeps, no real network calls)
- Meaningful (assert specific values, not just types)
- Boundary-mocked (mock at HTTP/DB/filesystem, not internal logic)

### Proactive Scouts
Specialized agents run proactively to catch issues:
- `security-auditor` after auth code, file uploads, API endpoints
- `ux-auditor` after UI components and user flows
- `test-auditor` when reviewing test quality

## Languages

- [English](./en/) - Primary
- [日本語](./ja/) - Japanese

## Customization

1. Copy to your project
2. Modify `CLAUDE.md` for project-specific instructions
3. Add project-specific rules to `.claude/rules/`
4. Remove agents you don't need

## Requirements

- Claude Code CLI
- Git (for commit skill)
