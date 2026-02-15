---
description: "Use just for common project operations — one-liner CLI commands"
paths:
  - "justfile"
  - "Justfile"
  - "Makefile"
  - "Taskfile*"
---

# Project CLI

Use `just` for common project operations. One-liner commands are the goal.

## Why just?

- **Cross-platform** - Native Windows/Mac/Linux (no WSL needed)
- **Clean syntax** - No tab vs space hell like Make
- **Discoverable** - Run `just` with no args to list all recipes
- **Language-agnostic** - Works for any tech stack

## Example justfile

```just
# justfile

# Start development environment
dev:
    docker-compose up -d db
    npm run dev

# Run tests
test:
    npm run test

# Build for production
build:
    npm run build

# Seed database (Python script)
seed:
    uv run python scripts/seed.py

# Deploy to staging
deploy-staging:
    ./scripts/deploy.sh staging
```

## Process Cleanup in Dev Recipes

Any recipe that backgrounds a process (e.g., `just server &`) MUST:

1. **Kill previous instances** before starting — `pkill -f "pattern" || true` for each backgrounded process AND its compiled binary (e.g., `go run ./cmd/api` spawns an `api` binary)
2. **Trap EXIT to kill children** — `trap 'kill 0' EXIT` so Ctrl-C cleans up the entire process group

Without this, repeated `just dev` runs leak orphaned processes that accumulate file descriptors until the OS refuses new connections ("accept: too many open files").

```just
# WRONG — orphans the server on Ctrl-C, accumulates zombies on restart
both:
    #!/usr/bin/env bash
    just server &
    npm run dev

# CORRECT — kills leftovers, cleans up on exit
both:
    #!/usr/bin/env bash
    set -euo pipefail
    pkill -f "go run ./cmd/api" 2>/dev/null || true
    pkill -x "api" 2>/dev/null || true
    pkill -f "node.*vite" 2>/dev/null || true
    sleep 0.5
    trap 'kill 0' EXIT
    just server &
    npm run dev
```

## The Principle

1. Common operations MUST be one-liners (`just dev`, `just test`)
2. MUST be discoverable (run `just` to see all recipes)
3. MUST be documented in project's CLAUDE.md
4. Recipes that background processes MUST clean up on exit and on restart

## When You Create Project CLIs

1. Document them in the project's `.claude/CLAUDE.md`
2. Add to `.claude/rules/` if project-specific patterns emerge

This makes operations repeatable and discoverable.
