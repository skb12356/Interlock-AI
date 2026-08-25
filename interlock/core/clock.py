"""Time, and the deadlines that make latency part of the objective.

Latency is a term in the loss function, not a side constraint (CLAUDE.md §3), so
deadlines are first-class objects rather than scattered ``time.monotonic()`` arithmetic.

Two clocks, deliberately distinct:

* ``monotonic_ms`` for **durations** — unaffected by NTP steps or DST, so a measured
  ``overhead_ms`` is never negative and never mysteriously 3600000.
* ``wall_time`` for **timestamps** written to the ledger, which must line up with the
  outside world.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field

__all__ = ["Deadline", "monotonic_ms", "wall_time"]


def monotonic_ms() -> float:
    """Milliseconds from an arbitrary origin. For measuring durations only."""
    return time.monotonic() * 1000.0


def wall_time() -> float:
    """Seconds since the epoch. For timestamps written to the ledger."""
    return time.time()


@dataclass(slots=True)
class Deadline:
    """A budget in milliseconds, counting down from when it was created.

    Every request carries one. Lane A gets 40 ms; the observer call gets what remains.
    The important property is that ``remaining_ms`` can go **negative** and is reported
    honestly rather than clamped -- a caller that overran needs to know by how much, and
    a clamped zero hides exactly the regression you are hunting.
    """

    budget_ms: float
    started_ms: float = field(default_factory=monotonic_ms)

    @property
    def elapsed_ms(self) -> float:
        return monotonic_ms() - self.started_ms

    @property
    def remaining_ms(self) -> float:
        """May be negative when the budget has been overrun."""
        return self.budget_ms - self.elapsed_ms

    @property
    def expired(self) -> bool:
        return self.remaining_ms <= 0.0

    def remaining_seconds(self, floor: float = 0.0) -> float:
        """Remaining budget in seconds, clamped at ``floor`` for ``asyncio.wait_for``.

        ``wait_for`` rejects a negative timeout, so this is the one place clamping is
        correct -- and it is separate from ``remaining_ms`` so measurement stays honest.
        """
        return max(floor, self.remaining_ms / 1000.0)

    def child(self, budget_ms: float) -> Deadline:
        """A sub-budget that can never outlive its parent."""
        return Deadline(budget_ms=min(budget_ms, max(0.0, self.remaining_ms)))
