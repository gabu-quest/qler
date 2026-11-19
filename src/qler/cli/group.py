"""The qler command group."""

from __future__ import annotations

import click

# ---------------------------------------------------------------------------
# CLI Group
# ---------------------------------------------------------------------------

@click.group(name="qler")
@click.version_option(package_name="qler")
def cli():
    """qler — Background jobs without Redis."""
