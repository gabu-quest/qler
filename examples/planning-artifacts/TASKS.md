# TASKS: User Session Management

**SPEC:** SPEC.md (User Session Management)
**Status:** In Progress
**Updated:** 2025-12-13

---

## Task List

### Core Infrastructure

#### T1: Database Schema & Models
**Type:** feat
**Status:** ✅ Completed
**Acceptance Criteria:**
- [ ] `sessions` table created with proper columns (id, user_id, token, created_at, last_activity, expires_at)
- [ ] Index on `token` column for fast lookups
- [ ] Index on `expires_at` for cleanup queries
- [ ] SQLAlchemy model `Session` with proper relationships
- [ ] Alembic migration created and tested
- [ ] Migration runs successfully on clean database

**Dependencies:** None

---

#### T2: Session Service Layer
**Type:** feat
**Status:** 🔄 In Progress
**Acceptance Criteria:**
- [ ] `SessionService` class implements create_session(user_id) → Session
- [ ] `SessionService` implements validate_session(token) → Optional[Session]
- [ ] `SessionService` implements terminate_session(token) → bool
- [ ] `SessionService` implements cleanup_expired_sessions() → int
- [ ] Tokens are generated with secrets.token_urlsafe(32) (256 bits)
- [ ] Session expiration logic is correct (absolute and activity-based)
- [ ] All methods have type hints
- [ ] Unit tests cover happy path, edge cases, error conditions

**Dependencies:** T1

---

#### T3: HTTP API Endpoints
**Type:** feat
**Status:** ⏳ Pending
**Acceptance Criteria:**
- [ ] POST /auth/login creates session, returns token
- [ ] POST /auth/logout terminates session
- [ ] GET /auth/session returns current session info
- [ ] DELETE /admin/sessions/:session_id revokes session (admin only)
- [ ] All endpoints use Pydantic models for request/response
- [ ] Proper HTTP status codes (200, 401, 403, 404)
- [ ] Error responses follow standard envelope format
- [ ] Integration tests use TestClient to verify behavior

**Dependencies:** T2

---

#### T4: Authentication Middleware
**Type:** feat
**Status:** ⏳ Pending
**Acceptance Criteria:**
- [ ] FastAPI dependency `get_current_session` extracts and validates token
- [ ] Token extracted from Authorization: Bearer header
- [ ] Returns Session object if valid
- [ ] Raises HTTPException 401 if invalid/expired
- [ ] Updates last_activity timestamp on successful validation
- [ ] Integration tests verify middleware behavior
- [ ] Works with FastAPI Depends() pattern

**Dependencies:** T2

---

### WebSocket Support

#### T5: WebSocket Authentication
**Type:** feat
**Status:** ⏳ Pending
**Acceptance Criteria:**
- [ ] WebSocket connections accept session token in query params
- [ ] Token is validated before accepting connection
- [ ] Invalid tokens result in connection rejection with clear error
- [ ] Session context is available in WebSocket handler
- [ ] Integration tests verify WebSocket authentication flow
- [ ] Documentation updated with WebSocket auth pattern

**Dependencies:** T2, T4

---

### Operations & Maintenance

#### T6: Session Cleanup Job
**Type:** feat
**Status:** ⏳ Pending
**Acceptance Criteria:**
- [ ] Background task runs hourly
- [ ] Deletes sessions where expires_at < now()
- [ ] Logs number of sessions deleted
- [ ] Handles database errors gracefully
- [ ] Can be triggered manually for testing
- [ ] Unit tests verify cleanup logic

**Dependencies:** T2

---

#### T7: Observability & Logging
**Type:** feat
**Status:** ⏳ Pending
**Acceptance Criteria:**
- [ ] Session creation logged (user_id, created_at, expires_at)
- [ ] Session validation failures logged (reason: expired, invalid, not found)
- [ ] Session termination logged (user_id, reason)
- [ ] Cleanup job logs summary (sessions deleted, duration)
- [ ] Logs DO NOT contain session tokens (security)
- [ ] Structured logging format (JSON) for parsing
- [ ] Integration tests verify logging behavior

**Dependencies:** T2, T3, T6

---

### Testing & Documentation

#### T_TEST1: End-to-End Tests
**Type:** test
**Status:** ⏳ Pending
**Acceptance Criteria:**
- [ ] Playwright test: login → authenticated request → logout
- [ ] Playwright test: expired session returns 401
- [ ] Playwright test: WebSocket connection with valid session
- [ ] Playwright test: WebSocket connection with invalid session fails
- [ ] All tests pass consistently (no flakes)
- [ ] Tests use realistic user scenarios

**Dependencies:** T3, T4, T5

---

#### T_TEST2: Security Tests
**Type:** test
**Status:** ⏳ Pending
**Acceptance Criteria:**
- [ ] Test: Cannot reuse token after logout
- [ ] Test: Cannot use another user's session token
- [ ] Test: Expired tokens are rejected
- [ ] Test: Invalid tokens return 401, not 500
- [ ] Test: Session tokens do not appear in logs
- [ ] Test: Tokens are truly random (no predictable patterns)
- [ ] Security review checklist completed

**Dependencies:** T3, T4, T7

---

#### T_DOC1: Documentation
**Type:** docs
**Status:** ⏳ Pending
**Acceptance Criteria:**
- [ ] API documentation updated (OpenAPI schema)
- [ ] Architecture docs show session flow diagrams
- [ ] README includes session management setup
- [ ] Client integration guide with code examples
- [ ] WebSocket authentication documented
- [ ] Security considerations documented
- [ ] ADR created for session storage approach

**Dependencies:** T3, T4, T5

---

## Summary

- **Total Tasks:** 10
- **Completed:** 1
- **In Progress:** 1
- **Pending:** 8

**Critical Path:** T1 → T2 → T3 → T4 → T_TEST1

**Estimated Completion:** All tasks can be completed in ~3 implementation sessions
