# CI Doctrine
## Reliable, fast signal with strict quality gates (LLM-friendly)

This document defines CI expectations: what must run, when it must run, and how to keep CI trustworthy.

Uses **MUST / MUST NOT / SHOULD** normatively.

---

## 1. Prime directive

**CI must be a reliable judge.**  
A green CI must mean “safe to proceed.” A red CI must be actionable.

---

## 2. Suite tiers (fast vs full)

### 2.1 Fast suite (every PR)
The fast suite MUST:
- run unit tests
- run key integration tests (critical paths)
- run a small Playwright smoke suite (critical UI flows if frontend exists)
- run type checks/builds as appropriate (e.g., `vue-tsc`, `mypy/pyright` if configured)

Goal: fast feedback without sacrificing correctness.

### 2.2 Full suite (nightly or on demand)
The full suite SHOULD run:
- the full integration suite
- stress/robustness tests
- expanded Playwright suite (more flows, more browsers if needed)

---

## 3. Quality gates (default)

A typical CI pipeline SHOULD include:
- formatting/linting (if repo uses it)
- type checking
- unit tests
- integration tests
- build steps (backend package build, frontend build)
- E2E smoke (Playwright)

If the repo uses path filters, they MUST be correct and conservative (avoid skipping required work).

---

## 4. Playwright policy

If the repo has a frontend, CI MUST:
- install Playwright browsers (cached)
- run Playwright tests in at least one browser
- capture artifacts on failure:
  - trace
  - screenshot
  - video (optional)

Artifacts MUST be easy to locate in CI outputs.

You MUST NOT:
- rely on `waitForTimeout` sleeps as synchronization
- write brittle selectors that frequently break

---

## 5. Caching policy

CI SHOULD cache:
- `uv` downloads/build artifacts
- `pnpm` store
- Playwright browser installs

Caches MUST be keyed correctly:
- include lockfiles (`uv.lock`, `pnpm-lock.yaml`)
- include OS / node/python versions when relevant

---

## 6. Flake policy (strict)

Flaky tests are bugs.

If a test is flaky, you MUST:
- reproduce it locally or in CI logs,
- fix the underlying cause,
- or quarantine it with:
  - an issue link,
  - a removal deadline,
  - and a plan to fix.

You MUST NOT:
- “just retry CI” as the solution
- weaken assertions to avoid flakes

---

## 7. Security checks (recommended baseline)

CI SHOULD include:
- dependency scanning (as appropriate)
- secret scanning (if available)
- basic SAST for Python/JS (repo-dependent)

If the repo runs ZAP baseline scans, they SHOULD run on scheduled jobs and/or release candidates.

---

## 8. Release readiness

Before release (or before merge to main), CI SHOULD run the full suite or a release candidate suite that includes:
- full tests
- build artifacts verification
- E2E flows

---
