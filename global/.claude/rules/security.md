---
description: "Input validation, dependency policy, and supply chain security rules"
---

# Security

Every external input is hostile until validated.

## Input & Code Safety

- No secrets in code (use env vars)
- Parameterized queries always (no string concat SQL)
- Validate at boundaries (Zod, Pydantic)
- No `eval()`, no `shell=True` with user input
- Sanitize file paths to prevent traversal

## Dependency Policy

Before adding any dependency (`uv add`, `npm install`, etc.), ask:

1. **Can we do this in <30 lines?** — If yes, write it yourself. A dependency for a single function is tech debt, not productivity.
2. **Is it maintained?** — Check last commit date, open issues, bus factor. Abandoned packages are liabilities.
3. **What's the dependency tree?** — A "small" package with 50 transitive dependencies is not small. Run `npm ls` or `uv tree` to check.
4. **Are there known vulnerabilities?** — Check `npm audit` / `pip audit` / GitHub security advisories before adding.

### Supply Chain Rules

| Rule | Why |
|------|-----|
| **Pin versions** | `"react": "18.2.0"` not `"^18.2.0"` — prevents surprise upgrades |
| **Use lockfiles** | `package-lock.json` / `uv.lock` MUST be committed |
| **Audit new deps** | Run `npm audit` / `pip audit` after adding any dependency |
| **Prefer well-known packages** | A package with 10 weekly downloads is a risk, not an opportunity |
| **Review major upgrades** | Major version bumps can change APIs and introduce vulnerabilities |

### Never Add a Dependency For

- String manipulation (padding, trimming, case conversion)
- Simple date formatting (unless you need full i18n)
- UUID generation (use `crypto.randomUUID()` / `uuid4()`)
- Deep object cloning (use `structuredClone()` / `copy.deepcopy()`)
- Anything the standard library already does
