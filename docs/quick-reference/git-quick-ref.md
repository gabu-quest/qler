# Quick Reference: Git Doctrine

**Prime Directive:** Git history is part of the product

---

## Branch Naming

| Type | Pattern | Example |
|------|---------|---------|
| Feature | `feat/T1-slug` | `feat/T1-user-auth` |
| Fix | `fix/T2-slug` | `fix/T2-login-timeout` |
| Refactor | `refactor/T3-slug` | `refactor/T3-api-cleanup` |
| Test | `test/T_TEST1-slug` | `test/T_TEST1-e2e-flows` |
| Security | `sec/T_SEC1-slug` | `sec/T_SEC1-xss-fix` |
| Docs | `docs/T_DOC1-slug` | `docs/T_DOC1-api-guide` |

---

## Commit Rules

### Message Format
```
type: imperative summary (under 72 chars)

Why this change was made.
Key context for reviewers.
```

### Types
`feat` | `fix` | `refactor` | `docs` | `test` | `chore` | `perf`

### Good Examples
```
feat: add session CRUD endpoints
fix: enforce auth dependency for HTTP and WS
test: add feature tests for complex query chaining
```

### Bad Examples
```
update code        # What code? What update?
fix bug            # What bug?
WIP                # What's in progress?
changes            # Meaningless
```

---

## Non-Negotiables

| Rule | Reason |
|------|--------|
| **NEVER commit secrets** | `.env`, credentials, API keys, tokens |
| **NEVER force-push shared branches** | Destroys others' work |
| **NEVER rebase after PR exists** | Breaks history for reviewers |
| **NEVER merge to main without permission** | Protect trunk |
| **ALWAYS use imperative mood** | "Add feature" not "Added feature" |
| **ALWAYS commit after logical units** | Not "everything in one commit" |

---

## Merge Philosophy

| Situation | Strategy |
|-----------|----------|
| Feature → main | Squash merge (preferred) |
| Sync main → branch | Merge commit (preserves history) |
| Before PR exists | Rebase allowed |
| After PR exists | No rebase, no force-push |

---

## Conflict Resolution

1. **Understand** both branches' intent
2. **Preserve** valuable work from both sides
3. **Run tests** after resolution
4. **Document** non-obvious choices in commit message

---

## .gitignore (Always Ignore)

```
# Caches
__pycache__/
.pytest_cache/
.ruff_cache/
node_modules/
.vite/

# Test artifacts
playwright-report/
test-results/

# Logs and local config
logs/
.env
*.local
```

---

## Safety Checklist

Before every commit:
- [ ] `git status` - review what's staged
- [ ] `git diff --staged` - review actual changes
- [ ] No secrets in diff
- [ ] No generated files (unless intentional)
- [ ] Message explains "why"

---

**Full doctrine:** [`docs/doctrine/git.md`](../doctrine/git.md)
