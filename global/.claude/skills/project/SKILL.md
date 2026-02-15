---
name: project
description: Store project-local preferences in CLAUDE.local.md (gitignored). Usage: /project <preference or note>
argument-hint: "<preference>"
disable-model-invocation: false
allowed-tools: Read, Write, Edit, Bash
---

# Project Memory (Local)

Store personal project-specific preferences in `CLAUDE.local.md` (gitignored).

Use this for:
- Sandbox URLs for this project
- Preferred test data or credentials
- Project-specific workflow preferences
- Personal reminders about this codebase

**User's input:** `$ARGUMENTS`

## Steps

1. **Check if CLAUDE.local.md exists** in the current directory
2. **Ensure .gitignore includes CLAUDE.local.md**
   - Check if `.gitignore` exists
   - Add `CLAUDE.local.md` if not already present
3. **Add the content to CLAUDE.local.md**
   - If file doesn't exist, create with header
   - Append the user's input with timestamp
   - Use markdown formatting for readability

## File Format

```markdown
# Project Memory (Local)

**This file is personal and gitignored. Store project-specific preferences here.**

---

## <Timestamp>

<User's content>

---
```

## Output Format

**Success:**
- Added to: `CLAUDE.local.md`
- Content: <one-line summary>
- Gitignored: <yes/no>

## Rules

- MUST ensure CLAUDE.local.md is gitignored
- MUST use markdown formatting
- MUST add timestamps for each entry
- SHOULD preserve existing content (append, don't overwrite)
- Content should be searchable and well-formatted
