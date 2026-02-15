# Delegation (MANDATORY)

**THIS IS NOT OPTIONAL.** When running as Opus, you MUST delegate routine work to agents. Failure to delegate is a violation of operating doctrine.

## Mandatory Delegation

You MUST use the Task tool to delegate these operations:

| Operation | Agent | Model | NEVER do directly |
|-----------|-------|-------|-------------------|
| Git commits | `commit-drafter` | haiku | `git commit` |
| Running tests | `test-runner` | haiku | `pytest`, `npm test`, etc. |
| Code review | `code-reviewer` | sonnet | Inline review in main context |

**NEVER run these commands directly as Opus.** Always spawn an agent.

## Violations

Direct execution as Opus causes:

- **Wasted tokens** - 15x cost increase over Haiku
- **Rate limit exhaustion** - Opus limits are lower
- **Context pollution** - Verbose output bloats your window
- **Role confusion** - Opus decides, agents execute

## Built-in Subagents (no agent files required)

- **Explore** → repo scanning, file discovery, inventory reports
- **Plan** → structuring large tasks into steps/checklists

## Enforcement

If you find yourself typing `git commit`, `git add`, `pytest`, or similar execution commands:

1. **STOP**
2. Use the Task tool with the appropriate agent
3. Run in background when possible (`run_in_background: true`)

This is not a suggestion. This is doctrine.
