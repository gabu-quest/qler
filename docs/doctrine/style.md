# Style Guide
## Code layout, naming, patterns, and consistency (LLM-friendly)

This document defines how code should look and how changes should be structured.
It complements `agents.md` and the Testing Doctrine.

Uses **MUST / MUST NOT / SHOULD** normatively.

---

## 1. Prime directive

**Consistency beats cleverness.**  
Prefer boring, clear patterns that match the repo.

---

## 2. Project layout

You MUST:
- follow the existing directory structure unless an approved DESIGN changes it
- keep modules cohesive (single responsibility)
- avoid dumping “utils” with unrelated functions

You SHOULD:
- keep public interfaces near the top-level of a package
- keep internal helpers in clearly named internal modules

---

## 3. Python style

### 3.1 Types and clarity
- Use type hints broadly.
- Prefer explicit models and clear data shapes.
- Avoid magic behavior without documentation.

### 3.2 Error handling
- Raise/return errors with consistent envelopes/codes.
- Avoid swallowing exceptions.
- Add context to errors.

### 3.3 Logging
- Log useful context, not noise.
- Never log secrets or tokens.
- Prefer structured logging if repo supports it.

### 3.4 Modern tooling
- Use `uv` and `pyproject.toml`.
- Prefer Pydantic v2 patterns.
- Prefer FastAPI DI patterns for HTTP/WS auth and shared concerns.

---

## 4. API design conventions (when applicable)

You SHOULD:
- use consistent request/response schemas
- centralize shared models
- define a consistent error envelope:
  - `{ "code": "...", "message": "...", "details": ... }` (or repo standard)

You MUST:
- keep schemas stable where public
- version breaking changes

---

## 5. Vue / UI style

You MUST:
- use Vue 3 Composition API + `<script setup>`
- use Pinia for state management
- keep UI behaviors accessible by default
- prefer Naive UI conventions if adopted

You SHOULD:
- centralize icons mapping
- use consistent toast/notification patterns
- use consistent modal/confirm patterns for destructive actions

---

## 6. Naming rules

You MUST:
- use realistic domain names in examples/tests (`user`, `order`, `item`, etc.)
- avoid single-letter identifiers except trivial loops
- avoid `foo/bar/baz`

---

## 7. Documentation expectations

When behavior changes, you MUST:
- update docs and/or README where users/devs will notice
- keep doc examples consistent with current code
- record meaningful decisions as ADRs when necessary

---

## 8. Refactoring rules

You MUST:
- keep refactors behavior-preserving unless explicitly changing behavior
- avoid mixing refactors with features unless requested
- keep tests green or restore quickly

---
