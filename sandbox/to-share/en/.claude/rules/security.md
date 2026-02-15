# Security

Every external input is hostile until validated.

- No secrets in code (use env vars)
- Parameterized queries always (no string concat SQL)
- Validate at boundaries (Zod, Pydantic)
- No `eval()`, no `shell=True` with user input
- Sanitize file paths to prevent traversal
