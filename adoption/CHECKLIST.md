# Adoption Checklist

Use this checklist when adopting The Standard in your repository.

---

## Light Mode: Minimal Adoption

**For small projects, scripts, or when you want to start simple.**

Only two files needed:

1. **Copy [`agents.md`](../agents.md)** to your repo root
2. **Copy [`docs/testing.md`](../docs/testing.md)** to `docs/`

That's it. You now have:
- Clear execution rules for AI agents
- Non-negotiable testing standards
- Git workflow guidance (section 5 of agents.md)

**When to upgrade to full adoption:**
- Adding CI/CD → read `docs/doctrine/ci.md`
- Security-sensitive work → add `docs/doctrine/security.md`
- Multi-session AI work → add `docs/doctrine/handoff.md`
- Frontend UI → see [The Style](https://github.com/gabu-quest/the-style) repo

**Skip everything below** if light mode is sufficient for your needs.

---

## Full Adoption (8 Phases)

For teams, larger projects, or when you want the complete standard.

---

## Phase 1: Setup & Planning

### Understand The Standard
- [ ] Read [`README.md`](../README.md) to understand the philosophy
- [ ] Read [`agents.md`](../agents.md) in full (30-45 minutes)
- [ ] Skim [`docs/testing.md`](../docs/testing.md) to understand testing requirements
- [ ] Review [`docs/doctrine/`](../docs/doctrine/) to see what's covered
- [ ] Check [`examples/`](../examples/) for sample artifacts

### Assess Your Project
- [ ] Identify which doctrines apply to your project
  - Backend (Python)? → Need agents.md, testing.md, git.md, ci.md, security.md
  - Frontend (Vue)? → Need agents.md, testing.md, git.md, design-system/
  - Full-stack? → Need everything
  - Other tech stack? → Adapt Python/Vue sections to your stack
- [ ] Evaluate current gaps (missing tests? no CI?)
- [ ] Decide: full adoption or gradual migration?

### Get Team Buy-In
- [ ] Share The Standard with team
- [ ] Discuss philosophy: execution over discussion, quality is non-negotiable
- [ ] Get agreement on which doctrines to adopt
- [ ] Identify concerns or blockers
- [ ] Set expectations: standards will evolve with use

---

## Phase 2: Initial Setup

### Copy Core Files
- [ ] Copy [`agents.md`](../agents.md) to your repo root
- [ ] Copy [`docs/testing.md`](../docs/testing.md) to your `docs/` directory
- [ ] Copy [`docs/doctrine/`](../docs/doctrine/) if adopting all doctrines
- [ ] Copy [`CLAUDE.md`](../CLAUDE.md) if you work with AI agents frequently
- [ ] Copy [`CHANGELOG.md`](../CHANGELOG.md) as template (update for your repo)

### Adapt to Your Stack
If not using Python 3.12+ / Vue 3:
- [ ] Update `agents.md` section 6 (Modern Stack Enforcement) for your tech
- [ ] Update testing doctrine for your test frameworks
- [ ] Keep philosophy intact, adapt implementation details

### Repository Structure
- [ ] Create `docs/` directory if it doesn't exist
- [ ] Create `docs/specs/` for SPEC documents
- [ ] Create `docs/design/` for DESIGN documents
- [ ] Create `docs/adr/` for Architecture Decision Records
- [ ] Add `.gitignore` entries if needed
- [ ] Add `.claude/settings.local.example.json` and ignore `.claude/settings.local.json` (personal)

---

## Phase 3: CI/CD Integration

### Testing Infrastructure
- [ ] Ensure test runner is configured (pytest, Vitest, etc.)
- [ ] Set up fast vs full test suites (per `docs/doctrine/ci.md`)
- [ ] Configure Playwright for E2E tests (if frontend project)
- [ ] Add test coverage reporting
- [ ] Set up pre-commit hooks (optional but recommended)

### CI Pipeline
- [ ] Create `.github/workflows/` (or equivalent) directory
- [ ] Set up CI pipeline to run tests on every PR
- [ ] Configure fast suite for PRs, full suite for nightly/merges
- [ ] Add linting/formatting checks (ruff for Python, eslint for JS)
- [ ] Set up failure notifications

### Quality Gates
- [ ] Enforce test coverage minimum (suggest 80%+)
- [ ] Require all tests passing before merge
- [ ] Align Git policy: squash-merge preferred; no rebase/force-push after PR exists unless explicitly approved
- [ ] Add branch protection rules
- [ ] Consider required code reviews

---

## Phase 4: GitHub Templates

### Issue Templates
- [ ] Copy examples from `.github/ISSUE_TEMPLATE/` in this repo
- [ ] Adapt for your project (add labels, assignees, etc.)
- [ ] Create templates for: bug, feature, security, documentation

### PR Template
- [ ] Copy example from `.github/pull_request_template.md`
- [ ] Customize checklist for your workflow
- [ ] Ensure it references SPEC/TASKS/DESIGN where applicable

---

## Phase 5: Design System (If Applicable)

### For Vue 3 Projects Using Naive UI

See **[The Style](https://github.com/gabu-quest/the-style)** - a separate repository with complete design systems.

- [ ] Choose variant (Goshuin or Cyberpunk)
- [ ] Follow installation guide in The Style repo
- [ ] Install dependencies: `naive-ui`, `@phosphor-icons/vue`
- [ ] Copy design system files to your project
- [ ] Customize tokens as needed

### For Other Stacks
- [ ] Consider token-based design approach
- [ ] Maintain single source of truth principle
- [ ] Keep accessibility-first philosophy

---

## Phase 6: Documentation

### Update Your README
- [ ] Add section referencing The Standard
- [ ] Link to `agents.md` and `docs/testing.md`
- [ ] Explain which doctrines you've adopted
- [ ] Add "Contributing" section referencing the standard

### Create ADRs for Major Decisions
- [ ] Use example ADRs from [`examples/adr/`](../examples/adr/)
- [ ] Document: database choice, framework choice, architecture patterns
- [ ] Keep ADRs in `docs/adr/` with numbering: `001-topic.md`

### Developer Onboarding
- [ ] Update onboarding docs to reference The Standard
- [ ] Point new developers to `agents.md` and `testing.md`
- [ ] Include links in new hire checklist

---

## Phase 7: Training & Rollout

### Train AI Agents
- [ ] Update AI agent prompts/instructions to reference `agents.md`
- [ ] Point agents to `CLAUDE.md` for repo-specific context
- [ ] Test with small tasks to verify agent behavior
- [ ] Iterate on custom instructions if needed

### Train Human Developers
- [ ] Hold team meeting to walk through The Standard
- [ ] Do code review of sample PR following the standard
- [ ] Pair on first SPEC → TASKS → DESIGN → PLAN workflow
- [ ] Encourage questions and feedback

### Gradual Rollout
- [ ] Start with new features (easier than retrofitting)
- [ ] Require standard for new code, encourage for refactors
- [ ] Review after 2-4 weeks: what's working? what's not?
- [ ] Adjust your adoption of the standard based on feedback

---

## Phase 8: Maintenance

### Regular Reviews
- [ ] Review adherence monthly (are PRs following the standard?)
- [ ] Collect feedback from team and agents
- [ ] Update your copy of The Standard quarterly (check for new versions)
- [ ] Adapt doctrines as your project evolves

### Track Improvements
- [ ] Measure: test coverage, PR review time, bug escape rate
- [ ] Document wins (faster reviews, fewer regressions, clearer history)
- [ ] Share successes with team to reinforce adoption

### Contribute Back
- [ ] Found something unclear? Open issue in The Standard repo
- [ ] Adapted doctrine for new stack? Share with community
- [ ] Improved examples? Submit PR to The Standard

---

## Troubleshooting

### "Our team finds this too strict"
- **Start gradual:** Adopt testing.md first, add others as team is ready
- **Customize:** The Standard is opinionated, but you can adapt
- **Explain why:** Share rationale, not just rules

### "Our AI agent isn't following the standard"
- **Check instructions:** Ensure agents.md is in their context
- **Be explicit:** Reference specific sections in requests
- **Iterate:** Update custom instructions based on behavior

### "We use different tech stack"
- **Keep philosophy:** Execution over discussion, quality is non-negotiable
- **Adapt tooling:** Replace Python/Vue with your stack equivalents
- **Document changes:** Track your adaptations for consistency

### "Too much ceremony for small projects"
- **Scale down:** Small projects might only need agents.md + testing.md
- **Skip artifacts:** SPEC/TASKS/DESIGN are for non-trivial work
- **Keep core principles:** Even small projects benefit from good tests and git history

---

## Success Criteria

You'll know adoption is successful when:

- ✅ PRs are consistently well-tested and documented
- ✅ Git history is readable and reviewable
- ✅ AI agents execute work end-to-end without hand-holding
- ✅ Test suites are green and meaningful
- ✅ Team spends less time bikeshedding, more time shipping
- ✅ Regressions are rare (tests catch bugs before production)
- ✅ New developers ramp up quickly (clear standards)

---

## Next Steps

After completing this checklist:

1. **Start small** - Pick one new feature to implement following The Standard
2. **Review together** - Team reviews the work against the standard
3. **Iterate** - Adjust your adoption based on what works
4. **Spread the word** - Share successes with other teams

---

## Resources

- [The Standard Repository](https://github.com/gabu-quest/the-standard)
- [Quick Reference Cards](../docs/quick-reference/)
- [Examples](../examples/)
- [Issue Tracker](https://github.com/gabu-quest/the-standard/issues) (for questions/improvements)

---

**Good luck!** Remember: The Standard exists to make shipping great software easier, not harder.
If it feels like friction, adjust it. If it feels like clarity, share it.
