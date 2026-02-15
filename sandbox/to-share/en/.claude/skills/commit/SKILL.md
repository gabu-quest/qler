---
name: commit
description: Stage and commit changes with clear, informative messages. Use `/commit` for commit only, `/commit push` to also push.
argument-hint: "[push]"
disable-model-invocation: true
allowed-tools: Bash(git:*)
---

# Commit (Capsule)

Stage and commit changes with clear, informative messages that explain **why**, not just what.

## Arguments

Check `$ARGUMENTS` for behavior:
- Empty or no "push": Commit only (default)
- Contains "push": Commit then push to current branch

**NEVER push to main/master** - warn and skip the push if on these branches.

## Steps

1. **Gather context**
   ```bash
   git status           # See what's changed
   git diff --staged    # Already staged changes
   git diff             # Unstaged changes
   git log --oneline -5 # Match existing style
   ```

2. **Stage selectively**
   - Stage related changes together
   - **NEVER** stage secrets (.env, credentials, API keys, tokens)
   - **NEVER** stage generated files unless intentional
   - Prefer specific files over `git add -A`

3. **Draft commit message**
   - Use imperative mood ("Add feature" not "Added feature")
   - First line: type + concise summary (under 72 chars)
   - Body: Explain **why** this change was made
   - Types: `feat`, `fix`, `refactor`, `docs`, `test`, `chore`, `perf`

4. **Commit using HEREDOC format**
   ```bash
   git commit -m "$(cat <<'EOF'
   type: concise summary

   Why this change was made and important context.
   EOF
   )"
   ```

5. **Push (if requested)**
   - Only if `$ARGUMENTS` contains "push"
   - Check current branch first
   - **NEVER** push to main/master - warn and skip

## Git Doctrine Rules

- **NEVER** commit secrets
- **NEVER** use `--force` or `--amend` (unless explicitly asked)
- **NEVER** skip hooks (`--no-verify`)
- **NEVER** push to main automatically
- **DO** explain why, not just what (the diff shows what)
- **DO** keep first line under 72 characters

## Output Format

- Committed: <one-line summary>
- Hash: <short hash>
- Push: <pushed to branch / skipped (reason)>
- Warnings: <any issues encountered>

## Assumptions & Overrides

- If conversation context suggests what the changes accomplish, use that for the "why"
- If repo has specific commit conventions, follow them over generic rules
