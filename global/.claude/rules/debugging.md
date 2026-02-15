---
description: "Systematic debugging protocol — hypothesize, verify, fix one thing at a time"
---

# Debugging

> Diagnose before you prescribe.

## The Protocol

Every debugging session follows this sequence. No exceptions.

1. **Read the error** — The full error message, stack trace, and context. Not just the last line.
2. **Reproduce** — Confirm you can trigger the error reliably. If you can't reproduce it, you can't verify a fix.
3. **Hypothesize** — Form a specific, testable theory about what's wrong. "The user ID is null because the auth middleware isn't running on this route."
4. **Verify the hypothesis** — Read the relevant code. Add logging. Check state. Confirm or reject your theory BEFORE writing a fix.
5. **Fix** — Make ONE change that addresses the verified root cause.
6. **Verify the fix** — Run the failing test/reproduction. Confirm it passes. Run the full test suite to check for regressions.

## The Three-Strike Rule

If **3 fix attempts fail**, STOP.

Do not try a fourth variation. Instead:

1. Re-read the original error message from scratch
2. Re-read the relevant code without assumptions
3. Question your mental model — what are you assuming that might be wrong?
4. Form a **new hypothesis** (not a variation of the old one)

If you still can't solve it after a new hypothesis cycle, **escalate to the user** with:
- What you tried
- What you observed
- What you think is happening
- What you need to investigate further

## One Change at a Time

**NEVER change multiple things simultaneously.** If you change the query AND the serializer AND the route handler, and it works, you don't know which change fixed it. If it doesn't work, you don't know which change broke something else.

One change → verify → next change.

## Anti-Patterns

| Anti-Pattern | Why It Fails | Correct Approach |
|--------------|-------------|------------------|
| **Shotgun fixing** — trying random changes hoping one sticks | Wastes time, may introduce new bugs | Hypothesize → verify → fix |
| **Ignoring error messages** — "I'll just try this" | The error is telling you what's wrong. Read it. | Read the full error first |
| **"Maybe this will work"** — no hypothesis, just hope | Random walks don't converge | Form a specific theory before changing code |
| **Changing multiple things at once** — bundled fixes | Can't attribute the fix, may mask other bugs | One change at a time |
| **Fixing symptoms, not causes** — wrapping in try/catch | The bug is still there, just hidden | Find and fix the root cause |
| **Copying Stack Overflow without understanding** — paste and pray | May not apply to your context | Understand WHY a solution works before applying |

## When to Escalate

Escalate to the user after **3 failed hypotheses** (not 3 failed random attempts — those don't count). A hypothesis is a specific, testable theory that you verified was wrong. "I tried stuff and it didn't work" is not 3 hypotheses.
