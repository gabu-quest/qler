---
description: "Python tooling — use uv for all Python operations, never raw python/pip"
paths:
  - "**/*.py"
  - "pyproject.toml"
  - "setup.py"
  - "requirements*.txt"
---

# Python Tooling

Use `uv` for ALL Python operations. Never raw `python`, `pip`, or `pip3`.

- `uv run pytest` not `python -m pytest`
- `uv add package` not `pip install`
- `uv sync` not `pip install -r requirements.txt`

If a project lacks `pyproject.toml`, run `uv init` first.
