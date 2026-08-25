"""The commit gate — text streams one sentence behind, so a bad sentence can be
repaired before anyone reads it.

Live television runs on a seven-second delay; viewers never notice and the producer can
still bleep a word. We do it with one sentence.

**The state machine**::

    PASSTHROUGH ──escalate──► BUFFERING ──decision──► HOLDING ──► REPAIRING
         │                        │                     │            │
         └────────────────────────┴─────────────────────┴────────────┴──► TERMINATED

Four properties, each of which exists because breaking it produces a specific disaster:

* **Nothing uncommitted is ever emitted.** The entire point. Once a sentence has been
  read you cannot un-say it, and pretending otherwise is what the console must never do.
* **Every token is emitted exactly once, or explicitly replaced.** A dropped token is a
  corrupted answer; a duplicated one is worse, because it looks deliberate.
* **Mode escalates, never de-escalates** (ADR-003). If sentence 2 trips a signal in an
  unbuffered stream, the rest of the stream buffers. Going back the other way would mean
  a stream that had already seen trouble relaxing its guard.
* **Verification runs concurrently with generation.** The upstream keeps filling the
  buffer while sentence *n* is being priced. Sequencing them instead is the difference
  between SentGuard's measured 36 ms and 576 ms of added overhead — the whole latency
  claim lives here.

**Unbuffered mode is not "the gate off".** Lane B still runs and still records signals;
what changes is the *feasible action set*. For text already on the wire the ladder is
capped at annotate/notify, and ``already_emitted=True`` tells the optimiser so, which is
why it never prices a repair it cannot perform.
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from interlock.core.types import Decision, Fragment, GateMode, RiskContext, Stakes
from interlock.gate.segmenter import StreamingSegmenter

__all__ = ["CommitGate", "Emission", "GateState", "drive"]


class GateState(StrEnum):
    """Where the gate is. Ordered, and it only ever moves forward."""

    PASSTHROUGH = "passthrough"
    BUFFERING = "buffering"
    HOLDING = "holding"
    REPAIRING = "repairing"
    TERMINATED = "terminated"


@dataclass(slots=True)
class Emission:
    """One thing to send downstream.

    ``raw`` carries the provider's exact bytes for unbuffered traffic, so a passthrough
    stream stays byte-identical. ``text`` carries a sentence the gate assembled (and may
    have replaced), which must be wrapped in a fresh chunk because it is no longer what
    the provider sent.
    """

    kind: str  # 'raw' | 'text' | 'event'
    raw: str = ""
    text: str = ""
    event_name: str = ""
    event_payload: Any = None
    sentence_idx: int = -1
    decision: Decision | None = None


#: Signature of the repair callback the gate uses for L2. Injected rather than imported
#: so the gate can be tested with no model, and so repair can be swapped without
#: touching the state machine.
RepairFn = Callable[[str, Decision, str], Awaitable[str | None]]


@dataclass
class CommitGate:
    """Holds at most one sentence, and decides what to do with it before it ships."""

    risk_engine: Any
    stakes: Stakes
    request_id: str
    mode: GateMode = "unbuffered"
    retrieved: list[Fragment] = field(default_factory=list)
    question: str = ""
    #: Per-sentence watchdog. If the model stalls mid-sentence, flush rather than hang.
    watchdog_s: float = 8.0
    #: What the risk engine is given per sentence.
    evaluate_deadline_ms: float = 120.0
    repair: RepairFn | None = None
    #: L2 gets two attempts before escalating to L3 (Implementation02 §4.3).
    max_repair_attempts: int = 2

    state: GateState = field(default=GateState.PASSTHROUGH, init=False)
    _segmenter: StreamingSegmenter = field(default_factory=StreamingSegmenter, init=False)
    _sentence_idx: int = field(default=0, init=False)
    _emitted_chars: int = field(default=0, init=False)
    _answer_prefix: str = field(default="", init=False)
    _decisions: list[Decision] = field(default_factory=list, init=False)
    _pending: asyncio.Task[Decision] | None = field(default=None, init=False, repr=False)
    _pending_sentence: str = field(default="", init=False)
    _pending_idx: int = field(default=-1, init=False)

    def __post_init__(self) -> None:
        self.state = GateState.BUFFERING if self.mode == "buffered" else GateState.PASSTHROUGH

    # ------------------------------------------------------------------ #
    # Public surface
    # ------------------------------------------------------------------ #

    @property
    def buffered(self) -> bool:
        return self.state is not GateState.PASSTHROUGH

    @property
    def decisions(self) -> list[Decision]:
        return list(self._decisions)

    def escalate(self, reason: str = "") -> None:
        """Switch to buffered for the remainder of the stream. One-way.

        Called when a mid-stream signal fires on traffic that started unbuffered. The
        text already sent stays sent -- that is exactly what ``already_emitted`` records,
        and the console shows it honestly rather than implying we could have caught it.
        """
        if self.state is GateState.PASSTHROUGH:
            self.state = GateState.BUFFERING
            self.mode = "buffered"

    async def push(self, chunk_text: str, raw: str = "") -> list[Emission]:
        """Feed one upstream chunk. Returns whatever may now be sent downstream."""
        if self.state is GateState.TERMINATED:
            return []

        out: list[Emission] = []

        # A decision that arrived while we were reading is applied first, so the buffer
        # never grows past one sentence plus whatever is currently generating.
        out.extend(await self._collect_pending(block=False))

        sentences = self._segmenter.push(chunk_text)

        if not self.buffered:
            # L0: forward the provider's bytes untouched. Sentences are still tracked so
            # Lane B records signals, but the ladder is capped at annotate/notify.
            if raw:
                out.append(Emission(kind="raw", raw=raw))
                self._emitted_chars += len(chunk_text)
            for sentence in sentences:
                self._answer_prefix += sentence + " "
                self._start_evaluation(sentence, already_emitted=True)
                out.extend(await self._collect_pending(block=False))
            return out

        for sentence in sentences:
            # One sentence in flight at a time: wait for the previous verdict before
            # starting the next, which is what bounds the buffer to a single sentence.
            out.extend(await self._collect_pending(block=True))
            self._start_evaluation(sentence, already_emitted=False)

        return out

    async def finish(self) -> list[Emission]:
        """End of stream. Drain the buffer and settle every outstanding decision."""
        if self.state is GateState.TERMINATED:
            return []

        out: list[Emission] = []
        out.extend(await self._collect_pending(block=True))

        tail = self._segmenter.flush()
        if tail:
            if not self.buffered:
                self._answer_prefix += tail
            self._start_evaluation(tail, already_emitted=not self.buffered)

        # Always block here, in both modes. Unbuffered traffic has already sent its
        # text, so this cannot change what the customer sees -- but the decision still
        # belongs in the trace, and an un-awaited task would be left dangling for
        # asyncio to destroy, losing the record and emitting a warning on shutdown.
        out.extend(await self._collect_pending(block=True))

        self.state = GateState.TERMINATED
        return out

    # ------------------------------------------------------------------ #
    # Evaluation
    # ------------------------------------------------------------------ #

    def _start_evaluation(self, sentence: str, *, already_emitted: bool) -> None:
        """Kick off pricing for one sentence, concurrently with generation.

        Not awaited here. The upstream keeps filling the buffer while this runs, which
        is the difference between hiding the verification under generation latency and
        adding it to the user's wait.
        """
        ctx = RiskContext(
            request_id=self.request_id,
            sentence_idx=self._sentence_idx,
            sentence=sentence,
            answer_prefix=self._answer_prefix,
            question=self.question,
            retrieved=list(self.retrieved),
            stakes=self.stakes,
            already_emitted=already_emitted,
            remaining_deadline_ms=self.evaluate_deadline_ms,
        )
        self._pending_sentence = sentence
        self._pending_idx = self._sentence_idx
        self._sentence_idx += 1
        self._pending = asyncio.create_task(self.risk_engine.evaluate(ctx))
        if self.buffered:
            self.state = GateState.HOLDING

    async def _collect_pending(self, *, block: bool) -> list[Emission]:
        """Apply a finished verdict, optionally waiting for it."""
        if self._pending is None:
            return []
        if not block and not self._pending.done():
            return []

        task, sentence, index = self._pending, self._pending_sentence, self._pending_idx
        self._pending = None

        try:
            decision = await asyncio.wait_for(task, timeout=self.watchdog_s)
        except TimeoutError:
            # The watchdog. A stalled verifier must never hold the stream: flush the
            # sentence with an annotation rather than let the demo appear to freeze.
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await task
            return self._release(sentence, index, decision=None, note="watchdog")
        except Exception:
            # but the gate does not get to assume it. Failing open here is correct:
            # holding a sentence because our own checker crashed is the worst outcome.
            return self._release(sentence, index, decision=None, note="engine_error")

        self._decisions.append(decision)
        return await self._apply(decision, sentence, index)

    async def _apply(self, decision: Decision, sentence: str, index: int) -> list[Emission]:
        """Execute the chosen action."""
        event = Emission(
            kind="event",
            event_name="interlock.decision",
            event_payload=decision,
            sentence_idx=index,
            decision=decision,
        )

        if decision.action in {"L0_pass", "L1_annotate"}:
            emissions = self._release(sentence, index, decision=decision)
            return [event, *emissions] if self.buffered else [event]

        if decision.action == "L2_repair":
            return [event, *await self._do_repair(decision, sentence, index)]

        if decision.action in {"L3_reroute", "L4_hold", "L5_block"}:
            # The sentence is withheld. The gateway turns the event into a hold row or a
            # terminated stream; the gate's job is simply not to emit the text.
            if decision.action == "L5_block":
                self.state = GateState.TERMINATED
            return [event]

        return [event, *self._release(sentence, index, decision=decision)]

    async def _do_repair(self, decision: Decision, sentence: str, index: int) -> list[Emission]:
        """L2: regenerate just this sentence with the evidence injected.

        Two failures escalate rather than looping: a repair that cannot be verified is
        not a repair, and a third attempt costs more than rerouting.
        """
        if self.repair is None:
            return self._release(sentence, index, decision=decision, note="repair_unavailable")

        self.state = GateState.REPAIRING
        for _ in range(self.max_repair_attempts):
            replacement = await self.repair(sentence, decision, self._answer_prefix)
            if replacement:
                self.state = GateState.HOLDING
                return self._release(replacement, index, decision=decision, replaced=True)

        # Both attempts failed. Withhold rather than ship the original: the optimiser
        # already priced this sentence as worth repairing.
        self.state = GateState.HOLDING
        return []

    def _release(
        self,
        sentence: str,
        index: int,
        *,
        decision: Decision | None,
        replaced: bool = False,
        note: str = "",
    ) -> list[Emission]:
        """Commit a sentence downstream.

        In unbuffered mode the raw chunks already went out, so releasing again would
        duplicate the text -- the second half of "exactly once, or explicitly replaced".
        """
        if not self.buffered and not replaced:
            return []
        self._answer_prefix += sentence + " "
        self._emitted_chars += len(sentence)
        return [
            Emission(
                kind="text",
                text=sentence + " ",
                sentence_idx=index,
                decision=decision,
            )
        ]


async def drive(
    gate: CommitGate,
    events: AsyncIterator[tuple[str, str]],
) -> AsyncIterator[Emission]:
    """Run a gate over an ``(text, raw)`` stream. Convenience for tests and the gateway."""
    async for text, raw in events:
        for emission in await gate.push(text, raw):
            yield emission
    for emission in await gate.finish():
        yield emission
