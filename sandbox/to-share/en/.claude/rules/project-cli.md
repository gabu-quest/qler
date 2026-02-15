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

## The Principle

1. Common operations MUST be one-liners (`just dev`, `just test`)
2. MUST be discoverable (run `just` to see all recipes)
3. MUST be documented in project's CLAUDE.md

## Installation

```bash
# macOS
brew install just

# Cross-platform (Rust)
cargo install just

# Windows
winget install Casey.Just

# Ubuntu/Debian
sudo apt install just
```

## When You Create Project CLIs

1. Document them in the project's `.claude/CLAUDE.md`
2. Add to `.claude/rules/` if project-specific patterns emerge

This makes operations repeatable and discoverable.
