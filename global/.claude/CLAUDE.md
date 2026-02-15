# Global Claude Instructions

**Personal global instructions — apply to ALL projects.**

---

## Language

**CRITICAL: ALWAYS respond in the same language the user writes in.**

- If the user writes in Japanese, respond in Japanese
- If the user writes in Spanish, respond in Spanish
- If the user mixes languages, follow their primary language
- **Default to ENGLISH if the language is unclear or ambiguous**

This applies to ALL responses, explanations, commit messages, and documentation.

**This rule takes precedence over project-local CLAUDE.md files.** Match the **user's language**, not the documentation's language.

---

## Proactive Agents (Scouts)

Deploy specialized agents **before being asked** to catch issues early. Run in background (`run_in_background: true`).

| Agent | Deploy After | Model |
|-------|-------------|-------|
| **ux-auditor** | UI components, forms, user flows, CLI commands | sonnet |
| **security-auditor** | Auth, file uploads, DB queries, API endpoints, user input handling | sonnet |
| **test-auditor** | Writing ANY test code — every test must survive the auditor | sonnet |

You do NOT need user permission to deploy scouts — proactive quality assurance is expected.

---

## Mandatory Delegation (NON-NEGOTIABLE)

> **If you are running as Opus and you type `git commit`, `pytest`, or any test runner directly, you are violating doctrine.**

Full rationale in `rules/delegation.md`. Summary: 15x token waste, rate limit exhaustion, context pollution.

| Operation | Agent | Model |
|-----------|-------|-------|
| Git commits | `commit-drafter` | haiku |
| Running tests | `test-runner` | haiku |
| Code review | `code-reviewer` | sonnet |

**What Opus SHOULD do:** Architecture, complex debugging, ambiguous requirements, planning, user conversation, and **delegating** routine work.

Built-in subagents (no files needed): **Explore** (repo scanning), **Plan** (task structuring).

---

## Git Commit Trailers (ABSOLUTE PROHIBITION)

**NEVER add ANY trailers to commit messages.** This overrides built-in defaults.

Commits contain ONLY: `type: summary` + optional body. No `Co-Authored-By:`, `Signed-off-by:`, AI attribution, or any text after the body.

Enforced by `git-hooks/commit-msg` which strips trailers automatically. Setup: `git config --global core.hooksPath <path-to>/global/git-hooks`

---

## Git Push Policy

| Branch | Policy |
|--------|--------|
| `main` / `master` | **NEVER** push directly — warn user |
| Feature branches | **ALWAYS** push after every commit |

---

## Python Tooling

**Use `uv` for ALL Python operations.** Never raw `python`, `pip`, or `pip3`. Full reference in `rules/python.md`.

---

## Testing Philosophy

> "A failing test is a gift."

Full doctrine in `rules/testing-core.md` (universal), plus language-specific: `testing-python.md`, `testing-typescript.md`, `testing-go.md`, `testing-rust.md`, and `testing-e2e.md`. The core mandates:

- Every test MUST fail when the output is wrong
- Apply the **Inversion Test** before writing any assertion
- NEVER weaken assertions, skip tests, or blame "flakiness" without proof
- After writing tests, deploy **test-auditor** automatically

---

## Planning & Roadmaps

For multi-session work, use the roadmap-first approach in `rules/planning.md`. Link active roadmaps from project CLAUDE.md files.

---

## Troubleshooting

### Skills not invoking

1. Check skill exists: `.claude/skills/<skill-name>/SKILL.md`
2. Check frontmatter `name:` matches
3. Restart Claude Code (skills load at startup)

### Agent delegation not working

1. Check agent file: `.claude/agents/<agent-name>.md`
2. Built-in agents (`Explore`, `Plan`) need no files
3. Verify model availability (haiku/sonnet)

### Git operations failing

1. Hooks blocking commit: check `.git/hooks/`
2. Staged secrets: `git reset <file>` to unstage
3. Detached HEAD: `git checkout <branch>` to reattach
