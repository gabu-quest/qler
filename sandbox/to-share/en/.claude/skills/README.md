# The Standard - Claude Skills

Skills are the operational interface for The Standard. Each skill is:
- **Capsule-first** in `SKILL.md` (short, actionable)
- Backed by a **distilled** `reference.md` (complete guidance)

Doctrine files remain long-form reference material for humans and onboarding.

## Available Skills

### `commit`
Stage and commit changes with clear, informative messages. Supports optional push.
- `/commit` — commit only
- `/commit push` — commit then push to current branch

### `handoff`
Create or update `HANDOFF.md` for multi-session continuity.

## Usage

Invoke skills directly:
- `/commit` or `/commit push`
- `/handoff`

## Memory

For persistent memory, store important notes in your project's `CLAUDE.md` under a "## Session Notes" section.

## Doctrine Enforcement

The `standard-*` skills have been removed. Their functionality is better served by:

| Was | Now |
|-----|-----|
| `/standard-review` | `code-reviewer` agent (auto-deployed or delegated) |
| `/standard-test` | `test-auditor` agent + testing rules |
| `/standard-design` | Built-in `Plan` agent + `planning.md` rule |

Agents are more powerful than skills because they can use tools, read files, and provide actionable feedback.

## Skill Features

Skills support these frontmatter options:
- `name` — skill name (required)
- `description` — what it does (shown in `/help`)
- `argument-hint` — placeholder shown after skill name (e.g., `"<query>"`)
- `allowed-tools` — restrict which tools the skill can use
- `disable-model-invocation` — prevent automatic invocation by the model

### Using Arguments

Skills receive user input via `$ARGUMENTS`. For example:
- `/commit push` → `$ARGUMENTS` = "push"

## Adding New Skills

1. Create a directory under `.claude/skills/<skill-name>/`
2. Add `SKILL.md` (capsule format, actionable steps)
3. Optionally add `reference.md` (deeper guidance, examples)
4. Update this README with the new skill
5. Keep `SKILL.md` compact and procedural
6. Put depth, rationale, and edge cases in `reference.md`
7. Use a strict Output Format in `SKILL.md`
