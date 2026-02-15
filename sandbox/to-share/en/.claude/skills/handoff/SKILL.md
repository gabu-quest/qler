---
name: handoff
description: Create a handoff document when ending a session or when asked for /handoff.
disable-model-invocation: true
---

# Handoff (Capsule)

Create or update `HANDOFF.md` so the next agent can continue without rework.

## When to Use
- Session ends with unfinished work
- Ownership shifts (Dev → Test → Security)
- Complex task spans multiple sessions

## Steps
1. Identify scope and current status (done, in progress, blocked).
2. Capture key decisions, constraints, and open questions.
3. List concrete next actions with file references and verification steps.
4. Record test status and any failures.
5. Save to `HANDOFF.md` in the repo root unless the repo uses a different standard name.

## Output Format
- Handoff File: `HANDOFF.md` (created/updated)
- Summary: <1–3 sentences>
- Key Decisions: <bullets>
- Risks/Blockers: <bullets>
- Next Actions: <bullets with file refs>
- Testing Status: <passing/failing/not run + notes>

## Assumptions & Overrides
- If repo policy differs, explicitly call out the mismatch and proceed with the safest reasonable interpretation.
- If a repo already uses a different handoff filename, follow repo convention and note it.

## Additional Reference
- For full rationale and edge cases, see `reference.md`.
