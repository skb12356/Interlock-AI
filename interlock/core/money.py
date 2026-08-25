"""Rupee formatting.

The whole objective is denominated in rupees and every rationale line, loss table and
ledger figure is read by an Indian audience, so amounts use the **Indian grouping
convention**: the last three digits, then groups of two. ``1,85,000``, not ``185,000``.

This is not cosmetic. A risk-and-compliance reviewer reading ``Rs.500,000`` has to stop
and convert; ``Rs.5,00,000`` reads at a glance. The policy file is meant to be reviewed
by exactly that person.
"""

from __future__ import annotations

__all__ = ["format_inr", "group_indian"]


def group_indian(value: float, decimals: int = 0) -> str:
    """Group digits in the Indian convention: 3 digits, then 2s.

    ``1234567.5`` -> ``12,34,567.5`` (at ``decimals=1``).
    """
    negative = value < 0
    text = f"{abs(value):.{decimals}f}"
    whole, _, fraction = text.partition(".")

    if len(whole) > 3:
        head, tail = whole[:-3], whole[-3:]
        pieces: list[str] = []
        while len(head) > 2:
            pieces.insert(0, head[-2:])
            head = head[:-2]
        if head:
            pieces.insert(0, head)
        whole = ",".join([*pieces, tail])

    out = f"{whole}.{fraction}" if fraction else whole
    return f"-{out}" if negative else out


def format_inr(value: float, decimals: int = 0) -> str:
    """A rupee amount, ready to display: ``Rs.5,00,000``."""
    return f"Rs.{group_indian(value, decimals)}"
