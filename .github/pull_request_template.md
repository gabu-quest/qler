# [Title: Conventional Commit Format]

**Type:** [Feature / Bug Fix / Refactor / Docs / Test / Chore]
**Related Issue:** Closes #[issue number]
**SPEC:** [Link to SPEC.md if applicable]
**Tasks:** [Task IDs: T1, T2, T3...]

---

## Summary

**Brief description of what this PR does and why.**

### Key Changes
- Change 1
- Change 2
- Change 3

---

## Implementation Details

### [Component / Module Name]
**Files changed:** `path/to/file.py`, `path/to/another.py`

Brief explanation of what changed and why.

**Key decisions:**
- Decision 1 with rationale
- Decision 2 with rationale

---

## Testing

### Unit Tests
- [ ] Unit tests added/updated
- [ ] Coverage: [X]% (target: 80%+)

**Key tests:**
- `test_feature_does_x` - Verifies [behavior]
- `test_handles_edge_case_y` - Verifies [edge case]

### Integration Tests
- [ ] Integration tests added/updated
- [ ] All integration tests passing

### E2E Tests (if applicable)
- [ ] Playwright tests added/updated (required for frontend features)
- [ ] E2E tests passing

**Test Results:**
```
[Paste test output or link to CI run]
```

---

## Security Review (if applicable)

- [ ] No hardcoded secrets or credentials
- [ ] Input validation added where necessary
- [ ] No SQL injection vulnerabilities
- [ ] No XSS vulnerabilities
- [ ] Authentication/authorization properly enforced
- [ ] Security checklist completed (for security-sensitive changes)

---

## Performance Impact

- [ ] No performance degradation expected
- [ ] Performance tested (if applicable)
- [ ] Benchmarks: [Add results if relevant]

---

## Documentation

- [ ] Code comments added where logic is non-obvious
- [ ] API documentation updated (if public API changed)
- [ ] README updated (if user-facing changes)
- [ ] ADR created (if architectural decision made)
- [ ] CHANGELOG updated (if release-worthy change)

---

## Database Changes

- [ ] Migration created (if schema changes)
- [ ] Migration tested on copy of production data
- [ ] Migration is reversible (downgrade path exists)
- [ ] Rollback plan documented

**Migration:**
```sql
[Paste migration SQL or link to migration file]
```

---

## Breaking Changes

- [ ] No breaking changes
- [ ] Breaking changes documented below with migration guide

**Breaking Changes:**
- [Describe what breaks and how to migrate]

---

## Deployment Notes

**Required steps:**
1. [Step 1]
2. [Step 2]

**Configuration changes:**
- [New env var or config needed]

**Rollback plan:**
- [How to rollback if deployment fails]

---

## Checklist

### Code Quality
- [ ] Code follows style guide
- [ ] No linting errors (ruff, eslint, etc.)
- [ ] Type checking passes (mypy, TypeScript)
- [ ] No commented-out code or debug statements

### Git
- [ ] Commits follow conventional commit format
- [ ] Commit messages are clear and specific
- [ ] Each commit is a logical unit
- [ ] No merge commits (rebased if needed)

### Testing
- [ ] All tests passing locally
- [ ] CI pipeline passing
- [ ] Coverage meets or exceeds target
- [ ] Tests are meaningful (not trivial assertions)

### Documentation
- [ ] All changes documented
- [ ] Examples provided where helpful
- [ ] Reviewer notes added (see below)

---

## Reviewer Notes

**Key areas to review:**
1. [Area 1 that needs careful review]
2. [Area 2 with potential concerns]
3. [Area 3 with tradeoffs made]

**Questions for reviewers:**
- [Question 1 where you need input]
- [Question 2 about approach/design]

**Known limitations:**
- [Limitation 1 with reason]
- [Limitation 2 with future work planned]

---

## Screenshots / Demo (if applicable)

**Before:**
[Screenshot or description of old behavior]

**After:**
[Screenshot or description of new behavior]

---

## Related PRs / Issues

- Related to #[issue]
- Depends on #[PR]
- Blocks #[issue]

---

## Verification Steps

For reviewers to verify this PR:

1. [Step 1 to test locally]
2. [Step 2 to verify behavior]
3. [Step 3 to check edge cases]

---

**Ready for review.** ✅ All checks passing.
