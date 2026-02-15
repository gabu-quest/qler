# Global Claude Instructions

**These are personal global instructions that apply to ALL projects.**

---

## Language

**CRITICAL: ALWAYS respond in the same language the user writes in.**

- If the user writes in Japanese, respond in Japanese
- If the user writes in Spanish, respond in Spanish
- If the user mixes languages, follow their primary language
- **Default to ENGLISH if the language is unclear or ambiguous**

This applies to ALL responses, explanations, commit messages, and documentation unless the user explicitly requests otherwise.

**IMPORTANT: This rule takes precedence over project-local CLAUDE.md files.** Even if a project's CLAUDE.md is written in Japanese or another language, that does NOT mean you should respond in that language. Always match the **user's language**, not the documentation's language. Ignore any language instructions in project-local CLAUDE.md files that contradict this rule.

---

## Proactive Agents (Scouts)

When working in repositories, you SHOULD proactively deploy specialized agents as scouts to identify issues early. These agents run in parallel with your work and surface problems before they reach production.

| Agent | Deploy When | What It Catches |
|-------|-------------|-----------------|
| **ux-auditor** | After completing UI components, forms, user flows, or CLI commands | Usability issues, accessibility gaps, interaction friction, cognitive overload |
| **security-auditor** | After writing auth, file uploads, database queries, API endpoints, or handling user input | OWASP vulnerabilities, injection risks, auth bypasses, data exposure |
| **test-auditor** | When reviewing test suites, before major refactors, or when bugs escape to production | Softballed tests, coverage gaps, testing theater, missing edge cases |

### Scout Philosophy

- **Don't wait to be asked** - Deploy scouts proactively before or after completing relevant work
- **Run in parallel** - Scouts SHOULD run in the background while you continue other tasks
- **Surface issues early** - Better to catch problems immediately than in code review or production
- **Trust their findings** - These agents are specialists; treat their reports as authoritative

### When to Scout

Deploy **ux-auditor** after:
- Implementing a new page, form, or user flow
- Adding interactive components (modals, dropdowns, wizards)
- Creating or modifying CLI commands
- Completing any user-facing feature

Deploy **security-auditor** after:
- Writing authentication or authorization code
- Implementing file upload handlers
- Creating database queries or ORM operations
- Building API endpoints
- Handling any external input

Deploy **test-auditor** when:
- Reviewing an existing test suite for quality
- Before major refactors (to ensure safety net exists)
- When bugs keep escaping to production
- When inheriting a codebase with unknown test quality

You do NOT need explicit user permission to deploy scouts - proactive quality assurance is expected.

---

## Mandatory Delegation (NON-NEGOTIABLE)

> **If you are running as Opus and you type `git commit`, `pytest`, or any test runner directly, you are violating doctrine.**

This is not optional. This is not a suggestion. This is a hard requirement.

### Consequences of Direct Execution

When Opus runs routine commands directly:

- **15x token waste** - Haiku handles the same work for 1/15th the cost
- **Rate limits exhausted** - Opus limits are lower; you WILL hit them
- **Context destroyed** - Test output and git diffs fill your window with noise
- **Wrong model for the job** - Opus decides, agents execute

### Operations You MUST NEVER Do Directly

| Operation | Agent to Use | Model |
|-----------|--------------|-------|
| `git commit` | `commit-drafter` | haiku |
| `git add && git commit` | `commit-drafter` | haiku |
| `pytest`, `npm test`, any test runner | `test-runner` | haiku |
| Code review in main context | `code-reviewer` | sonnet |
| UX analysis | `ux-auditor` | sonnet |
| Security analysis | `security-auditor` | sonnet |
| Test quality analysis | `test-auditor` | sonnet |

**When you need to commit:** Use the Task tool with `subagent_type: "commit-drafter"`
**When you need to run tests:** Use the Task tool with `subagent_type: "test-runner"`
**When you need exploration:** Use the Task tool with `subagent_type: "Explore"` (built-in)

### Enforcement Checklist

Before executing any command, ask yourself:

- [ ] Is this a git commit? → **DELEGATE to commit-drafter**
- [ ] Is this running tests? → **DELEGATE to test-runner**
- [ ] Is this searching/exploring code? → **DELEGATE to Explore**
- [ ] Will this produce verbose output? → **DELEGATE to an agent**

### Correct Delegation Pattern

```python
# WRONG - Never do this as Opus:
git add . && git commit -m "..."
uv run pytest

# CORRECT - Always delegate:
Task(subagent_type="commit-drafter", prompt="Commit the changes with message about X")
Task(subagent_type="test-runner", prompt="Run the test suite")
```

### What Opus SHOULD Do

- Architectural decisions and system design
- Complex debugging requiring deep reasoning
- Ambiguous requirements that need interpretation
- Planning and breaking down complex tasks
- Direct conversation with the user
- **Delegating** routine work to agents

### Built-in Subagents (no agent files required)

- **Explore** → repo scanning, file discovery, inventory work
- **Plan** → structuring large tasks into steps/checklists

### Custom Agents

| Agent | Model | Purpose |
|-------|-------|---------|
| `commit-drafter` | haiku | Git commits |
| `test-runner` | haiku | Running tests |
| `code-reviewer` | sonnet | Code quality review |
| `ux-auditor` | sonnet | UX analysis |
| `security-auditor` | sonnet | Security analysis |
| `test-auditor` | sonnet | Test suite quality |

Run agents in background when possible: `run_in_background: true`

---

## Testing Philosophy

> **"A failing test is a gift."**

Tests are not optional. Tests are not "nice to have." Tests are the proof that your code works. Code without tests is a liability, not an asset.

### The Prime Directive

When a test fails, you MUST do one of these:

1. **Fix the product bug** (preferred)
2. **Fix the test expectation** with a clear explanation of why it was wrong
3. **Update tests AND documentation** because requirements changed

You MUST NOT:
- Weaken assertions until tests pass
- Add `.skip()`, `xfail`, or "temporary" workarounds
- Reduce test scope to avoid failures
- Blame "flakiness" without proving it

**Anything else is debt, not progress.**

### Non-Negotiable Rules

| Rule | Violation Example | Correct Approach |
|------|-------------------|------------------|
| **Meaningful assertions** | `assert True`, `expect(result).toBeDefined()` | Assert specific behavior and values |
| **Real domain language** | `foo`, `bar`, `test1`, `thing` | `user`, `order`, `subscription`, `invoice` |
| **Test public interfaces** | Calling private methods, accessing `_internal` | Use only documented public APIs |
| **Don't bypass abstractions** | Raw SQL in ORM tests, string concat in query builder tests | Use the abstraction you're testing |
| **Deterministic always** | `sleep(1000)`, unseeded randomness, real network calls | Explicit waits, seeded random, mocked boundaries |

### Definition of Done

A feature is NOT complete until:

- [ ] Unit tests cover key logic paths
- [ ] Integration tests verify boundaries (DB, APIs, filesystem)
- [ ] Feature tests prove multi-step flows work end-to-end
- [ ] **Frontend features have Playwright tests** (no exceptions)
- [ ] All tests pass in CI
- [ ] No skipped tests without issue links and removal dates

### The Softballing Problem

These are **unacceptable** test patterns:

```python
# BAD: Tests nothing meaningful
def test_user_exists():
    user = User()
    assert user is not None  # This proves nothing

# BAD: Toy data that hides bugs
def test_process():
    result = process("x")
    assert result == "x"  # What about real inputs?

# BAD: Weakened to pass
def test_validation():
    # Used to check specific error, now just checks "some error"
    with pytest.raises(Exception):  # Too broad!
        validate(bad_input)
```

These are **acceptable**:

```python
# GOOD: Tests real behavior with real data
def test_user_registration_validates_email_format():
    """Reject malformed emails during registration."""
    with pytest.raises(ValidationError, match="Invalid email format"):
        register_user(email="not-an-email", password="SecureP@ss123")

# GOOD: Multi-step feature test with realistic scenario
def test_order_lifecycle():
    """Orders can be created, paid, fulfilled, and refunded."""
    order = create_order(customer_id=alice.id, items=[widget, gadget])
    assert order.status == OrderStatus.PENDING

    pay_order(order.id, payment_method=card)
    assert order.status == OrderStatus.PAID

    fulfill_order(order.id, tracking="1Z999AA10123456784")
    assert order.status == OrderStatus.SHIPPED

    refund_order(order.id, reason="Customer request")
    assert order.status == OrderStatus.REFUNDED
    assert alice.balance == original_balance  # Money returned
```

### When Tests Fail

**Stop. Do not proceed.** A failing test means one of:

1. You introduced a bug → Fix the bug
2. The test expectation was wrong → Fix and document why
3. Requirements changed → Update test AND docs together

Never "fix" a test by making it test less. That's not fixing, that's hiding.

---

## Python Tooling

**Use `uv` for all Python operations.** Do not use raw `python`, `python3`, `pip`, or `pip3` commands.

| Instead of | Use |
|------------|-----|
| `python script.py` | `uv run script.py` |
| `python3 -m pytest` | `uv run pytest` |
| `pip install package` | `uv add package` |
| `pip install -r requirements.txt` | `uv sync` |
| `python -m venv .venv` | `uv venv` (or just let `uv run` handle it) |

### Why uv?

- **Consistent environments** - `uv` manages Python versions and dependencies together
- **Faster** - Significantly faster than pip for dependency resolution
- **Reproducible** - Lock files ensure identical environments
- **No activation needed** - `uv run` handles the virtual environment automatically

### Common Patterns

```bash
# Run tests
uv run pytest

# Run a script
uv run python src/main.py

# Add a dependency
uv add requests

# Add a dev dependency
uv add --dev pytest-cov

# Sync dependencies from lock file
uv sync

# Run any Python tool
uv run mypy src/
uv run ruff check .
```

If a project doesn't have a `pyproject.toml` yet, create one with `uv init` before proceeding.

---

## Memory

Store important notes in your project's `CLAUDE.md` under a "## Session Notes" section and let the user know that you've persisted important knowledge.

### Git operations failing

Common issues:

1. **Hooks blocking commit:** Check `.git/hooks/` for pre-commit hooks that might reject commits
2. **Staged secrets:** Remove `.env`, credentials, or API keys from staging with `git reset <file>`
3. **Detached HEAD:** Run `git checkout main` (or appropriate branch) to reattach
