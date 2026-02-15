# Quick Reference: Security Baseline

**Prime Directive:** Every external input is hostile until validated

---

## OWASP Top 10 Mental Checklist

| Threat | Defense |
|--------|---------|
| **Injection** (SQL, command, template) | Parameterized queries, no `shell=True`, no `eval()` |
| **Broken Auth** | Centralized, explicit, deny-by-default |
| **Sensitive Data Exposure** | Don't log secrets, redact in errors |
| **XXE** | Disable external entities in XML parsers |
| **Broken Access Control** | Verify ownership on every resource access |
| **Security Misconfiguration** | No debug mode in prod, secure headers |
| **XSS** | Never inject unsanitized user input into HTML |
| **Insecure Deserialization** | Validate schemas, avoid pickle with untrusted data |
| **Using Components with Known Vulns** | Pin deps, run vuln scanning |
| **Insufficient Logging** | Log security events, don't log secrets |

---

## Input Validation Rules

**Validate ALL external inputs:**
- HTTP request bodies (Pydantic/Zod schemas)
- Query params
- Path params
- Headers (when used)
- File uploads (name, size, type, content)

**Reject invalid input with clear error envelopes.**

---

## Auth Non-Negotiables

| Rule | Why |
|------|-----|
| **Explicit auth** | Never "hidden" auth in handlers |
| **Centralized** | Use middleware/dependencies |
| **Deny-by-default** | Unknown = denied |
| **Verify ownership** | Don't trust IDs in URLs (IDOR defense) |

---

## Injection Prevention

### SQL
```python
# BAD
cursor.execute(f"SELECT * FROM users WHERE id = {user_id}")

# GOOD
cursor.execute("SELECT * FROM users WHERE id = ?", (user_id,))
```

### Command
```python
# BAD
subprocess.run(f"ls {user_input}", shell=True)

# GOOD
subprocess.run(["ls", validated_path], shell=False)
```

### Template
```python
# BAD
eval(user_input)
jinja2.Template(user_input).render()

# GOOD
template.render(safe_context)
```

---

## File Handling

| Threat | Defense |
|--------|---------|
| Path traversal (`../`) | Normalize and validate paths |
| Absolute path injection | Constrain to allowed root |
| Symlink tricks | Resolve and validate final path |
| Oversized uploads | Enforce size limits |
| Content-type confusion | Validate actual content, not just extension |

---

## HTTP Security Headers

```
Content-Security-Policy: default-src 'self'
X-Content-Type-Options: nosniff
Referrer-Policy: strict-origin-when-cross-origin
Permissions-Policy: geolocation=(), microphone=()
Strict-Transport-Security: max-age=31536000; includeSubDomains
```

### CORS Rules
- **NEVER** use `*` with credentials
- Restrict origins to known trusted values

---

## Cookie Security

```python
# Secure cookie flags
response.set_cookie(
    "session",
    value=token,
    httponly=True,    # No JS access
    secure=True,      # HTTPS only
    samesite="Lax"    # CSRF protection
)
```

---

## Error Handling

| Do | Don't |
|----|-------|
| Return consistent error envelopes | Leak stack traces |
| Log details server-side | Expose internal paths |
| Redact secrets in logs | Log tokens or credentials |

---

## Pre-Deploy Checklist

- [ ] No open admin/debug endpoints
- [ ] Auth required where appropriate
- [ ] Authorization checks exist (no IDOR)
- [ ] Inputs validated; invalid returns 4xx not 500
- [ ] No reflected input in HTML without encoding
- [ ] No SSRF primitives without allowlists
- [ ] File endpoints defend against traversal
- [ ] Rate limits on sensitive endpoints
- [ ] Security headers configured
- [ ] CORS is explicit and restrictive

---

**Full doctrine:** [`docs/doctrine/security.md`](../doctrine/security.md)
