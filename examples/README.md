# Examples

This directory contains real-world examples of artifacts, workflows, and patterns defined in The Standard.

## Directory Structure

```
examples/
├── README.md                    ← You are here
├── planning-artifacts/          ← Example SPEC, TASKS, DESIGN, PLAN documents
├── repository-structure/        ← Sample repo layouts following the standard
├── pull-request/                ← Example PR descriptions and workflows
└── adr/                         ← Example Architecture Decision Records
```

## How to Use These Examples

### For Learning
- Browse the examples to understand what "good" looks like
- Compare your work to the examples
- Use as templates for your own artifacts

### For Adoption
- Copy example files as starting templates
- Adapt to your domain while maintaining structure
- Reference these when training team members

### For Teaching AI Agents
- Point agents to these examples when planning
- Use as reference implementations
- Show "this is what we expect"

## Examples Index

### Planning Artifacts
- **[SPEC.md](./planning-artifacts/SPEC.md)** - Feature specification example (user session management)
- **[TASKS.md](./planning-artifacts/TASKS.md)** - Task breakdown with IDs and acceptance criteria
- **[DESIGN.md](./planning-artifacts/DESIGN.md)** - Design document with all required sections
- **[PLAN.md](./planning-artifacts/PLAN.md)** - Execution plan derived from tasks

### Repository Structure
- **[backend-python/](./repository-structure/backend-python/)** - Python FastAPI project layout
- **[frontend-vue/](./repository-structure/frontend-vue/)** - Vue 3 project layout
- **[monorepo/](./repository-structure/monorepo/)** - Full-stack monorepo layout

### Pull Requests
- **[feature-pr.md](./pull-request/feature-pr.md)** - Example PR description for a feature
- **[refactor-pr.md](./pull-request/refactor-pr.md)** - Example PR description for refactoring
- **[bugfix-pr.md](./pull-request/bugfix-pr.md)** - Example PR description for bug fix

### Architecture Decision Records
- **[001-use-postgresql.md](./adr/001-use-postgresql.md)** - Database choice ADR
- **[002-vue3-composition-api.md](./adr/002-vue3-composition-api.md)** - Frontend framework ADR
- **[003-monorepo-structure.md](./adr/003-monorepo-structure.md)** - Repository organization ADR

## Contributing Examples

When adding examples:

1. **Make them realistic** - Use real domain language, not foo/bar
2. **Show complete artifacts** - Don't stub or leave TODOs
3. **Include context** - Add comments explaining non-obvious choices
4. **Follow the standard** - Examples must exemplify our own rules
5. **Update this README** - Add your example to the index above

## Anti-Examples

We intentionally do NOT include "bad" examples here. The doctrine documents already show anti-patterns. These examples show the **right way** only.

---

**Tip:** When using these as templates, search and replace:
- `SessionManagement` → `YourFeatureName`
- `user_sessions` → `your_domain`
- Adjust dates and authors
- Keep the structure intact
