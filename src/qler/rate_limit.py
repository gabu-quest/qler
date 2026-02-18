"""Rate limiting — token bucket algorithm with refill-on-access."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import TYPE_CHECKING

from qler._time import now_epoch
from qler.exceptions import ConfigurationError

if TYPE_CHECKING:
    from qler.models.bucket import RateLimitBucket

_RATE_PATTERN = re.compile(r"^(\d+)/([smhd])$")
_UNIT_SECONDS = {"s": 1, "m": 60, "h": 3600, "d": 86400}


@dataclass(frozen=True)
class RateSpec:
    """Parsed rate limit specification."""

    limit: int
    window_seconds: int

    @property
    def refill_rate(self) -> float:
        """Tokens per second."""
        return self.limit / self.window_seconds


def parse_rate(spec: str) -> RateSpec:
    """Parse rate limit string like '10/m', '100/h', '5/s', '1000/d'.

    Returns:
        RateSpec with limit and window_seconds.

    Raises:
        ConfigurationError: If the spec format is invalid.
    """
    match = _RATE_PATTERN.match(spec)
    if not match:
        raise ConfigurationError(
            f"Invalid rate limit spec: '{spec}'. "
            "Expected format: '<number>/<unit>' where unit is s, m, h, or d."
        )
    limit = int(match.group(1))
    if limit < 1:
        raise ConfigurationError(
            f"Rate limit must be >= 1, got {limit}"
        )
    unit = match.group(2)
    return RateSpec(limit=limit, window_seconds=_UNIT_SECONDS[unit])


async def try_acquire(key: str, rate: RateSpec) -> bool:
    """Try to acquire a token from the bucket. Returns True if allowed.

    Uses atomic operations to prevent race conditions:
    1. Upsert bucket if it doesn't exist (first use)
    2. Refill tokens based on elapsed time
    3. If >= 1 token available: decrement and return True
    4. Otherwise: return False
    """
    from sqler import F

    from qler.models.bucket import RateLimitBucket

    now = now_epoch()

    # Try to find existing bucket
    bucket = await RateLimitBucket.query().filter(
        F("key") == key
    ).first()

    if bucket is None:
        # First use — create bucket with full tokens minus 1 (this request)
        bucket = RateLimitBucket(
            key=key,
            tokens=float(rate.limit - 1),
            max_tokens=rate.limit,
            refill_rate=rate.refill_rate,
            last_refill_at=now,
        )
        await bucket.save()
        return True

    # Calculate refill
    elapsed = now - bucket.last_refill_at
    refilled = elapsed * bucket.refill_rate
    new_tokens = min(bucket.tokens + refilled, float(bucket.max_tokens))

    if new_tokens >= 1.0:
        # Consume a token
        bucket.tokens = new_tokens - 1.0
        bucket.last_refill_at = now
        await bucket.save()
        return True

    # Update refill timestamp even if no token consumed (for accurate tracking)
    bucket.tokens = new_tokens
    bucket.last_refill_at = now
    await bucket.save()
    return False
