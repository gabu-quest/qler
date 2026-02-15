# Handoff Reference (Distilled)

## Prime Intent
A handoff prevents rework, regressions, and context loss. It is the contract with the next agent.

## When a Handoff Is Required
- You will not finish the task in this session.
- Another agent/role is expected to continue.
- You hit a blocker needing external input.
- You are about to make a risky change and need a safe checkpoint.

## Required Sections (Minimum Bar)
- **Context**: What is being built and why it matters.
- **Current State**: What’s done, what’s in progress, what’s next.
- **Key Decisions**: Non-obvious choices and rationale.
- **Risks/Blockers**: Unknowns, failing tests, dependency issues.
- **Testing Status**: Passing/failing/not run with details.
- **Next Actions**: Concrete, file-specific steps.

## Quality Rules
- Keep it short but complete. No walls of logs.
- Write for someone who never saw the thread.
- Prefer explicit file references over vague statements.

## Git Policy (Option 2)
- Rebase permitted only before branch is shared / before PR exists.
- After PR exists / after branch is shared: no rebase, no force-push unless explicitly requested/approved.
- Preferred merge strategy: squash merge.
- Merge commits are allowed when policy-compliant; do not auto-fail for their presence.

## Template (Concise)
```
# Handoff: <topic>

Status: In Progress | Blocked | Ready for Review

## Context
- Goal:
- Why it matters:

## Current State
- Done:
- In progress:
- Next:

## Key Decisions
- Decision → Rationale

## Risks/Blockers
- Issue → Impact

## Testing Status
- Unit:
- Integration:
- E2E:

## Next Actions
- [ ] Step with file refs
```
