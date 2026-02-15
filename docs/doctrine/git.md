# Git Doctrine
## Branching, commits, merges, and `.gitignore` (LLM-friendly)

This document defines Git behavior for agents and humans.
It complements `agents.md` and is stricter where Git history matters.

Uses **MUST / MUST NOT / SHOULD** normatively.

---

## 1. Prime directive

**Git history is part of the product.**  
Readable history reduces bugs, enables rollback, and makes future work cheaper.

---

## 2. Branching

### 2.1 Default rule
For non-trivial changes, you MUST work on a branch.

You MUST NOT merge into `main`/`master` unless the user explicitly requests it.

### 2.2 Naming
Branches SHOULD include intent and a task ID:

- `feat/T1-<short-slug>`
- `fix/T2-<short-slug>`
- `refactor/T3-<short-slug>`
- `test/T_TEST1-<short-slug>`
- `sec/T_SEC1-<short-slug>`
- `docs/T_DOC1-<short-slug>`

### 2.3 Sub-branches (allowed)
If a TODO list naturally decomposes, you MAY create sub-branches off your own branches to keep the Git graph narratively useful.

---

## 3. Commit discipline

### 3.1 What a good commit contains
Each commit MUST be:
- coherent (one idea)
- reviewable
- buildable or quickly recoverable

Commits SHOULD be small enough to review comfortably.

### 3.2 Commit frequency
You MUST commit after each logical unit:
- feature slice
- refactor slice
- test slice
- docs slice

Avoid “everything in one commit” unless the repo is tiny.

### 3.3 Commit messages
Commit messages MUST be:
- imperative mood
- specific
- not vague

Examples:
- `feat: add session CRUD endpoints`
- `fix: enforce auth dependency for HTTP and WS`
- `test: add feature tests for complex query chaining`
- `docs: add dev workflow and troubleshooting guide`

---

## 4. Merge philosophy

### 4.1 Merge vs squash vs rebase
Default behavior:
- keep meaningful commit history on feature branches
- preferred merge strategy: **squash merge** when integrating to `main`/`master`
- merge commits are allowed when policy-compliant (e.g., syncing `main` into a branch without rewriting history)

Rebase rules (policy):
- **Rebase permitted only before branch is shared / before PR exists.**
- **After PR exists / after branch is shared: no rebase, no force-push unless explicitly requested/approved.**

You MUST NOT rewrite history (force push / rebase+force) unless explicitly requested.

### 4.2 Conflict resolution
When resolving conflicts, you MUST:
- understand the intent of each branch,
- preserve valuable work from both sides,
- avoid “winning” by deleting the other side’s code silently.

You SHOULD:
- run tests after conflict resolution,
- document non-obvious resolution choices in the merge commit message or `handoff.md`.

---

## 5. Generated artifacts & build outputs

You MUST NOT commit large generated outputs unless the repo convention requires it.

Allowed examples (repo-dependent):
- lockfiles (`uv.lock`, `pnpm-lock.yaml`)
- small generated type files if they are stable and expected
- compiled frontend dist only if the repo policy explicitly tracks it

If unsure:
- follow existing repo patterns,
- document the decision in an ADR or design note if it affects future workflow.

---

## 6. `.gitignore` policy (non-negotiable)

If the user explicitly asks you to create or modify files inside a git-ignored directory, you MAY do so, but you MUST NOT:
- move the content into tracked, non-ignored locations unless explicitly asked,
- change `.gitignore` or force-add ignored files unless explicitly asked,
- bypass ignore rules by embedding ignored content into tracked files/directories.

Recommended ignored content (if consistent with repo policy):
- caches (`__pycache__`, `.pytest_cache`, `.ruff_cache`)
- node artifacts (`node_modules`, `.vite`, `.turbo`)
- test artifacts (`playwright-report`, `test-results`, traces/videos)
- logs (`logs/`)

---

## 7. Safety and hygiene

You SHOULD:
- avoid committing secrets; use environment configuration,
- run `git status` before each commit,
- keep diffs readable.

If secrets are suspected to have been committed:
- stop immediately and follow repo’s incident policy (or notify user).

---
