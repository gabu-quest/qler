---
name: commit
description: Stage and commit changes with clear, informative messages. Use `/commit` for commit only, `/commit push` to also push.
argument-hint: "[push]"
disable-model-invocation: true
allowed-tools: Bash(git:*)
---

# Commit (Capsule)

Stage and commit changes. See `agents/commit-drafter.md` for full guidelines on message format, staging rules, and push policy.

## Arguments

Check `$ARGUMENTS` for behavior:
- Empty or no "push": Commit only (default)
- Contains "push": Commit then push to current branch

## Steps

1. **Gather context**: `git status`, `git diff --staged`, `git diff`, `git log --oneline -5`
2. **Stage selectively** — related changes together, NEVER stage secrets or generated files
3. **Draft message** — imperative mood, `type: summary` under 72 chars, body explains **why**
4. **Commit** using HEREDOC:
   ```bash
   git commit -m "$(cat <<'EOF'
   type: concise summary

   Why this change was made.
   EOF
   )"
   ```
5. **Push** if `$ARGUMENTS` contains "push" (NEVER to main/master)

## Rules

- **NEVER** commit secrets, use `--force`/`--amend` (unless asked), or skip hooks
- **NO TRAILERS** — no `Co-Authored-By:`, `Signed-off-by:`, or any text after body

## Output

- Committed: <summary>
- Hash: <short hash>
- Push: <pushed to branch / skipped (reason)>
