---
description: "Roadmap-first planning — specs, milestones, and artifact selection by task size"
---

# Planning

When planning non-trivial work, create the minimum artifacts needed to execute confidently.

## Roadmap-First Principle

For anything spanning multiple milestones or sessions, create a `ROADMAP.md` first. A roadmap is the source of truth for what's being built, what's done, and what's next.

### When to Roadmap

| Scope | Approach |
|-------|----------|
| Single session, <3 files | One-off plan mode (no roadmap needed) |
| Single session, 3-10 files | Plan mode with spec file |
| Multi-session or multi-milestone | **Roadmap required** |
| Architectural change | **Roadmap + ADR required** |

### File Conventions

```
project/
├── ROADMAP.md                 ← High-level milestones and status
├── specs/<milestone>.md       ← Detailed spec per milestone
├── plans/<milestone>.md       ← Execution plan per milestone
└── CLAUDE.md                  ← Links active roadmaps
```

## The Workflow

1. **Roadmap** — Define milestones with clear deliverables and order
2. **Pick milestone** — Choose the next unfinished milestone
3. **Plan mode** — Enter plan mode to explore and design
4. **Step 0** — Create `specs/<milestone>.md` and `plans/<milestone>.md`
5. **Execute** — Implement according to the plan
6. **Verify** — Run tests, confirm acceptance criteria
7. **Update roadmap** — Mark milestone done, note what changed

### ROADMAP.md Format

```markdown
# Roadmap: <Project Name>

## Milestones

### M1: <Name> ✅
- Deliverable 1
- Deliverable 2

### M2: <Name> 🔄 ← current
- Deliverable 1
- Deliverable 2

### M3: <Name> ⬚
- Deliverable 1
- Deliverable 2
```

Status markers: ✅ done, 🔄 in progress, ⬚ not started.

## CLAUDE.md Contract

Every project's CLAUDE.md (or CLAUDE.local.md) SHOULD link active roadmaps:

```markdown
## Active Roadmaps
- [Feature X](./ROADMAP.md) — current milestone: M2
```

This makes roadmaps discoverable for any agent entering the project.

## One-Off Plan Mode

For small/medium work (single session, <3 files), plan mode without a roadmap is fine. Use `EnterPlanMode`, explore, plan, get approval, execute.

For anything bigger, **suggest the roadmap approach** to the user.

## Artifact Selection by Size

| Task Size | Files | Artifacts |
|-----------|-------|-----------|
| Small | <3 | TASKS only |
| Medium | 3-10 | SPEC + TASKS (+ optional DESIGN) |
| Large | 10+ | SPEC + DESIGN + TASKS + PLAN |
| Architectural decision | any | ADR required |

## Artifact Essentials

- **SPEC**: What + why + constraints + success criteria
- **TASKS**: Ordered, measurable, checkable items
- **DESIGN**: Options, tradeoffs, chosen path, rationale
- **PLAN**: Sequencing, dependencies, coordination steps
- **ADR**: Decision record with context and consequences

## Quality Bar

- Specific, not vague
- Testability defined
- Explicit scope boundaries
- Clear acceptance criteria

## Anti-Patterns

- **Plan-only forever** — Planning without executing is procrastination
- **Roadmap without milestones** — A roadmap that's just a wish list helps nobody
- **Milestones without specs** — "Build the thing" is not a milestone
- **Hidden tradeoffs** — Every design choice has costs; name them
- **Unbounded scope** — If you can't say what's NOT included, scope is undefined
