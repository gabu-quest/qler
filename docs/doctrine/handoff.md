# Handoff Protocol
## Multi-session continuity for humans, LLM agents, and orchestrators

This document defines how to record state so work can continue across:
- multiple AI agent sessions,
- restarts by an orchestrator,
- human handoffs,
- branch/sub-branch work.

Uses **MUST / MUST NOT / SHOULD** normatively.

---

## 1. Prime directive

**If you stop, someone else must be able to start.**  
A good handoff reduces rework, prevents regressions, and enables unattended execution.

---

## 2. Standard files

### 2.1 `plan.md`
- the execution plan (ordered tasks) derived from SPEC/TASKS/DESIGN
- stable task IDs, acceptance criteria, dependencies

### 2.2 `handoff.md`
- the single source of truth for “where we are right now”

If the repo already uses different filenames, follow repo convention, but keep the structure.

---

## 3. When to update `handoff.md`

You MUST update `handoff.md` whenever:
- you complete a coherent chunk and are about to stop,
- you are about to switch branches/sub-branches,
- you made a non-trivial decision or discovered a risk,
- you are about to hand control to another role/agent,
- an orchestrator is likely to restart your session.

You SHOULD keep `handoff.md` concise; do not paste huge logs.

---

## 4. Required `handoff.md` structure

`handoff.md` MUST include these sections:

1. **Context**
   - one paragraph: what project, what goal, what constraints
2. **Current branch**
   - branch name
3. **Last known good state**
   - last commit hash (or “uncommitted changes”)
   - how to run tests/builds relevant to current work
4. **What changed**
   - bullet summary (high level)
5. **Completed tasks**
   - checklist of task IDs marked done
6. **Remaining tasks**
   - ordered checklist (most important first)
7. **Risks / open questions**
   - anything that could bite the next agent
8. **Next steps**
   - exact commands and/or exact next prompt to continue

Optional but recommended:
- links to relevant files
- notes about CI failures
- migration notes

---

## 5. Signals for orchestrators (optional)

If an orchestrator is driving the session, you MAY emit exact tokens on their own line:

- `[PLAN_COMPLETE]` — planning artifacts ready
- `[HANDOFF_READY]` — safe boundary reached; handoff updated
- `[PLAN_DONE]` — everything complete

Do not emit these tokens unless they are true.

---

## 6. Handoff quality rules

You MUST:
- avoid long narrative explanations; prefer checklists and commands
- record assumptions explicitly
- record decisions that affect future work (or link to ADR)

You MUST NOT:
- claim tasks are complete when they are not
- rely on private knowledge not written down

---

## 7. Template

```markdown
# Handoff

## Context
<1 paragraph>

## Current branch
- branch: <name>

## Last known good state
- commit: <hash> (or “working tree dirty”)
- run: <commands to run tests/build>

## What changed
- ...
- ...

## Completed tasks
- [x] T1 ...
- [x] T2 ...

## Remaining tasks
- [ ] T3 ...
- [ ] T4 ...

## Risks / open questions
- ...
- ...

## Next steps
1. <exact command>
2. <exact command>
3. <exact next prompt>
```
---
