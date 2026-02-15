# Using The Standard as a GitHub Template

**The Standard** is designed to be used as a template repository. This guide explains how to use it effectively.

---

## What You Get

When you create a new repository from this template, you get:

### 📚 Complete Engineering Doctrine
- `agents.md` - Core agent operating rules
- `docs/testing.md` - Non-negotiable testing standards
- `docs/doctrine/` - All specialized doctrines (git, CI, security, style, handoff)

### 🎨 Design Systems (Vue 3 + Naive UI)
- See **[The Style](https://github.com/gabu-quest/the-style)** - Separate repository with Goshuin & Cyberpunk variants

### 🛠️ Claude Skills
- `.claude/skills/` - Reusable skills for code review, design, testing, handoffs

### 📋 GitHub Templates
- `.github/ISSUE_TEMPLATE/` - Issue templates enforcing standards
- `.github/PULL_REQUEST_TEMPLATE.md` - PR template with quality checklist

### 📖 Examples & Resources
- `examples/` - Reference implementations and workflows
- `adoption/CHECKLIST.md` - Step-by-step adoption guide
- `docs/quick-reference/` - One-page summaries

---

## How to Use This Template

### Option 1: Create Repository from Template (Recommended)

1. **Go to the template repo:** https://github.com/gabu-quest/the-standard
2. **Click "Use this template"** (green button)
3. **Create your new repository:**
   - Choose a name for your project
   - Set visibility (public/private)
   - Click "Create repository from template"

4. **Clone your new repo:**
   ```bash
   git clone https://github.com/your-username/your-project.git
   cd your-project
   ```

5. **Customize for your project:**
   - Update `README.md` with your project description
   - For Vue 3 UI, add [The Style](https://github.com/gabu-quest/the-style) design systems
   - Adjust doctrine documents if you have project-specific requirements
   - Configure the Claude skills paths if needed

### Option 2: Manual Copy

If you prefer to copy specific parts:

```bash
# Core doctrine only
cp the-standard/agents.md your-project/
cp the-standard/docs/testing.md your-project/docs/

# Full doctrine suite
cp -r the-standard/docs/doctrine your-project/docs/

# Claude skills
cp -r the-standard/.claude/skills your-project/.claude/

# GitHub templates
cp -r the-standard/.github your-project/

# Adoption resources
cp -r the-standard/adoption your-project/adoption
```

---

## First Steps After Creating Your Repo

### 1. Update Your README

Replace the template README with your project description, but keep references to The Standard:

```markdown
# Your Project Name

[Your project description]

## Engineering Standards

This project follows **The Standard** - see [agents.md](./agents.md) for core doctrine.

Key standards:
- [Testing Doctrine](./docs/testing.md)
- [Code Style Guide](./docs/doctrine/style.md)
- [Security Baseline](./docs/doctrine/security.md)
- [Git Workflow](./docs/doctrine/git.md)
```

### 2. Add Design System (Vue 3 Projects)

If building a Vue 3 app, see **[The Style](https://github.com/gabu-quest/the-style)** for design systems.

**Not using Vue?** No action needed - design systems are now in a separate repository.

### 3. Configure Your Tech Stack

Update `README.md` with your specific tech choices (within The Standard's constraints):

**Python Projects:**
- Python 3.12+
- `uv` for dependencies
- FastAPI for APIs
- pytest for testing

**Frontend Projects:**
- Vue 3 + Composition API
- Pinia for state
- Vitest for unit tests
- Playwright for E2E tests

### 4. Set Up Your Environment

```bash
# Python
uv venv
source .venv/bin/activate  # or `.venv\Scripts\activate` on Windows
uv pip install -e ".[dev]"

# Frontend
npm install
# or
pnpm install
```

### 5. Review and Customize Doctrine

The doctrine documents are opinionated but adaptable:

- **Keep as-is** for maximum consistency with The Standard
- **Extend** by adding project-specific sections
- **Override sparingly** - document why if you deviate

If you override a doctrine rule, document it:

```markdown
## Project-Specific Overrides

### Testing Doctrine
**Override:** We use mocks for external payment APIs
**Rationale:** Stripe/PayPal rate limits make realistic tests impractical
**Scope:** Only for `tests/test_payments.py`
```

### 6. Configure Claude for Your Project

Create or update `.claude/claude.json`:

```json
{
  "name": "your-project",
  "description": "Your project description",
  "version": "1.0.0",
  "doctrines": [
    "agents.md",
    "docs/testing.md"
  ],
  "skills": [
    ".claude/skills/standard-review.md",
    ".claude/skills/standard-design.md",
    ".claude/skills/standard-test.md",
    ".claude/skills/standard-handoff.md"
  ]
}
```

---

## What to Keep vs Remove

### Always Keep
- ✅ `agents.md` - Core agent doctrine
- ✅ `docs/testing.md` - Testing standards
- ✅ `docs/doctrine/` - All specialized doctrines
- ✅ `.claude/skills/` - Claude skills
- ✅ `.github/` - Issue/PR templates
- ✅ `LICENSE` - MIT license

### Decide Based on Your Project
- ⚠️ `examples/` - Helpful for reference, but not required
- ⚠️ `adoption/CHECKLIST.md` - Useful for team adoption, remove if solo

### Should Remove/Replace
- ❌ `TEMPLATE_USAGE.md` - This file (it's template-specific)
- ❌ Template `README.md` - Replace with your project's README
- ❌ Template `CHANGELOG.md` - Start your own changelog

---

## Adoption Checklist

Follow the [Adoption Checklist](./adoption/CHECKLIST.md) for a phased approach to implementing The Standard in your project.

**Quick version:**
1. ✅ Copy core doctrine (agents.md, testing.md)
2. ✅ Set up project structure
3. ✅ Configure CI/CD with quality gates
4. ✅ Train team on the standards
5. ✅ Integrate Claude skills
6. ✅ Enforce via PR templates
7. ✅ Regular audits and improvements

---

## Customization Guidelines

### When to Customize

You SHOULD customize when:
- Your domain has specific compliance requirements (HIPAA, PCI-DSS, etc.)
- Your tech stack differs (e.g., Go instead of Python)
- Your team has strong existing conventions
- Your project has unique constraints

You SHOULD NOT customize to:
- ❌ Lower quality standards
- ❌ Skip testing requirements
- ❌ Avoid documentation
- ❌ Bypass security baselines

### How to Customize

1. **Document the override** in a `CUSTOMIZATIONS.md` file
2. **Provide rationale** for each deviation
3. **Maintain the spirit** of The Standard (quality, clarity, execution)
4. **Keep it minimal** - most projects don't need major changes

Example `CUSTOMIZATIONS.md`:

```markdown
# Project Customizations to The Standard

## Overview
This project follows The Standard with the following documented deviations.

## Language-Specific Changes

### Go Instead of Python
**Rationale:** Existing team expertise, performance requirements
**Impact:**
- Replace pytest with Go testing package
- Use Go idioms for error handling
- Maintain same testing doctrine (realistic, deterministic, documentary)

## Domain-Specific Requirements

### HIPAA Compliance
**Rationale:** Healthcare data requires additional security measures
**Impact:**
- Enhanced security.md with PHI handling rules
- Audit logging for all data access
- Encrypted backups with retention policies

## Team Conventions

### Commit Message Format
**Rationale:** Team uses Jira integration
**Impact:** Commit messages include `[PROJ-123]` ticket prefix
**Compatibility:** Still follows conventional commits, just adds prefix
```

---

## Getting Help

### Documentation
- [Main README](./README.md) - Overview of The Standard
- [CLAUDE.md](./CLAUDE.md) - Context for AI agents
- [Adoption Checklist](./adoption/CHECKLIST.md) - Step-by-step guide

### Community
- [GitHub Issues](https://github.com/gabu-quest/the-standard/issues) - Report bugs or suggest improvements
- [GitHub Discussions](https://github.com/gabu-quest/the-standard/discussions) - Ask questions, share experiences

### Contributing Back
Found a bug or improvement? Submit a PR to the template repository to help others!

---

## Success Criteria

You'll know The Standard is working when:

- ✅ PRs are consistently high quality
- ✅ Tests catch bugs before production
- ✅ New team members onboard quickly
- ✅ Fewer "how should we do this?" debates
- ✅ Code reviews focus on logic, not formatting
- ✅ AI agents produce production-ready code
- ✅ Technical debt stays low

---

## Version Compatibility

This template follows semantic versioning:

- **MAJOR** versions: Breaking changes to core doctrine
- **MINOR** versions: New doctrines or significant additions
- **PATCH** versions: Clarifications and fixes

**Current Template Version:** 1.0.0

When the template is updated:
- **PATCH updates:** Safe to cherry-pick improvements
- **MINOR updates:** Review new features, adopt if relevant
- **MAJOR updates:** Careful migration, may require project changes

Track the template version you started with in your README:

```markdown
## Standards
Based on [The Standard](https://github.com/gabu-quest/the-standard) v1.0.0
```

---

## FAQ

### Do I have to use everything?

No. Start with `agents.md` and `docs/testing.md`, then adopt other doctrines as needed.

### Can I modify the doctrine documents?

Yes, but document your changes in `CUSTOMIZATIONS.md` and maintain the spirit of quality and clarity.

### What if I'm not using Python or Vue?

The principles apply to any language. Adapt the tech-specific examples (FastAPI, pytest, etc.) to your stack.

### Do I need design systems?

If building a Vue 3 app, see **[The Style](https://github.com/gabu-quest/the-style)** - a separate repository.

### How do I keep up with template updates?

Watch the template repo for releases. Cherry-pick improvements or periodically sync major updates.

### Can I use this for commercial projects?

Yes! The Standard is MIT licensed. Use it freely in any project.

---

**Ready to build something great? Start with the [Adoption Checklist](./adoption/CHECKLIST.md).**
