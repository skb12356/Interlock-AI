"""Typed errors.

The distinction that matters here is **who is allowed to raise on the token path**.

Almost nothing is. The risk engine never raises (Contract 1). The observer never
returns an error status (Contract 2). Lane A detectors that overrun are dropped, not
awaited. So these exceptions are mostly for boot-time and configuration failures — the
places where failing loudly is correct — plus a small set the gateway catches and
converts into a typed error the client can act on.
"""

from __future__ import annotations

__all__ = [
    "ConfigError",
    "ContractViolation",
    "DeadlineExceeded",
    "InterlockError",
    "PolicyError",
    "ProviderError",
    "UpstreamError",
]


class InterlockError(Exception):
    """Base class. Catch this to catch anything of ours."""


class ConfigError(InterlockError):
    """Bad or missing configuration. Raised at boot; never on a request."""


class PolicyError(ConfigError):
    """A policy file failed schema validation.

    Refuse to start rather than run on a policy nobody validated. On a *hot reload*
    failure the previous version stays live, because a running system on a known-good
    policy beats a stopped system on a correct one.
    """


class ContractViolation(InterlockError):
    """One of the five frozen contracts was breached.

    Raised where the breach happens rather than allowed to surface three modules later
    as something inscrutable -- for example, an SSE event name the console cannot render.
    """


class DeadlineExceeded(InterlockError):
    """A budget was overrun.

    Note this is *not* how Lane A handles a slow detector: that path drops the detector
    and records its absence as a signal with ``prob=None``, because a missing signal is
    information and an exception is not.
    """


class UpstreamError(InterlockError):
    """The upstream model provider failed after the retry budget was spent.

    Carries the status so the gateway can map it back to an OpenAI-shaped error body;
    the cost of the failed attempt is still charged to the ledger.
    """

    def __init__(self, message: str, *, status: int | None = None, provider: str | None = None):
        super().__init__(message)
        self.status = status
        self.provider = provider


class ProviderError(ConfigError):
    """No such provider, or a provider missing the credentials it needs."""
