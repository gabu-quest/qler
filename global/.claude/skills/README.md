# The Standard - Claude Skills (Global)

Skills are the operational interface for The Standard. Each skill is:
- **Capsule-first** in `SKILL.md` (short, actionable)
- Backed by a **distilled** `reference.md` (complete guidance)

## Prerequisites

| Skill | Requires | Installation |
|-------|----------|--------------|
| `commit` | `git` | Usually pre-installed |

## Available Skills

### `commit`
Stage and commit changes with clear, informative messages.
- `/commit` — commit only
- `/commit push` — commit then push to current branch

### `handoff`
Create or update `HANDOFF.md` for multi-session continuity.

### `project`
Store project-local preferences in `CLAUDE.local.md` (gitignored).
- `/project <preference>` — store the specified preference

## Usage

Invoke skills directly:
- `/commit` or `/commit push`
- `/handoff`
- `/project <preference>`

## Memory

The custom `mem`/`remember`/`recall` skills have been removed in favor of Claude Code's built-in auto-memory (`~/.claude/projects/*/memory/`). Auto-memory works with zero external dependencies and persists across sessions automatically.

## Doctrine Enforcement

The `standard-*` skills have been removed. Their functionality is better served by agents:

| Was | Now |
|-----|-----|
| `/standard-review` | `code-reviewer` agent |
| `/standard-test` | `test-auditor` agent |
| `/standard-design` | Built-in `Plan` agent + `planning.md` rule |

Agents are more powerful because they can use tools, read files, and provide actionable feedback.

## Adding New Skills

1. Create a directory under `.claude/skills/<skill-name>/`
2. Add `SKILL.md` (capsule format, actionable steps)
3. Optionally add `reference.md` (deeper guidance, examples)
4. Update this README with the new skill
