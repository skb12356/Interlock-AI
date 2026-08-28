"""Identifiers and content digests.

Two jobs, both load-bearing:

* **Prefixed, sortable ids.** Every id carries its type as a prefix (``req_``, ``dec_``)
  so a stray id in a log or a trace is self-describing. They sort lexicographically by
  creation time, which makes ``ORDER BY`` on an id column do the obvious thing.
* **Content digests.** ``inputs_digest`` is what makes a decision replayable bit-for-bit
  (F9), and ``context_key`` is what the observer caches its KV prefix under — get that
  key wrong and every sentence pays full prefill.
"""

from __future__ import annotations

import hashlib
import json
import os
import time
from collections.abc import Iterable
from typing import Any

__all__ = [
    "context_key",
    "inputs_digest",
    "new_decision_id",
    "new_hold_id",
    "new_id",
    "new_request_id",
    "new_span_id",
    "new_stakes_id",
    "new_tool_call_id",
    "new_trace_id",
    "sha256_text",
]

# Crockford base32, minus the letters that look like digits. Case-insensitive and
# safe to read aloud off a screen during a demo.
_ALPHABET = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"
_RANDOM_CHARS = 16


def _encode(value: int, length: int) -> str:
    out = []
    for _ in range(length):
        value, remainder = divmod(value, 32)
        out.append(_ALPHABET[remainder])
    return "".join(reversed(out))


def new_id(prefix: str) -> str:
    """A prefixed, time-sortable identifier.

    The first 10 characters encode milliseconds since the epoch, so ids generated in
    order sort in order; the remaining characters are random, so concurrent generation
    does not collide.
    """
    stamp = _encode(time.time_ns() // 1_000_000, 10)
    entropy = _encode(int.from_bytes(os.urandom(10), "big"), _RANDOM_CHARS)
    return f"{prefix}_{stamp}{entropy}"


def new_request_id() -> str:
    return new_id("req")


def new_trace_id() -> str:
    return new_id("trc")


def new_span_id() -> str:
    return new_id("spn")


def new_decision_id() -> str:
    return new_id("dec")


def new_hold_id() -> str:
    return new_id("hld")


def new_stakes_id() -> str:
    """The id that ties the router and the risk engine to one estimate.

    Contribution 1 is only credible if it is provable from a trace, and this is what
    makes it provable: both consumers record the same ``stakes_id``.
    """
    return new_id("stk")


def new_tool_call_id() -> str:
    return new_id("tc")


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _canonical(payload: Any) -> str:
    """Stable JSON: sorted keys, no incidental whitespace.

    Digests must not change because a dict happened to be built in a different order.
    """
    return json.dumps(
        payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str
    )


def inputs_digest(payload: Any) -> str:
    """The digest stamped on a decision, so it can be replayed bit-for-bit (F9).

    ``interlock replay <decision_id>`` recomputes this over the stored inputs; a
    mismatch means the inputs were not what we recorded, which is exactly the thing an
    auditor is asking about.
    """
    return f"sha256:{sha256_text(_canonical(payload))}"


def _fragment_material(fragment: Any) -> list[str]:
    """Reduce one fragment to the (provenance, text) pair the key is built from.

    Accepts a pydantic ``Fragment``, a plain mapping, or a bare string, because the key
    is computed in three different places -- the gateway, the observer's own cache, and
    the eval harness -- and they do not all hold the same representation.
    """
    if isinstance(fragment, str):
        return ["", fragment]
    if isinstance(fragment, dict):
        return [str(fragment.get("provenance", "")), str(fragment.get("text", ""))]
    return [str(getattr(fragment, "provenance", "")), str(getattr(fragment, "text", ""))]


def context_key(fragments: Iterable[Any]) -> str:
    """The observer's KV-prefix cache key.

    Keyed on the *content* of the context, not on the request, so successive sentences
    of one answer -- and separate requests that happen to share retrieval -- both hit
    the same warm prefix. Provenance is part of the key because a fragment that changed
    trust level is a different fragment as far as the probe is concerned.
    """
    material = [_fragment_material(fragment) for fragment in fragments]
    return f"sha256:{sha256_text(_canonical(material))}"
