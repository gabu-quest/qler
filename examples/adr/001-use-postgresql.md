# ADR-001: Use PostgreSQL for Primary Database

**Status:** Accepted
**Date:** 2025-12-10
**Deciders:** Engineering Team, CTO
**Context:** Initial database selection for application

---

## Context and Problem Statement

We need to select a primary database for our application. The application will have:
- User authentication and session management
- Complex relational data (users, organizations, projects, tasks)
- Need for ACID transactions
- Expected scale: 100k users, 10M records
- Team has varied database experience

**Decision:** What database should we use as our primary data store?

---

## Decision Drivers

1. **Data Model** - Complex relational data with foreign keys and constraints
2. **ACID Compliance** - Need strong consistency for financial/auth data
3. **Ecosystem** - Mature ORM support, tooling, hosting options
4. **Team Experience** - Mix of SQL and NoSQL experience
5. **Cost** - Open source preferred, good managed hosting options
6. **Performance** - Sub-100ms queries for typical CRUD operations
7. **Scalability** - Should handle 100k users without re-architecture

---

## Considered Options

### Option 1: PostgreSQL
**Pros:**
- Excellent relational model with full SQL support
- ACID compliant with strong consistency guarantees
- Rich ecosystem (SQLAlchemy, psycopg3, pgAdmin)
- JSON/JSONB support for flexible schemas where needed
- Full-text search, GIS extensions available
- Free and open source
- Excellent managed hosting (RDS, Supabase, Neon, etc.)
- Battle-tested at massive scale (Instagram, Spotify, etc.)

**Cons:**
- Vertical scaling limits (though very high)
- Requires careful indexing for performance
- More complex than document databases for simple key-value use cases

### Option 2: MySQL/MariaDB
**Pros:**
- Widely used, huge ecosystem
- Good ORM support
- Free and open source
- Many managed hosting options

**Cons:**
- Less feature-rich than PostgreSQL (weaker JSON support, fewer data types)
- Historically weaker compliance with SQL standards
- Less consistent syntax across versions

### Option 3: MongoDB (NoSQL)
**Pros:**
- Flexible schema
- Good for rapid prototyping
- Horizontal scaling built-in

**Cons:**
- No native ACID transactions (added later, but not primary design)
- Complex joins are inefficient
- Data duplication required for many-to-many relationships
- Our data is inherently relational

### Option 4: SQLite
**Pros:**
- Zero configuration
- Embedded, no separate server
- Perfect for development

**Cons:**
- Not designed for concurrent writes at scale
- No network access (not suitable for multi-server deployments)
- Limited for production use

---

## Decision Outcome

**Chosen Option:** PostgreSQL

**Rationale:**

1. **Best fit for data model** - Our data is relational (users → organizations → projects → tasks). PostgreSQL's foreign keys, constraints, and joins are exactly what we need.

2. **ACID compliance** - Authentication, sessions, and financial data require strong consistency. PostgreSQL provides this by default.

3. **Ecosystem maturity** - SQLAlchemy (Python ORM) has excellent PostgreSQL support. Tools like Alembic (migrations), pgAdmin (GUI), and pg_dump (backups) are mature.

4. **JSON flexibility** - JSONB columns let us store flexible metadata without sacrificing relational structure for core data.

5. **Performance** - With proper indexing, PostgreSQL easily handles our expected scale. Instagram ran on PostgreSQL for years at massive scale.

6. **Cost** - Open source with excellent managed hosting options (AWS RDS, Supabase, Railway, Neon). Can start small and scale.

7. **Team ramp-up** - SQL is widely known. PostgreSQL's excellent documentation and community make learning straightforward.

---

## Consequences

### Positive

- ✅ Strong data integrity guarantees (foreign keys, constraints, transactions)
- ✅ Rich querying capabilities (complex joins, subqueries, CTEs, window functions)
- ✅ Excellent tooling ecosystem (ORMs, migration tools, monitoring)
- ✅ JSONB support allows flexibility where needed without going full NoSQL
- ✅ Well-understood performance characteristics and scaling patterns
- ✅ Large community and extensive documentation

### Negative

- ⚠️ Team must learn SQL if not familiar (mitigated by ORM initially)
- ⚠️ Schema migrations require planning (mitigated by Alembic)
- ⚠️ Vertical scaling limits exist (though very high, not a concern for our scale)
- ⚠️ Must be careful with query performance (requires understanding of indexes)

### Neutral

- ℹ️ Need to set up managed PostgreSQL instance (or use Docker for dev)
- ℹ️ Need to establish backup strategy (handled by managed hosting)
- ℹ️ Connection pooling required for high concurrency (PgBouncer or SQLAlchemy pool)

---

## Implementation Notes

### Development Setup
```bash
# Docker for local development
docker run -d \
  --name postgres-dev \
  -e POSTGRES_PASSWORD=dev-password \
  -e POSTGRES_DB=myapp \
  -p 5432:5432 \
  postgres:16-alpine
```

### Production Hosting
- Use managed PostgreSQL (AWS RDS, Supabase, or similar)
- Enable automated backups (daily minimum)
- Set up replication for high availability (when needed)
- Use connection pooling (PgBouncer or managed equivalent)

### ORM Strategy
- Use SQLAlchemy 2.0+ with async support
- Alembic for schema migrations
- Type hints for all models
- Explicit indexes for foreign keys and frequent queries

### Migration Strategy
- All schema changes via Alembic migrations
- Test migrations on copy of production data before deploying
- Keep migrations reversible where possible
- Document breaking changes in CHANGELOG

---

## Related Decisions

- ADR-002: Use SQLAlchemy 2.0 as ORM (pending)
- ADR-003: Use Alembic for database migrations (pending)

---

## References

- [PostgreSQL Official Docs](https://www.postgresql.org/docs/)
- [SQLAlchemy Documentation](https://docs.sqlalchemy.org/)
- [Use The Index, Luke! (SQL Performance)](https://use-the-index-luke.com/)
- [PostgreSQL at Scale (Instagram Engineering)](https://instagram-engineering.com/tagged/postgresql)

---

## Review and Update

This decision will be reviewed:
- When application reaches 50k users (evaluate horizontal scaling needs)
- If query performance becomes a consistent bottleneck
- If a major new PostgreSQL version changes the landscape

**Next review:** 2026-06-01 or when scale triggers above occur
