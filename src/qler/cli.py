"""qler CLI entry point."""

import click


@click.group()
@click.version_option(package_name="qler")
def cli():
    """qler — Background jobs without Redis."""
