# Security Baseline
## Secure-by-default engineering doctrine (ZAP/Burp-aware, pen-test resistant)

This document defines minimum security expectations.
It is written for LLM agents and humans building web apps, APIs, CLIs, and libraries.

Uses **MUST / MUST NOT / SHOULD** normatively.

**Threat model orientation:** assume motivated attackers, automated scanners, and fuzzers.
The system must be robust against OWASP ZAP, Burp Suite testing, and real-world pen tests.

---

## 1. Prime directive

**Secure-by-default.**  
Every external input is hostile until validated. Every output must be safe by construction.

Security work is not “extra.” It is part of correctness.

---

## 2. Default threat model (always consider)

You MUST consider common attack surfaces:
- Auth/session compromise
- Injection (SQL/command/template)
- XSS/CSRF
- SSRF
- Path traversal / file disclosure
- Insecure deserialization
- Broken access control (BOLA/IDOR)
- Rate-limit / brute force
- Sensitive data exposure (logs, errors, storage)
- Misconfiguration (CORS, headers, debug mode)

Use OWASP Top 10 as a mental checklist.

---

## 3. Input validation & output encoding

### 3.1 Validation (must be explicit)
You MUST validate all external inputs at the boundary:
- HTTP request bodies (schema validation)
- query params
- path params
- headers (when used)
- file uploads (names, size, type, content where needed)

Prefer typed schemas (Pydantic v2) and strict parsing.
Reject invalid input with clear error envelopes.

### 3.2 Output encoding
You MUST avoid unsafe output rendering:
- never inject unsanitized user content into HTML
- be careful with markdown rendering; sanitize if rendered to HTML
- avoid building HTML strings manually

---

## 4. Authentication & authorization

### 4.1 Auth
Auth MUST be:
- explicit
- centralized
- deny-by-default

Never rely on “hidden” auth in a handler. Use dependencies/middleware consistently.

### 4.2 Authorization (BOLA/IDOR defense)
Every resource access MUST check:
- “is this caller allowed to access this resource?”

Do not trust IDs in URLs. Verify ownership/permissions.

### 4.3 Session/token handling
- Do not log tokens or secrets.
- Use secure cookie flags if cookies are used (`HttpOnly`, `Secure`, `SameSite`).
- Consider CSRF if cookies are used for auth.
- Prefer short-lived tokens + rotation where feasible (repo-dependent).

### 4.4 Rate limiting and brute force resistance
Public endpoints SHOULD have:
- rate limiting
- lockouts/backoff for auth attempts

---

## 5. Injection resistance

### 5.1 SQL injection
- Use parameterized queries.
- If using an ORM/query builder, never concatenate SQL strings from user input.

### 5.2 Command injection
- Avoid `shell=True`.
- Validate and whitelist any arguments passed to subprocess.

### 5.3 Template injection
- Avoid evaluating untrusted templates.
- Never `eval` untrusted input.

---

## 6. File handling security

If the system supports file operations, you MUST defend against:
- path traversal (`../`)
- absolute path injection
- symlink tricks
- oversized uploads (limits)
- content-type confusion

Rules:
- normalize and validate paths
- constrain file ops to an allowed root directory
- never trust client-provided filenames blindly
- return safe download headers

---

## 7. HTTP security headers & browser posture (when applicable)

For web apps, you SHOULD set:
- Content-Security-Policy (CSP) appropriate to the app
- X-Content-Type-Options: nosniff
- Referrer-Policy
- Permissions-Policy
- HSTS (if served over HTTPS)

CORS MUST be explicit and restrictive:
- do not use `*` with credentials
- restrict origins to known trusted values

---

## 8. Error handling & information leakage

Errors MUST:
- avoid leaking secrets, internal paths, stack traces (in production)
- return consistent error envelopes
- log details server-side with redaction

Debug endpoints MUST NOT ship enabled by default.

---

## 9. Dependency and supply-chain security

You MUST:
- prefer maintained, widely used dependencies
- pin or lock dependencies (uv/pnpm lockfiles)
- avoid installing from random Git SHAs unless explicitly justified

You SHOULD:
- run dependency vulnerability scanning (repo policy)
- run secret scanning

---

## 10. ZAP/Burp readiness checklist

Before declaring security-ready, you MUST ensure:

- No open admin/debug endpoints.
- Auth is required where appropriate; unauthorized requests fail cleanly.
- Authorization checks exist (no IDOR).
- Inputs are validated; invalid inputs return 4xx, not 500.
- No reflected input in HTML without encoding.
- No obvious SSRF primitives (server-side fetch) without allowlists.
- File endpoints defend against traversal and oversized content.
- Rate limits exist for sensitive endpoints (auth, password reset, etc.).
- Security headers and CORS are sensible.

You SHOULD run:
- OWASP ZAP baseline scan (CI or local)
- Manual Burp exploration for critical flows

---

## 11. Security testing requirements

You SHOULD include security tests for:
- auth/authorization boundaries
- injection attempts
- path traversal attempts
- XSS output encoding (where relevant)
- SSRF attempts (where relevant)

Security tests MUST be deterministic and safe.

---
