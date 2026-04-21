"""RateLimitBucket model — token bucket state for rate limiting."""

from __future__ import annotations

from typing import ClassVar

from pydantic import Field
from sqler import NO_REBASE_CONFIG, AsyncSQLerSafeModel, RebaseConfig


class RateLimitBucket(AsyncSQLerSafeModel):
    """Token bucket state for rate limiting.

    Each bucket tracks tokens for a specific rate limit key
    (e.g., "task:myapp.tasks.send_email" or "queue:emails").
    """

    __promoted__: ClassVar[dict[str, str]] = {
        "bucket_key": "TEXT NOT NULL",
    }
    __checks__: ClassVar[dict[str, str]] = {}
    _rebase_config: ClassVar[RebaseConfig] = NO_REBASE_CONFIG

    bucket_key: str = Field(default="", alias="key")
    tokens: float = 0.0
    max_tokens: int = 10
    refill_rate: float = 0.167  # tokens per second
    last_refill_at: int = 0

    model_config = {
        "extra": "ignore",
        "frozen": False,
        "populate_by_name": True,
    }

    @property
    def key(self) -> str:
        """Backward-compatible alias for the renamed promoted field."""
        return self.bucket_key
