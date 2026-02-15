# Design Doctrine
## How we plan, design, and make decisions (LLM-friendly)

This document defines when design artifacts are required, how to write them, and how to keep architecture coherent over time.

This doctrine uses **MUST / MUST NOT / SHOULD** in the normative standards sense.

---

## 1. Prime directive

**Change the system intentionally.**  
If you are going to change behavior, APIs, architecture, data shapes, or security posture, you MUST do it with an explicit plan and explicit acceptance criteria.

---

## 2. When a DESIGN is required

You MUST write or update a DESIGN document (`docs/design/<topic>.md`) when ANY of the following are true:

- You are adding a new subsystem, service, module boundary, or major dependency.
- You are changing a **public API** (Python public functions/classes, exported JS modules, HTTP API schemas, CLI flags).
- You are performing a migration (framework upgrade, ORM swap, auth change, router split, monorepo restructure).
- You are introducing concurrency/async coordination (workers, queues, WebSockets, background jobs).
- You are changing security-sensitive behavior (auth, sessions, file uploads/downloads, secrets handling).
- You are making a refactor that touches many files or changes core architecture (not just local cleanup).
- You anticipate non-trivial tradeoffs and the outcome is not obviously “standard.”

If none of those are true, a DESIGN is optional. A short plan in the PR description or `handoff.md` may be sufficient.

---

## 3. When an ADR is required

You MUST write an ADR (`docs/adr/NNN-<title>.md`) when you make a decision that:

- is likely to be revisited,
- constrains future choices,
- affects multiple parts of the system,
- has meaningful alternatives,
- impacts safety/security or long-term maintainability.

ADRs should be short and crisp; they record **why**, not every detail of implementation.

---

## 4. Planning phases (Kiro-style)

For large work, the default phases are:

1. **SPEC** — what we are building and why
2. **TASKS** — what we will do (IDs + acceptance criteria)
3. **DESIGN** — how we will do it (tradeoffs, risks)
4. **PLAN** — the execution order (chunked, end-to-end slices)
5. **IMPLEMENTATION** — code + tests + docs
6. **VERIFY** — run suites, validate acceptance criteria
7. **HANDOFF** — record current state and next steps (if not done)

Agents MUST avoid “plan-only forever.” Planning is a gateway to execution.

---

## 5. The DESIGN template (required sections)

A DESIGN document MUST contain these sections:

1. **Context**
   - What exists today? What problem are we solving?
2. **Goals**
   - What outcomes must be true?
3. **Non-goals**
   - What we explicitly are not doing (prevents scope creep).
4. **Requirements**
   - Functional requirements (what the system must do).
   - Non-functional requirements (performance, reliability, security, UX).
5. **Constraints & assumptions**
   - Known constraints (tech, time, compatibility, policy).
6. **Current architecture**
   - Key modules/flows today.
7. **Proposed solution**
   - New/changed components, boundaries, and data flow.
8. **Public interfaces**
   - Explicit contracts:
     - API schemas (request/response models)
     - function signatures
     - events/messages
9. **Data model & persistence**
   - Tables, migrations, indexes, cache keys, file layouts.
10. **Error handling**
   - Error envelope format, codes, and user-visible messages.
11. **Observability**
   - Logs, metrics, traces (what we need to debug production).
12. **Security considerations**
   - Threat model notes; what we did to reduce risk.
13. **Testing strategy**
   - What tests prove correctness (unit/integration/feature/E2E).
14. **Rollout / migration plan**
   - How to deploy safely; backwards compatibility; toggles.
15. **Alternatives considered**
   - What else we could do and why we didn’t.
16. **Risks & mitigations**
   - What could go wrong and how we reduce it.
17. **Acceptance criteria**
   - Concrete, testable definition of done.

If any section is “not applicable,” you MUST say why.

---

## 6. Public interface stability rules

If the work produces a library, SDK, ORM, or reusable module:

- Tests MUST target the **public API** (see testing doctrine).
- Public methods and schemas MUST be documented (docstrings or docs).
- Breaking changes MUST be explicit:
  - bump version (if applicable)
  - update changelog
  - include migration notes

You MUST NOT rely on private methods to implement public behavior without documenting the contract boundaries.

---

## 7. Decision discipline (anti-chaos rules)

You MUST NOT:
- introduce a new pattern in one place and a different pattern elsewhere without justification,
- mix two competing architectures mid-migration without a plan,
- “sneak in” architecture changes during unrelated feature work.

When you discover an architectural problem during feature work:
- create a task (`T_ARCH*`) and propose a refactor plan,
- do not half-fix it ad-hoc unless the user explicitly asks.

---

## 8. Review checklist

Before implementation begins (or before merge), confirm:

- The SPEC’s goals/non-goals are clear.
- TASKS have IDs and acceptance criteria.
- DESIGN states boundaries and contracts.
- Testing strategy includes at least one end-to-end proof for user-facing behavior.
- Security considerations are not hand-waved.
- Rollout plan is realistic and safe.

---
