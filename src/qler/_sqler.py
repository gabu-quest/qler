"""Private helpers that say what sqler's annotations do not."""

from __future__ import annotations

from typing import Any, cast

from sqler import AsyncSQLerQuerySet


async def grouped_count(grouped: AsyncSQLerQuerySet[Any]) -> list[dict[str, Any]]:
    """The rows of a ``group_by(...)`` queryset's ``count()``: one dict per group, the
    number of rows under ``"count"``.

    sqler annotates ``AsyncSQLerQuerySet.count()`` as ``-> int``, but after ``group_by`` it
    returns this list of dicts. This is the one place that cast is made.
    """
    return cast("list[dict[str, Any]]", await grouped.count())
