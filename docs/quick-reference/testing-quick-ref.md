# Quick Reference: Testing Doctrine

**One-page summary of [`testing.md`](../testing.md) — Print this for quick reference**

---

## Prime Directive

**A failing test is a gift.**

Fix the product bug, not the test. Never weaken tests to make them pass.

---

## Definition of Done (Per Feature)

### Backend (Python)
- ✅ Unit tests for key logic
- ✅ Integration tests for boundaries (DB, HTTP, parsing)
- ✅ At least one feature test for non-trivial features

### Frontend (Vue 3)
- ✅ **Playwright tests for EVERY UI feature** (mandatory)
- ✅ Vitest for stores/composables/component logic
- ✅ Use accessibility-friendly selectors (roles/labels)

**No frontend feature ships without Playwright.**

---

## Non-Negotiable Rules

### 1. Determinism
**MUST:**
- Freeze time, seed randomness, use explicit waits

**MUST NOT:**
- Depend on real external services
- Sleep-based timing (except last resort)

### 2. Meaningful Assertions
**Forbidden:** `assert True`, default value checks, huge snapshots

**Good:** Invariants, error behavior, roundtrips

### 3. Never Weaken Tests
If test fails: Fix product OR fix expectation with reason OR update docs + tests

### 4. Test Public API
**MUST:** Test documented public classes/functions/methods

**MUST NOT:** Call private methods, assert internal state

### 5. Don't Bypass What You're Testing
Testing ORM? Use the ORM, not raw SQL (except setup/teardown/verification)

---

## Test Taxonomy

| Type | Purpose | When |
|------|---------|------|
| **Unit** | Small units in isolation | Always, many |
| **Integration** | Subsystems together | Key boundaries |
| **Feature** | Multi-step scenarios | Non-trivial features |
| **E2E** | Full system from outside | Every UI feature |
| **Stress** | Edge cases under pressure | Optional, isolated |

---

## Naming & Documentation

**Use realistic names:** `user`, `account`, `order`, `planet` (not `foo`, `bar`)

**Every test MUST have:** Descriptive name + comment explaining what/why

---

## Mocking & Fixtures

**Mock boundaries only:** Network, external services, filesystem/time

**Prefer "real but local":** SQLite temp DBs, temp dirs, ASGI in-process

---

## Python (pytest)

### Tools
- pytest + fixtures
- pytest-mock / unittest.mock
- httpx TestClient / ASGITransport for FastAPI
- respx for httpx mocking

### Markers
```python
@pytest.mark.unit
@pytest.mark.integration
@pytest.mark.feature
@pytest.mark.e2e
@pytest.mark.slow
```

---

## Frontend (Vitest + Playwright)

### Vitest
Test stores, composables, component logic

### Playwright (MANDATORY)
**MUST:**
- Exercise real UI (clicks, typing, navigation)
- Use `getByRole`, `getByLabel`, `data-testid`
- Avoid brittle CSS selectors
- Capture artifacts on failure
- No `waitForTimeout` - use auto-wait + expectations

---

## CI Strategy

### Fast Suite (Every PR)
- Unit + key integration
- Small Playwright smoke suite

### Full Suite (Nightly)
- Full integration + stress
- Expanded Playwright

### Failure Policy
Failing test blocks merge unless explicitly overridden with reason + issue link

---

## LLM Agent Protocol

1. **Identify behaviors** - What must be true?
2. **Choose layers** - Unit/integration/feature/E2E
3. **Test public interface** - Not private methods
4. **Write as documentation** - Realistic names, clear comments
5. **Never fudge** - Fix product, not tests
6. **Keep stable** - No flakes

---

## Red Flags

🚩 Test passes without asserting anything
🚩 Test uses sleep instead of waiting
🚩 Test fails inconsistently (flaky)
🚩 Test calls private methods
🚩 Test uses `foo`/`bar` names
🚩 Test marked `skip` without issue link
🚩 Coverage <80% without justification

---

## Good Examples

### Unit Test
```python
def test_session_validates_and_updates_activity():
    """Session validation should update last_activity."""
    service = SessionService(db)
    session = service.create_session(user_id="alice")
    validated = service.validate_session(session.token)
    assert validated.last_activity > session.created_at
```

### Playwright Test
```typescript
test('user can login and access protected resource', async ({ page }) => {
  await page.goto('/login');
  await page.getByLabel('Username').fill('alice');
  await page.getByRole('button', { name: 'Login' }).click();
  await expect(page).toHaveURL('/dashboard');
});
```

---

**Full Docs:** [testing.md](../testing.md) | **Agents:** [agents.md](../../agents.md) | **CI:** [ci.md](../doctrine/ci.md)
