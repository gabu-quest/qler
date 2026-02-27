"""Deterministic payload generator for qler benchmarks."""

from __future__ import annotations

import random
import string


class PayloadGenerator:
    """Generates deterministic job payloads at various size profiles.

    All generators use a seeded random.Random for reproducibility.
    Returns (args, kwargs) tuples ready for task.enqueue(*args, **kwargs).
    """

    def __init__(self, seed: int = 42):
        self.rng = random.Random(seed)

    def generate(self, profile: str, count: int) -> list[tuple[tuple, dict]]:
        """Generate `count` payloads at the given size profile.

        Returns list of (args, kwargs) tuples.
        """
        factory = {
            "tiny": self._tiny,
            "small": self._small,
            "medium": self._medium,
            "large": self._large,
        }.get(profile)
        if factory is None:
            raise ValueError(f"Unknown profile: {profile!r} (choose from tiny/small/medium/large)")
        return [factory(i) for i in range(count)]

    def _random_string(self, length: int) -> str:
        return "".join(self.rng.choices(string.ascii_letters + string.digits, k=length))

    def _tiny(self, index: int) -> tuple[tuple, dict]:
        """~20B payload — just a counter."""
        return (index,), {}

    def _small(self, index: int) -> tuple[tuple, dict]:
        """~200B payload — order-like dict."""
        return (), {
            "order_id": f"ORD-{index:06d}",
            "customer": self._random_string(12),
            "amount": round(self.rng.uniform(1.0, 999.99), 2),
            "items": [
                {"sku": self._random_string(8), "qty": self.rng.randint(1, 10)}
                for _ in range(self.rng.randint(1, 3))
            ],
        }

    def _medium(self, index: int) -> tuple[tuple, dict]:
        """~1KB payload — order with metadata and description."""
        _, kwargs = self._small(index)
        kwargs["metadata"] = {
            "source": self.rng.choice(["web", "api", "mobile", "batch"]),
            "region": self.rng.choice(["us-east", "us-west", "eu-west", "ap-south"]),
            "priority": self.rng.choice(["low", "normal", "high"]),
            "tags": [self._random_string(6) for _ in range(self.rng.randint(2, 5))],
            "timestamp": 1700000000 + index,
        }
        kwargs["description"] = self._random_string(500)
        return (), kwargs

    def _large(self, index: int) -> tuple[tuple, dict]:
        """~5KB payload — medium with nested lists/dicts."""
        _, kwargs = self._medium(index)
        kwargs["line_items"] = [
            {
                "sku": self._random_string(10),
                "name": self._random_string(30),
                "qty": self.rng.randint(1, 100),
                "unit_price": round(self.rng.uniform(0.5, 500.0), 2),
                "attributes": {
                    self._random_string(6): self._random_string(20)
                    for _ in range(self.rng.randint(3, 8))
                },
            }
            for _ in range(self.rng.randint(5, 15))
        ]
        kwargs["audit_trail"] = [
            {
                "action": self.rng.choice(["created", "updated", "reviewed", "approved"]),
                "actor": self._random_string(10),
                "timestamp": 1700000000 + index + i,
                "note": self._random_string(80),
            }
            for i in range(self.rng.randint(3, 8))
        ]
        return (), kwargs
