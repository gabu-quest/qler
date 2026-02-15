# Testing Doctrine
## Modern, Strict, LLM-Friendly Rules for Writing Tests (Perfect Edition)

This document is the authoritative testing doctrine for repositories that adopt it.
It is written for **LLM agents** and humans who want tests that are:

- **strict** (fail loudly and correctly)
- **realistic** (exercise real behavior, not toy assertions)
- **deterministic** (non-flaky, reproducible)
- **documentary** (tests explain “what” and “why”)
- **future-proof** (patterns survive tool/library evolution)

This document uses **MUST / MUST NOT / SHOULD** in the normative standards sense.

---

## 0. Prime directive

**A failing test is a gift.**

You MUST treat failing tests as signals to fix product behavior, not excuses to weaken tests.

You MUST NOT:
- “fudge” expectations to make a test pass,
- silence failures with `skip`, `xfail`, or “temporary defaults,”
- reduce assertions until green,
- blame flakiness without proving it.

When tests fail, you MUST choose one of these legitimate outcomes:

1. **Fix the product bug** (preferred).
2. **Fix the test** because the expectation was wrong (explain why; update docs if behavior is intentionally different).
3. **Update tests and documentation together** because the requirement changed.

Anything else is debt, not progress.

---

## 1. Test taxonomy (what kinds of tests exist)

A healthy repo uses a deliberate mix:

### 1.1 Unit tests
- **Purpose:** Prove small units of behavior in isolation.
- **Traits:** Fast, focused, numerous.
- **Allowed mocking:** Internal collaborators as needed, but prefer real implementations where cheap.

### 1.2 Integration tests
- **Purpose:** Prove subsystems work together (DB + query builder, API layer + service layer, filesystem + parsing).
- **Traits:** Fewer than unit tests, more realistic, still deterministic.
- **Allowed mocking:** Only at true boundaries (external network, cloud services).

### 1.3 Feature tests (example-as-test)
- **Purpose:** Prove a user-visible or API-visible feature works end-to-end with multiple steps.
- **Traits:** Longer, narrative, “documentation you can run.”
- **Style:** Reads like a real-world scenario (not `foo/bar`).

### 1.4 End-to-end tests (E2E)
- **Purpose:** Prove the full system behaves correctly from the outside.
- **Frontend requirement:** All frontend features MUST have Playwright coverage.
- **Traits:** Fewest in number, highest value, most stable.

### 1.5 Stress / robustness tests
- **Purpose:** Prove complex logic and edge behaviors survive real pressure (large inputs, repeated operations, complex chains).
- **Traits:** Can be slower; should be isolated and optionally run in a “full” suite.

---

## 2. Definition of done (testing requirements per feature)

A feature is not complete until all applicable layers are satisfied.

### 2.1 Backend features (Python)
You MUST include:
- Unit tests for key logic.
- Integration tests for boundaries you introduced (DB queries, HTTP routes, parsing, filesystem behavior).
- At least one **feature test** for non-trivial features (multi-step scenario).

### 2.2 Frontend features (Vue 3)
For every user-visible frontend feature, you MUST include:
- **Vitest** tests for stores/composables/component logic (unit/integration).
- **Playwright** tests that exercise the actual UI behavior:
  - click the real buttons
  - fill the real inputs
  - verify UI state and resulting side effects
  - prefer accessibility-friendly selectors (roles/labels) where feasible

**No frontend feature ships without Playwright.**

---

## 3. Non-negotiable quality rules

### 3.1 Determinism (flakiness is a bug)
Tests MUST be deterministic and reproducible.

You MUST NOT:
- rely on sleep-based timing for synchronization (except as a last resort with justification),
- depend on real external services or the public internet,
- depend on non-seeded randomness,
- assert against unstable ordering unless ordering is part of the contract.

You SHOULD:
- freeze time for time-dependent logic,
- seed randomness and log the seed for repro,
- use explicit waits / auto-waits (Playwright) instead of sleeps.

### 3.2 Assertions must be meaningful
Tests MUST assert behavior, not implementation trivia.

Forbidden examples:
- `assert True`
- checking a default value that is not meaningful
- snapshotting huge outputs without targeted assertions

Good examples:
- invariants (“query builder emits the expected SQL + bindings”)
- behavior under errors (“raises DomainError with code X”)
- roundtrips (“create → read → update → list → delete”)

### 3.3 Never weaken tests to get green
If a test fails:
- fix the product,
- or fix the test expectation with a clear reason,
- or update docs + tests for intended behavior changes.

No “just make it pass.”

### 3.4 Test the public interface, not private internals (library rule)
**This is non-negotiable.**

If you are testing a library, you MUST test it through its **public API**:

- Use documented public classes, functions, methods, and supported configuration.
- Treat private methods/fields (leading underscore, internal modules, undocumented hooks) as off-limits.

You MUST NOT:
- call private methods to “make testing easier”,
- assert internal state that is not part of the public contract,
- couple tests to private module structure.

Exceptions are allowed only if:
- the user explicitly asks for internal tests, or
- you are writing tests for a private/internal module where the “public API” is that module.
In that case, you MUST document the exception in the test file header.

### 3.5 Do not bypass the thing you are testing (no “raw SQL in ORM tests”)
If you are testing an abstraction (ORM, query builder, client SDK, API wrapper), you MUST use that abstraction in tests.

Example:
- When testing an ORM, you MUST NOT write raw SQL queries as the primary interaction.
- When testing a query builder, you MUST NOT build the query with string concatenation.

Raw SQL (or other lower-level bypasses) is allowed only for:
- **setup/teardown** (creating fixtures efficiently),
- **verification of external state** when the public API does not provide a way to introspect it,
- **security/injection tests** that specifically need lower-level assertions.

If you use a bypass, you MUST:
- keep it minimal,
- explain why it was necessary,
- never let it replace the core test flow.

### 3.6 Snapshot discipline (anti-abuse rule)
Snapshots are easy to generate and easy to abuse.

You MUST NOT:
- snapshot huge outputs (large JSON blobs, long HTML, entire rendered pages) as a substitute for real assertions,
- accept snapshots blindly without review.

You MAY use snapshots when:
- the output is small and stable,
- the snapshot is a targeted artifact (e.g., a small component render),
- you still assert key semantic facts in addition to the snapshot.

Prefer explicit, readable assertions over snapshots.

---

## 4. Naming, documentation, and realism

### 4.1 Use real language, not toy names
Tests MUST use realistic domain language:
- `user`, `account`, `subscription`, `item`, `order`, `star_system`, `planet`, `shipment`, etc.
Not `foo`, `bar`, `baz`, `thing`, or single letters (except trivial loops).

### 4.2 Tests are documentation
Every non-trivial test MUST include:
- a descriptive name, and
- a short comment/docstring explaining **what** is being tested and **why it matters**.

### 4.3 Prefer clarity over cleverness
Write tests that a new engineer can understand without reading the implementation.

---

## 5. Mocking & fixtures doctrine

### 5.1 Mock boundaries, not internals
You SHOULD mock only at true boundaries:
- network calls (HTTP clients)
- external services
- filesystem/time if needed for determinism

You MUST NOT mock internal methods just to avoid designing testable code.

### 5.2 Prefer “real but local” integrations
Prefer:
- SQLite temp DBs or per-test DB files (explicit schema setup),
- temp directories for filesystem tests,
- ASGI in-process testing for FastAPI routes.

### 5.3 Fixtures must be explicit and composable
Fixtures MUST:
- be named clearly (`user_alice`, `db_with_schema`, `api_client_authenticated`),
- avoid hidden global state,
- tear down cleanly.

### 5.4 Modern mocking tools
- Python: `pytest` fixtures + `pytest-mock` (or `unittest.mock`), `respx` for `httpx` when appropriate.
- JS: `vi.fn()`, `vi.spyOn()`, MSW where appropriate for client tests.

---

## 6. “Painful” tests (stress + complexity) — the right way

“Painful” means:
- painful for bugs,
- not painful for maintainers.

### 6.1 Complex-chain proofs for complex builders
If the code implements complex query building (chaining, composition, nested logic), you MUST include tests that:
- build long, realistic chains,
- mix filters/joins/order/limits/subqueries where relevant,
- validate both generated output (SQL + bindings) and semantics (returned rows) where feasible.

You MUST include at least one “ridiculous chain” test when the API supports chaining:
- long chains should prove associativity/precedence
- parameter binding must be correct and safe
- empty lists, nullables, and special characters must not corrupt output

### 6.2 Multi-step feature tests
At least some tests SHOULD be “feature tests” that read like an executable guide:
- create an entity,
- mutate it,
- query it,
- validate results and invariants,
- delete it,
- validate it is gone.

### 6.3 Stress tests and suite separation
You SHOULD separate slower/stress tests into an optional suite:
- fast suite runs on every PR
- full suite runs nightly or on demand

Do not make every PR take forever.

### 6.4 Data volume guidance
Stress tests SHOULD use data sizes that are:
- meaningfully above “toy” size,
- large enough to exercise algorithmic behavior,
- still bounded so CI remains reliable.

Avoid pathological sizes that turn CI into a benchmarking lab unless explicitly requested.

---

## 7. Concurrency and async correctness (when applicable)

If the product code is concurrent or async, tests MUST include at least one scenario that exercises:
- concurrent operations (e.g., parallel queries, concurrent requests),
- ordering/race behavior where relevant,
- cancellation/timeouts where relevant.

You MUST make these tests deterministic:
- avoid sleeps,
- prefer controlled schedulers/hooks,
- ensure clear, stable expectations.

---

## 8. Python testing doctrine (pytest-first)

### 8.1 Tools and defaults
- Use `pytest`.
- Use fixtures extensively.
- Use markers for suite selection:
  - `@pytest.mark.unit`
  - `@pytest.mark.integration`
  - `@pytest.mark.feature`
  - `@pytest.mark.e2e`
  - `@pytest.mark.slow`

### 8.2 FastAPI / ASGI testing (preferred pattern)
Prefer in-process ASGI testing:
- Use `httpx.AsyncClient` with `ASGITransport` (or FastAPI `TestClient` where appropriate) for route tests.
- Avoid starting a real server unless E2E requires it.

### 8.3 Database testing
Prefer ephemeral DBs:
- SQLite in a temporary file (or `:memory:` when safe), with explicit schema setup.
- Ensure isolation:
  - transaction rollback, or
  - per-test DB file, or
  - explicit per-test schema setup.

### 8.4 Property-based tests (optional)
If you use Hypothesis, you MUST:
- constrain generators to realistic domains,
- keep tests deterministic (Hypothesis already persists examples; still log context),
- avoid flaky strategies.

---

## 9. Frontend testing doctrine (Vitest + Playwright)

### 9.1 Vitest (unit + integration)
Vitest MUST cover:
- Pinia stores
- composables
- component logic that is meaningful without a browser

You SHOULD:
- prefer Testing Library style patterns (interact as user, not as DOM surgeon),
- keep tests stable and readable.

### 9.2 Playwright (mandatory for frontend features)
All frontend features MUST have Playwright tests that:
- exercise real UI behavior (clicks, typing, navigation),
- use stable selectors:
  - prefer `getByRole`, `getByLabel`, and accessible names,
  - use `data-testid` only when necessary and keep it consistent,
- avoid brittle CSS selectors.

You MUST:
- capture artifacts on failure (trace/screenshot/video) as configured by the repo,
- avoid `waitForTimeout`; rely on auto-wait + explicit expectations.

### 9.3 E2E environment
E2E SHOULD run against:
- a local dev/test server started in CI, or
- a deterministic stubbed backend designed for E2E.

E2E MUST NOT depend on:
- the public internet,
- non-deterministic external services.

---

## 10. CI strategy (fast vs full)

### 10.1 Fast suite (every PR)
Run:
- unit + key integration tests,
- a small Playwright smoke suite that hits critical paths.

### 10.2 Full suite (nightly or on demand)
Run:
- full integration suite,
- stress tests,
- expanded Playwright suite (more flows/browsers if needed).

### 10.3 Failure policy
A failing test MUST block merges until resolved unless the user explicitly overrides with a documented exception.

If `xfail`/`skip` is unavoidable:
- It MUST include a reason and issue link.
- It MUST include a planned removal milestone/date.
- It MUST not conceal regressions.

---

## 11. LLM-agent operating protocol (how to behave while writing tests)

When asked to add or update tests, the agent MUST:

1. **Identify behaviors and invariants**
   - What must always be true?
   - What are the edge cases?
   - What are the failure modes?

2. **Choose the right test layers**
   - Unit for local logic,
   - Integration for real wiring,
   - Feature tests for multi-step flows,
   - Playwright for frontend behavior.

3. **Test the public interface**
   - Do not call private methods.
   - Do not bypass abstractions (no raw SQL in ORM tests unless strictly justified).

4. **Write tests as documentation**
   - Realistic names,
   - Comments explaining why,
   - Clear assertions and helpful failure messages.

5. **Never fudge**
   - Fix product bugs rather than weakening tests.

6. **Keep suites stable**
   - Eliminate flakiness,
   - Avoid sleeps,
   - Produce useful artifacts on failures (especially in E2E).

---

## 12. Framework-Specific Testing Guides

The core principles above apply universally to all testing. For framework-specific patterns and advanced techniques, load the relevant guide below.

**Loading strategy:**
1. **Always read this file first** (`testing.md`) — Core principles apply everywhere
2. **Then load your framework-specific guide** — Focuses ONLY on framework patterns
3. Framework guides **assume** you've read core principles
4. Framework guides **extend, never contradict** core doctrine

### 12.1 Available Framework Guides

| Framework/Tool | Guide | When to Load | Prime Directive |
|----------------|-------|--------------|-----------------|
| **FastAPI** | [testing-fastapi.md](./testing-fastapi.md) | Python web APIs with FastAPI | Dependency override > mocking |
| **Hypothesis** | [testing-hypothesis.md](./testing-hypothesis.md) | Property-based testing in Python | Pure functions, clear properties, actionable failures |
| **Playwright (Advanced)** | [testing-playwright.md](./testing-playwright.md) | Complex E2E scenarios, accessibility | Accessibility selectors first |

### 12.2 Context Efficiency

**Rationale for separate guides:**
- **Context window optimization** — Agents working on Go don't need FastAPI patterns
- **Modular adoption** — Projects copy only what they use
- **Independent evolution** — Framework patterns evolve without changing core doctrine

**Token estimates:**
- Core `testing.md`: ~2,900 tokens (universal principles)
- `testing-fastapi.md`: ~800 tokens (FastAPI-specific patterns)
- `testing-hypothesis.md`: ~600 tokens (property-based testing)
- `testing-playwright.md`: ~700 tokens (advanced E2E patterns)

An agent working on FastAPI loads ~3,700 tokens (core + FastAPI), not all 5,000+ tokens.

### 12.3 Future Framework Guides

As The Standard grows, framework guides will be added for:
- **Go** (`testing-go.md`) — Table-driven tests, test packages
- **Rust** (`testing-rust.md`) — Compiler-assisted testing, property tests with proptest
- **React Testing Library** (`testing-react.md`) — Component testing patterns
- **Vitest (Advanced)** (`testing-vitest.md`) — Vue 3 + Pinia testing patterns

Contributions welcome following the pattern established by existing framework guides.

---

## Appendix A: Templates (recommended)

### A.1 Feature test structure (backend)
- Arrange: create realistic entities
- Act: run the real feature flow (multiple steps)
- Assert: validate results and invariants
- Cleanup: confirm deletion/rollback behavior

### A.2 Feature test structure (frontend / Playwright)
- Navigate
- Interact (click/type)
- Expect UI changes
- Expect persistence/side effects
- Verify refresh/reload behavior if relevant

---
