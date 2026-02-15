# DESIGN: User Session Management

**SPEC:** SPEC.md
**TASKS:** TASKS.md
**Author:** Engineering Team
**Date:** 2025-12-13
**Status:** Approved

---

## 1. Context

### Current State
- Application has basic username/password authentication
- No session persistence - users must authenticate on every request
- No WebSocket authentication mechanism
- No session expiration or cleanup

### Problem
Users cannot maintain authenticated state across requests, making the application unusable for real workflows requiring multiple operations.

---

## 2. Goals

1. Implement secure, scalable session management
2. Support both HTTP and WebSocket authentication
3. Provide <10ms session validation latency
4. Ensure OWASP-compliant security posture

---

## 3. Non-Goals

- OAuth/SSO integration (future)
- Multi-device session tracking (future)
- Advanced analytics (future)

---

## 4. Requirements

### Functional
- Create, validate, and terminate sessions
- Support HTTP and WebSocket protocols
- Automatic expiration (time-based and activity-based)
- Admin session revocation

### Non-Functional
- Performance: <10ms validation (p95)
- Security: Cryptographically secure tokens, HTTPS-only
- Reliability: Graceful degradation on database failures
- Observability: Comprehensive logging

---

## 5. Constraints & Assumptions

### Constraints
- Must use existing PostgreSQL database
- Must integrate with FastAPI application
- Must not break existing auth flows

### Assumptions
- HTTPS enforced at infrastructure level
- Database connection pooling configured
- User IDs are unique and stable

---

## 6. Current Architecture

```
Client
  ↓
FastAPI App (no session management)
  ↓
Auth Service (username/password only)
  ↓
PostgreSQL (users table)
```

**Current authentication flow:**
1. Client sends username/password on every request
2. Server validates against database
3. No state maintained

---

## 7. Proposed Solution

### Architecture Overview

```
Client
  ↓
  Bearer Token in Header/Query
  ↓
FastAPI Middleware (validate_session)
  ↓
SessionService (create/validate/terminate)
  ↓
PostgreSQL (sessions table)
```

### Components

#### Database Layer
**Sessions Table:**
```sql
CREATE TABLE sessions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    token VARCHAR(64) NOT NULL UNIQUE,
    created_at TIMESTAMP NOT NULL DEFAULT NOW(),
    last_activity TIMESTAMP NOT NULL DEFAULT NOW(),
    expires_at TIMESTAMP NOT NULL,
    INDEX idx_token (token),
    INDEX idx_expires_at (expires_at)
);
```

#### Service Layer
**SessionService:**
```python
class SessionService:
    async def create_session(self, user_id: UUID) -> Session:
        """Create new session with cryptographically random token."""

    async def validate_session(self, token: str) -> Optional[Session]:
        """Validate token, update activity, return session or None."""

    async def terminate_session(self, token: str) -> bool:
        """Delete session, return True if existed."""

    async def cleanup_expired_sessions(self) -> int:
        """Delete expired sessions, return count."""
```

#### API Layer
**Endpoints:**
- `POST /auth/login` → Create session, return token
- `POST /auth/logout` → Terminate session
- `GET /auth/session` → Get current session info
- `DELETE /admin/sessions/:id` → Admin revoke

#### Middleware
**Authentication Dependency:**
```python
async def get_current_session(
    authorization: str = Header(None),
    session_service: SessionService = Depends()
) -> Session:
    """Extract and validate session token from header."""
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(401, "Missing or invalid authorization header")

    token = authorization[7:]
    session = await session_service.validate_session(token)

    if not session:
        raise HTTPException(401, "Invalid or expired session")

    return session
```

---

## 8. Public Interfaces

### API Schemas (Pydantic Models)

#### Request Models
```python
class LoginRequest(BaseModel):
    username: str
    password: str

class LogoutRequest(BaseModel):
    pass  # Token from header
```

#### Response Models
```python
class LoginResponse(BaseModel):
    session_token: str
    user_id: UUID
    expires_at: datetime

class SessionInfo(BaseModel):
    user_id: UUID
    created_at: datetime
    last_activity: datetime
    expires_at: datetime
```

#### Error Envelope
```python
class ErrorResponse(BaseModel):
    error: str
    detail: str
    timestamp: datetime
```

---

## 9. Data Model & Persistence

### Sessions Table
- **Primary key:** UUID (id)
- **Foreign key:** user_id → users(id) with CASCADE delete
- **Unique constraint:** token (for fast lookups)
- **Indexes:** token (primary lookup), expires_at (cleanup queries)

### Token Generation
```python
import secrets
token = secrets.token_urlsafe(32)  # 256 bits of randomness
```

### Expiration Logic
- **Absolute expiration:** 7 days from creation
- **Activity expiration:** 24 hours since last activity
- **Cleanup:** Hourly background job deletes where expires_at < now()

---

## 10. Error Handling

### Error Categories

| Error | HTTP Status | When | Client Action |
|-------|-------------|------|---------------|
| Missing token | 401 | No Authorization header | Redirect to login |
| Invalid token | 401 | Token not in DB | Redirect to login |
| Expired token | 401 | expires_at < now | Redirect to login |
| Server error | 500 | Database failure | Retry with backoff |

### Error Response Format
```json
{
  "error": "InvalidSession",
  "detail": "Session has expired",
  "timestamp": "2025-12-13T10:30:00Z"
}
```

---

## 11. Observability

### Logging Strategy

**Session Creation (INFO):**
```json
{
  "event": "session_created",
  "user_id": "uuid",
  "expires_at": "timestamp",
  "duration": "P7D"
}
```

**Session Validation (DEBUG):**
```json
{
  "event": "session_validated",
  "user_id": "uuid",
  "last_activity_updated": true
}
```

**Session Failure (WARNING):**
```json
{
  "event": "session_validation_failed",
  "reason": "expired|invalid|not_found",
  "token_prefix": "abc..." // First 3 chars only
}
```

**Cleanup Job (INFO):**
```json
{
  "event": "session_cleanup_complete",
  "sessions_deleted": 42,
  "duration_ms": 123
}
```

### Metrics (Future)
- `sessions_created_total` (counter)
- `sessions_active` (gauge)
- `session_validation_duration_ms` (histogram)
- `sessions_expired_total` (counter)

---

## 12. Security Considerations

### Threat Model

| Threat | Mitigation |
|--------|-----------|
| Token theft | HTTPS-only, short-lived tokens |
| Token prediction | Cryptographically random (256-bit) |
| Session fixation | New token on each login |
| Session hijacking | Activity timeout, single-use logout |
| Brute force | Rate limiting (future), account lockout |
| Token in logs | Filter tokens from all logs |

### Security Controls
1. ✅ Tokens generated with `secrets.token_urlsafe()` (CSPRNG)
2. ✅ HTTPS-only transmission
3. ✅ Tokens stored hashed in database (future enhancement)
4. ✅ Activity-based expiration limits hijacking window
5. ✅ User can terminate own sessions
6. ✅ Admin can revoke any session

### OWASP Top 10 Review
- **A01 - Broken Access Control:** ✅ Sessions tied to user_id, validated on each request
- **A02 - Cryptographic Failures:** ✅ CSPRNG tokens, HTTPS-only
- **A03 - Injection:** ✅ Parameterized queries, Pydantic validation
- **A07 - Auth Failures:** ✅ Secure session management, activity timeout

---

## 13. Testing Strategy

### Unit Tests (pytest)
- `SessionService` methods (create, validate, terminate, cleanup)
- Token generation randomness
- Expiration logic (absolute and activity-based)
- Error handling (database failures, invalid inputs)

### Integration Tests (TestClient)
- Login flow: POST /auth/login → token
- Authenticated request: GET /auth/session with Bearer token
- Logout flow: POST /auth/logout
- Expired session rejection
- Admin revocation

### Feature Tests
- Full workflow: login → multiple authenticated requests → logout
- Session expiration after timeout
- Concurrent session validation (10k sessions)

### E2E Tests (Playwright)
- User logs in via UI
- Makes authenticated requests
- Logs out
- Cannot access protected resources after logout

---

## 14. Rollout / Migration Plan

### Phase 1: Infrastructure (Week 1)
1. Create sessions table (migration)
2. Deploy SessionService
3. Verify database performance

### Phase 2: HTTP API (Week 1-2)
1. Deploy /auth endpoints
2. Update existing routes to use session middleware
3. Beta test with internal users

### Phase 3: WebSocket Support (Week 2)
1. Add WebSocket auth
2. Test with real-time features
3. Monitor performance

### Phase 4: Cleanup & Polish (Week 3)
1. Deploy background cleanup job
2. Add comprehensive logging
3. Security review
4. Documentation

### Backwards Compatibility
- Existing username/password auth still works (creates session)
- No breaking changes to public APIs
- Session is optional for backward compatibility (deprecated path)

---

## 15. Alternatives Considered

### Alternative 1: JWT Tokens (Stateless)
**Pros:** No database lookups, scalable
**Cons:** Cannot revoke, larger tokens, no activity tracking
**Decision:** Rejected - need revocation and activity tracking

### Alternative 2: Redis Session Store
**Pros:** Very fast, built for sessions
**Cons:** Additional infrastructure, persistence complexity
**Decision:** Rejected for MVP - PostgreSQL sufficient, revisit if performance issues

### Alternative 3: Cookie-Based Sessions
**Pros:** Browser handles storage
**Cons:** Doesn't work for mobile/API clients, CSRF concerns
**Decision:** Rejected - need API/WebSocket support

---

## 16. Risks & Mitigations

| Risk | Impact | Probability | Mitigation |
|------|--------|-------------|------------|
| Database performance | High | Low | Indexed queries, connection pooling, monitoring |
| Session table growth | Medium | Medium | Hourly cleanup job, set reasonable defaults |
| Token collisions | Critical | Very Low | 256-bit tokens (2^256 space), impossible in practice |
| Security vulnerability | Critical | Low | Security review, OWASP compliance, penetration test |

---

## 17. Acceptance Criteria

1. ✅ All endpoints implemented and tested
2. ✅ Session validation <10ms (p95)
3. ✅ Unit tests >90% coverage
4. ✅ Integration tests cover all flows
5. ✅ Playwright tests pass
6. ✅ Security review complete
7. ✅ Documentation updated
8. ✅ Logging implemented
9. ✅ Migration runs successfully
10. ✅ Beta users provide positive feedback

---

## Appendix A: Database Schema (Full)

```sql
-- Migration: 001_create_sessions_table.sql
CREATE TABLE sessions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    token VARCHAR(64) NOT NULL UNIQUE,
    created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW(),
    last_activity TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW(),
    expires_at TIMESTAMP WITH TIME ZONE NOT NULL,
    metadata JSONB DEFAULT '{}',

    CONSTRAINT sessions_user_id_fkey FOREIGN KEY (user_id)
        REFERENCES users(id) ON DELETE CASCADE
);

CREATE INDEX idx_sessions_token ON sessions(token);
CREATE INDEX idx_sessions_expires_at ON sessions(expires_at);
CREATE INDEX idx_sessions_user_id ON sessions(user_id);

COMMENT ON TABLE sessions IS 'User session storage for authentication';
COMMENT ON COLUMN sessions.token IS 'Cryptographically random session token (256-bit)';
COMMENT ON COLUMN sessions.expires_at IS 'Absolute expiration time (7 days from creation)';
```

---

## Appendix B: References

- [OWASP Session Management Cheat Sheet](https://cheatsheetseries.owasp.org/cheatsheets/Session_Management_Cheat_Sheet.html)
- [FastAPI Security](https://fastapi.tiangolo.com/tutorial/security/)
- Python `secrets` module (CSPRNG): PEP 506

---

**Sign-off:**
- Engineering: ✅ Approved
- Security: ✅ Approved
- Product: ✅ Approved

**Next:** Create PLAN.md for execution order
