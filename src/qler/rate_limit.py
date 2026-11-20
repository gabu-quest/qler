"""Rate limiting — token bucket algorithm with refill-on-access."""

from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass
from typing import TYPE_CHECKING

from sqler import F

from qler._time import now_epoch
from qler.exceptions import ConfigurationError

if TYPE_CHECKING:
    from sqler import AsyncSQLerDB

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
        raise ConfigurationError(f"Rate limit must be >= 1, got {limit}")
    unit = match.group(2)
    return RateSpec(limit=limit, window_seconds=_UNIT_SECONDS[unit])


async def try_acquire(key: str, rate: RateSpec, *, db: AsyncSQLerDB) -> bool:
    """Try to take one token from the bucket ``key`` in ``db``. Returns True if allowed.

    The read of the bucket, the refill computed from it and the write of the result are
    one transaction that holds the write lock from before the read (``BEGIN IMMEDIATE``),
    so no other task or process changes the row between the decision and its write: N
    concurrent callers within one window get exactly ``rate.limit`` True answers. The
    first caller of a key inserts its row inside that same transaction.

    The bucket always follows the rate it is given: when the stored ``max_tokens`` or
    ``refill_rate`` differ, the write that takes the token stores the new values, and the
    tokens carried over are refilled for the elapsed time at the new rate and capped at
    the new maximum.

    A clock that steps back refills nothing and never moves ``last_refill_at`` back,
    so it can neither hand out a token twice nor drive the count below zero.

    When the write lock cannot be had within the database's busy timeout, the limiter
    cannot decide and answers False (deny, try later); nothing is written. That applies
    when this call opens the transaction. Called inside a caller's transaction (a claim),
    it is a savepoint under a lock the caller already holds, and a busy database raises
    where the caller began.
    """
    from qler.models.bucket import RateLimitBucket

    began = False
    try:
        async with db.transaction():
            began = True
            return await _take_token(RateLimitBucket, db, key, rate)
    except sqlite3.OperationalError as exc:
        if began or exc.sqlite_errorcode != sqlite3.SQLITE_BUSY:
            raise
        return False


async def _take_token(
    bucket_model: type[RateLimitBucket], db: AsyncSQLerDB, key: str, rate: RateSpec
) -> bool:
    """The decision and its write; the caller holds the write lock."""
    now = now_epoch()
    bucket = await bucket_model.using(db).filter(F("bucket_key") == key).first()

    if bucket is None:
        # First use: a full bucket minus the token this request takes.
        await bucket_model(
            bucket_key=key,
            tokens=float(rate.limit - 1),
            max_tokens=rate.limit,
            refill_rate=rate.refill_rate,
            last_refill_at=now,
        ).save(db=db)
        return True

    # A clock that went back refills nothing and leaves the refill mark where it was.
    elapsed = max(0, now - bucket.last_refill_at)
    new_tokens = min(bucket.tokens + elapsed * rate.refill_rate, float(rate.limit))
    bucket.max_tokens = rate.limit
    bucket.refill_rate = rate.refill_rate
    allowed = new_tokens >= 1.0
    bucket.tokens = new_tokens - 1.0 if allowed else new_tokens
    bucket.last_refill_at = max(now, bucket.last_refill_at)
    await bucket.save(db=db)
    return allowed
