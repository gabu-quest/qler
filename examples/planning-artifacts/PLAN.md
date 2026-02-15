# PLAN: User Session Management Implementation

**SPEC:** SPEC.md
**TASKS:** TASKS.md
**DESIGN:** DESIGN.md
**Status:** In Progress
**Updated:** 2025-12-13

---

## Execution Strategy

This plan breaks down the implementation into **coherent, end-to-end slices** that can be committed incrementally while keeping the repository in a working state.

**Principle:** Each slice should be testable and reviewable independently.

---

## Slice 1: Database Foundation ✅ COMPLETE

**Goal:** Sessions table exists and can be queried.

### Tasks
- [x] T1: Database Schema & Models

### Implementation Steps
1. [x] Create Alembic migration for sessions table
2. [x] Define SQLAlchemy `Session` model
3. [x] Add relationship to `User` model
4. [x] Run migration on dev database
5. [x] Verify table structure with manual query

### Tests
- [x] Test migration runs successfully
- [x] Test Session model can be instantiated
- [x] Test foreign key constraint works

### Commit
```
feat: add sessions table and SQLAlchemy model

- Create sessions table with token, user_id, timestamps
- Add indexes for fast token lookups and cleanup queries
- Add Session model with User relationship
- Migration tested on clean database

Refs: T1
```

**Verification:** `psql` shows sessions table with correct schema.

---

## Slice 2: Core Session Service 🔄 IN PROGRESS

**Goal:** Can create, validate, and terminate sessions programmatically.

### Tasks
- [ ] T2: Session Service Layer

### Implementation Steps
1. [ ] Create `services/session_service.py`
2. [ ] Implement `create_session(user_id)` with token generation
3. [ ] Implement `validate_session(token)` with expiration logic
4. [ ] Implement `terminate_session(token)`
5. [ ] Implement `cleanup_expired_sessions()`
6. [ ] Add comprehensive unit tests

### Tests (pytest)
- [ ] `test_create_session_generates_unique_token`
- [ ] `test_validate_session_returns_valid_session`
- [ ] `test_validate_session_returns_none_for_expired`
- [ ] `test_validate_session_updates_last_activity`
- [ ] `test_terminate_session_deletes_from_db`
- [ ] `test_cleanup_deletes_only_expired_sessions`
- [ ] `test_token_is_cryptographically_random`

### Commit
```
feat: implement SessionService with CRUD operations

- create_session() generates 256-bit random tokens
- validate_session() checks expiration and updates activity
- terminate_session() deletes from database
- cleanup_expired_sessions() for maintenance
- Full unit test coverage (90%+)

Refs: T2
```

**Verification:** All SessionService unit tests pass.

---

## Slice 3: HTTP Authentication Endpoints ⏳ PENDING

**Goal:** Users can login, logout, and check session status via HTTP API.

### Tasks
- [ ] T3: HTTP API Endpoints

### Implementation Steps
1. [ ] Create `routers/auth.py`
2. [ ] Implement `POST /auth/login`
3. [ ] Implement `POST /auth/logout`
4. [ ] Implement `GET /auth/session`
5. [ ] Define Pydantic request/response models
6. [ ] Add integration tests with TestClient

### Tests (pytest + TestClient)
- [ ] `test_login_returns_session_token`
- [ ] `test_login_with_invalid_credentials_returns_401`
- [ ] `test_logout_deletes_session`
- [ ] `test_get_session_returns_info_for_valid_token`
- [ ] `test_get_session_returns_401_for_invalid_token`

### Commit
```
feat: add HTTP endpoints for session management

- POST /auth/login creates session and returns token
- POST /auth/logout terminates session
- GET /auth/session returns current session info
- Pydantic models for request/response validation
- Integration tests with FastAPI TestClient

Refs: T3
```

**Verification:** `curl` can login, make authenticated request, logout.

---

## Slice 4: Authentication Middleware ⏳ PENDING

**Goal:** Existing endpoints can be protected with `Depends(get_current_session)`.

### Tasks
- [ ] T4: Authentication Middleware

### Implementation Steps
1. [ ] Create `dependencies/auth.py`
2. [ ] Implement `get_current_session` dependency
3. [ ] Extract token from Authorization header
4. [ ] Validate via SessionService
5. [ ] Update last_activity on success
6. [ ] Raise HTTPException on failure
7. [ ] Add integration tests

### Tests
- [ ] `test_get_current_session_with_valid_token`
- [ ] `test_get_current_session_with_expired_token_raises_401`
- [ ] `test_get_current_session_without_header_raises_401`
- [ ] `test_get_current_session_updates_last_activity`

### Commit
```
feat: add authentication middleware dependency

- get_current_session extracts Bearer token from header
- Validates via SessionService
- Updates last_activity timestamp
- Raises 401 for invalid/expired sessions
- Can be used with FastAPI Depends()

Refs: T4
```

**Verification:** Protected endpoint returns 401 without token, 200 with valid token.

---

## Slice 5: WebSocket Authentication ⏳ PENDING

**Goal:** WebSocket connections can authenticate via session token.

### Tasks
- [ ] T5: WebSocket Authentication

### Implementation Steps
1. [ ] Update WebSocket endpoint to accept token in query params
2. [ ] Validate token before accepting connection
3. [ ] Reject with clear error if invalid
4. [ ] Make session context available in handler
5. [ ] Add integration tests

### Tests
- [ ] `test_websocket_accepts_connection_with_valid_token`
- [ ] `test_websocket_rejects_connection_with_invalid_token`
- [ ] `test_websocket_rejects_connection_with_expired_token`

### Commit
```
feat: add WebSocket authentication via session token

- WebSocket accepts session token in query params
- Validates before accepting connection
- Rejects with clear error if invalid
- Session context available in handler
- Integration tests for auth flows

Refs: T5
```

**Verification:** WebSocket connects with valid token, rejects without.

---

## Slice 6: Background Cleanup Job ⏳ PENDING

**Goal:** Expired sessions are automatically cleaned up.

### Tasks
- [ ] T6: Session Cleanup Job

### Implementation Steps
1. [ ] Create `tasks/session_cleanup.py`
2. [ ] Implement cleanup function using SessionService
3. [ ] Add background task scheduler (FastAPI lifespan or APScheduler)
4. [ ] Schedule to run hourly
5. [ ] Add logging
6. [ ] Add unit tests

### Tests
- [ ] `test_cleanup_deletes_expired_sessions`
- [ ] `test_cleanup_does_not_delete_active_sessions`
- [ ] `test_cleanup_logs_count_deleted`
- [ ] `test_cleanup_handles_database_errors`

### Commit
```
feat: add hourly background job for session cleanup

- Cleanup job runs every hour
- Deletes sessions where expires_at < now()
- Logs number of sessions deleted
- Handles database errors gracefully
- Unit tests cover logic and error cases

Refs: T6
```

**Verification:** Manual trigger deletes expired sessions, logs show output.

---

## Slice 7: Admin Endpoints ⏳ PENDING

**Goal:** Admins can revoke any session.

### Tasks
- [ ] Part of T3

### Implementation Steps
1. [ ] Add `DELETE /admin/sessions/:session_id` endpoint
2. [ ] Check admin permissions (assume middleware exists)
3. [ ] Call SessionService.terminate_session()
4. [ ] Return 204 on success, 404 if not found
5. [ ] Add integration tests

### Tests
- [ ] `test_admin_can_delete_any_session`
- [ ] `test_admin_delete_returns_404_for_nonexistent`
- [ ] `test_non_admin_cannot_delete_sessions_403`

### Commit
```
feat: add admin endpoint to revoke sessions

- DELETE /admin/sessions/:session_id revokes any session
- Requires admin permissions
- Returns 204 on success, 404 if not found
- Integration tests verify behavior

Refs: T3
```

**Verification:** Admin can revoke session via API, user is logged out.

---

## Slice 8: Observability & Logging ⏳ PENDING

**Goal:** All session operations are logged for debugging and auditing.

### Tasks
- [ ] T7: Observability & Logging

### Implementation Steps
1. [ ] Add structured logging to SessionService methods
2. [ ] Add logging to auth endpoints
3. [ ] Add logging to cleanup job
4. [ ] Ensure tokens are filtered from logs
5. [ ] Add integration tests that verify logs

### Tests
- [ ] `test_session_creation_is_logged`
- [ ] `test_session_validation_failure_is_logged`
- [ ] `test_cleanup_job_logs_summary`
- [ ] `test_tokens_do_not_appear_in_logs`

### Commit
```
feat: add comprehensive logging for session operations

- Session creation, validation, termination logged
- Cleanup job logs summary
- Structured JSON format for parsing
- Tokens filtered from all logs (security)
- Integration tests verify logging behavior

Refs: T7
```

**Verification:** Logs show session lifecycle events in structured format.

---

## Slice 9: End-to-End Tests ⏳ PENDING

**Goal:** Full user workflows are tested in Playwright.

### Tasks
- [ ] T_TEST1: End-to-End Tests

### Implementation Steps
1. [ ] Create Playwright test suite
2. [ ] Test: login → authenticated request → logout
3. [ ] Test: expired session returns 401
4. [ ] Test: WebSocket with valid/invalid session
5. [ ] Run against local dev server
6. [ ] Add to CI pipeline

### Tests (Playwright)
- [ ] `test_user_can_login_and_access_protected_resource`
- [ ] `test_user_can_logout`
- [ ] `test_expired_session_redirects_to_login`
- [ ] `test_websocket_connects_with_valid_session`
- [ ] `test_websocket_fails_with_invalid_session`

### Commit
```
test: add Playwright E2E tests for session management

- Full workflow: login → authenticated requests → logout
- Expired session handling
- WebSocket authentication flows
- All tests pass consistently
- Integrated into CI pipeline

Refs: T_TEST1
```

**Verification:** Playwright tests pass locally and in CI.

---

## Slice 10: Security Audit & Tests ⏳ PENDING

**Goal:** Security review complete, all OWASP concerns addressed.

### Tasks
- [ ] T_TEST2: Security Tests

### Implementation Steps
1. [ ] Run security test suite
2. [ ] Verify tokens are random (statistical tests)
3. [ ] Verify tokens don't leak in logs
4. [ ] Test session isolation (cannot use other user's token)
5. [ ] Complete security review checklist
6. [ ] Document findings

### Tests
- [ ] `test_tokens_are_cryptographically_random`
- [ ] `test_cannot_reuse_token_after_logout`
- [ ] `test_cannot_use_another_users_token`
- [ ] `test_tokens_not_in_logs`
- [ ] `test_expired_tokens_rejected`

### Commit
```
test: add security test suite for session management

- Verify token randomness (CSPRNG)
- Test session isolation (no cross-user access)
- Verify token filtering in logs
- Security review checklist complete
- No OWASP Top 10 vulnerabilities found

Refs: T_TEST2
```

**Verification:** Security review approved, no critical findings.

---

## Slice 11: Documentation ⏳ PENDING

**Goal:** All documentation is complete and accurate.

### Tasks
- [ ] T_DOC1: Documentation

### Implementation Steps
1. [ ] Update API documentation (OpenAPI/Swagger)
2. [ ] Add architecture diagrams to docs/
3. [ ] Update README with session setup
4. [ ] Write client integration guide
5. [ ] Document WebSocket auth pattern
6. [ ] Create ADR for session storage choice

### Deliverables
- [ ] OpenAPI schema includes all session endpoints
- [ ] Architecture diagram shows session flow
- [ ] README includes "Session Management" section
- [ ] Client guide with Python/JS examples
- [ ] ADR-004-session-storage-postgresql.md

### Commit
```
docs: add comprehensive session management documentation

- OpenAPI schema updated with all endpoints
- Architecture diagrams show session flows
- README includes setup and usage guide
- Client integration guide with code examples
- ADR documents session storage decision

Refs: T_DOC1
```

**Verification:** Documentation is clear and complete, examples work.

---

## Summary

### Execution Order
1. ✅ Database Foundation (T1)
2. 🔄 Core Session Service (T2) ← **Current**
3. ⏳ HTTP Endpoints (T3)
4. ⏳ Middleware (T4)
5. ⏳ WebSocket Auth (T5)
6. ⏳ Cleanup Job (T6)
7. ⏳ Admin Endpoints (T3)
8. ⏳ Logging (T7)
9. ⏳ E2E Tests (T_TEST1)
10. ⏳ Security Tests (T_TEST2)
11. ⏳ Documentation (T_DOC1)

### Progress
- **Completed:** 1 slice (9%)
- **In Progress:** 1 slice
- **Remaining:** 9 slices

### Estimated Timeline
- **Slice 2-4:** Session 1 (3-4 hours)
- **Slice 5-7:** Session 2 (2-3 hours)
- **Slice 8-11:** Session 3 (2-3 hours)

**Total:** ~8-10 hours of focused work

---

## Handoff Notes

**Current State:**
- Database schema deployed
- Session model working
- No service layer yet

**Next Steps:**
1. Implement SessionService (Slice 2)
2. Write comprehensive unit tests
3. Commit when all tests pass
4. Move to Slice 3

**Blockers:** None

**Questions:** None

---

[PLAN_READY]
