"""Provider adapters — one interface, any upstream model.

Model-agnosticism is the moat (invariant 7), and this file is where it is cashed out:
the rest of the system never learns which provider answered. Integration for the
customer stays one line of config — point ``base_url`` at us.

Three adapters:

* **Ollama** — the default. OpenAI-compatible, local, keyless, no rate limit. Two models
  give a genuine two-tier router.
* **OpenAI** — the same wire format against a different host and an API key.
* **Anthropic** — a different request shape *and* a different SSE format, translated in
  both directions. See the honesty note on ``AnthropicProvider``.

Streaming yields ``StreamEvent`` rather than raw bytes because the commit gate needs the
parsed delta to segment sentences, while passthrough needs the original text byte-for-byte.
Carrying both means we never re-serialise a provider's JSON just to forward it.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Mapping
from dataclasses import dataclass
from typing import Any, Protocol

import httpx

from interlock.core.errors import ProviderError, UpstreamError

__all__ = [
    "AnthropicProvider",
    "OpenAICompatProvider",
    "Provider",
    "StreamEvent",
    "build_providers",
    "delta_text",
]


@dataclass(slots=True)
class StreamEvent:
    """One upstream SSE payload.

    ``raw`` is the exact text the provider sent between ``data: `` and the newline, so
    passthrough can forward it byte-for-byte. ``data`` is the same thing parsed, for the
    segmenter and the gate.
    """

    raw: str
    data: dict[str, Any] | None = None
    is_done: bool = False

    @property
    def text(self) -> str:
        """The content delta this event carries, or an empty string."""
        return delta_text(self.data) if self.data else ""


def delta_text(chunk: Mapping[str, Any] | None) -> str:
    """Pull the content delta out of an OpenAI-shaped streaming chunk.

    Tolerant by design: a provider that omits ``choices``, sends an empty delta, or
    sends a role-only opening chunk must not break the gate.
    """
    if not chunk:
        return ""
    choices = chunk.get("choices")
    if not isinstance(choices, list) or not choices:
        return ""
    delta = choices[0].get("delta")
    if not isinstance(delta, dict):
        # Non-streaming shape, which some providers use for the final chunk.
        message = choices[0].get("message")
        if isinstance(message, dict):
            return str(message.get("content") or "")
        return ""
    return str(delta.get("content") or "")


class Provider(Protocol):
    """What the gateway needs from an upstream, and nothing more."""

    name: str

    async def stream(self, body: dict[str, Any]) -> AsyncIterator[StreamEvent]:
        """Yield upstream SSE events until the stream terminates."""
        ...

    async def complete(self, body: dict[str, Any]) -> dict[str, Any]:
        """Non-streaming completion, returning an OpenAI-shaped response."""
        ...


class OpenAICompatProvider:
    """Any OpenAI-compatible endpoint: Ollama, OpenAI, vLLM, together, and friends.

    The body is forwarded essentially untouched, so a client's unusual sampling
    parameters reach the model rather than being silently dropped by us.
    """

    def __init__(
        self,
        *,
        name: str,
        base_url: str,
        client: httpx.AsyncClient,
        api_key: str | None = None,
    ) -> None:
        self.name = name
        self._base_url = base_url.rstrip("/")
        self._client = client
        self._api_key = api_key

    def _headers(self) -> dict[str, str]:
        headers = {"content-type": "application/json"}
        if self._api_key:
            headers["authorization"] = f"Bearer {self._api_key}"
        return headers

    async def stream(self, body: dict[str, Any]) -> AsyncIterator[StreamEvent]:
        payload = {**body, "stream": True}
        url = f"{self._base_url}/chat/completions"
        try:
            async with self._client.stream(
                "POST", url, json=payload, headers=self._headers()
            ) as response:
                if response.status_code >= 400:
                    detail = (await response.aread()).decode("utf-8", "replace")
                    raise UpstreamError(
                        f"{self.name} returned {response.status_code}: {detail[:500]}",
                        status=response.status_code,
                        provider=self.name,
                    )
                async for line in response.aiter_lines():
                    event = _parse_sse_line(line)
                    if event is None:
                        continue
                    yield event
                    if event.is_done:
                        return
        except httpx.HTTPError as exc:
            raise UpstreamError(f"{self.name} transport error: {exc}", provider=self.name) from exc

    async def complete(self, body: dict[str, Any]) -> dict[str, Any]:
        payload = {**body, "stream": False}
        url = f"{self._base_url}/chat/completions"
        try:
            response = await self._client.post(url, json=payload, headers=self._headers())
        except httpx.HTTPError as exc:
            raise UpstreamError(f"{self.name} transport error: {exc}", provider=self.name) from exc
        if response.status_code >= 400:
            raise UpstreamError(
                f"{self.name} returned {response.status_code}: {response.text[:500]}",
                status=response.status_code,
                provider=self.name,
            )
        result: dict[str, Any] = response.json()
        return result


def _parse_sse_line(line: str) -> StreamEvent | None:
    """Turn one raw SSE line into an event, or None if it carries nothing.

    Blank lines, comments and named events from the upstream are skipped: we re-frame
    the stream ourselves, and an upstream's own event names are not part of our contract.
    """
    if not line or line.startswith(":"):
        return None
    if not line.startswith("data:"):
        return None
    raw = line[len("data:") :].lstrip(" ")
    if raw == "[DONE]":
        return StreamEvent(raw=raw, data=None, is_done=True)
    try:
        return StreamEvent(raw=raw, data=json.loads(raw))
    except json.JSONDecodeError:
        # Forward it anyway: an unparseable chunk is the provider's business, and
        # dropping it would silently lose the customer's tokens.
        return StreamEvent(raw=raw, data=None)


class AnthropicProvider:
    """Anthropic Messages API, translated to and from the OpenAI shape.

    **Honesty note.** This adapter is written from the documented Messages API and is
    **not verified against the live service** in this build — there is no Anthropic key
    on the build machine (deviation D-003). It is exercised by a recorded-fixture
    contract test only. Do not claim two-provider coverage on stage without running it
    against a real key first; the plan's D5 eval asks for exactly that proof.
    """

    name = "anthropic"

    def __init__(self, *, base_url: str, client: httpx.AsyncClient, api_key: str | None) -> None:
        self._base_url = base_url.rstrip("/")
        self._client = client
        self._api_key = api_key

    def _headers(self) -> dict[str, str]:
        if not self._api_key:
            raise ProviderError("anthropic provider selected but ANTHROPIC_API_KEY is not set")
        return {
            "content-type": "application/json",
            "x-api-key": self._api_key,
            "anthropic-version": "2023-06-01",
        }

    @staticmethod
    def _to_anthropic(body: dict[str, Any]) -> dict[str, Any]:
        """OpenAI request -> Anthropic request.

        The system prompt is a top-level field rather than a message, which is the
        difference that silently breaks naive translations.
        """
        messages = [m for m in body.get("messages", []) if m.get("role") != "system"]
        system_parts = [
            str(m.get("content", "")) for m in body.get("messages", []) if m.get("role") == "system"
        ]
        payload: dict[str, Any] = {
            "model": body.get("model"),
            "messages": messages,
            "max_tokens": body.get("max_tokens") or 1024,
        }
        if system_parts:
            payload["system"] = "\n\n".join(system_parts)
        for key in ("temperature", "top_p", "stop_sequences"):
            if key in body:
                payload[key] = body[key]
        return payload

    @staticmethod
    def _chunk(text: str, model: str, finish_reason: str | None = None) -> dict[str, Any]:
        """Wrap an Anthropic delta in the OpenAI chunk shape the client expects."""
        return {
            "id": "chatcmpl-anthropic",
            "object": "chat.completion.chunk",
            "model": model,
            "choices": [
                {
                    "index": 0,
                    "delta": {"content": text} if text else {},
                    "finish_reason": finish_reason,
                }
            ],
        }

    async def stream(self, body: dict[str, Any]) -> AsyncIterator[StreamEvent]:
        payload = {**self._to_anthropic(body), "stream": True}
        model = str(body.get("model", ""))
        url = f"{self._base_url}/messages"
        try:
            async with self._client.stream(
                "POST", url, json=payload, headers=self._headers()
            ) as response:
                if response.status_code >= 400:
                    detail = (await response.aread()).decode("utf-8", "replace")
                    raise UpstreamError(
                        f"anthropic returned {response.status_code}: {detail[:500]}",
                        status=response.status_code,
                        provider=self.name,
                    )
                async for line in response.aiter_lines():
                    if not line.startswith("data:"):
                        continue
                    raw = line[len("data:") :].lstrip(" ")
                    try:
                        event = json.loads(raw)
                    except json.JSONDecodeError:
                        continue
                    kind = event.get("type")
                    if kind == "content_block_delta":
                        text = str(event.get("delta", {}).get("text", ""))
                        if text:
                            chunk = self._chunk(text, model)
                            yield StreamEvent(
                                raw=json.dumps(chunk, separators=(",", ":")), data=chunk
                            )
                    elif kind == "message_stop":
                        yield StreamEvent(raw="[DONE]", data=None, is_done=True)
                        return
        except httpx.HTTPError as exc:
            raise UpstreamError(f"anthropic transport error: {exc}", provider=self.name) from exc

    async def complete(self, body: dict[str, Any]) -> dict[str, Any]:
        url = f"{self._base_url}/messages"
        try:
            response = await self._client.post(
                url, json=self._to_anthropic(body), headers=self._headers()
            )
        except httpx.HTTPError as exc:
            raise UpstreamError(f"anthropic transport error: {exc}", provider=self.name) from exc
        if response.status_code >= 400:
            raise UpstreamError(
                f"anthropic returned {response.status_code}: {response.text[:500]}",
                status=response.status_code,
                provider=self.name,
            )
        result = response.json()
        text = "".join(
            block.get("text", "") for block in result.get("content", []) if isinstance(block, dict)
        )
        usage = result.get("usage", {})
        return {
            "id": result.get("id", "chatcmpl-anthropic"),
            "object": "chat.completion",
            "model": result.get("model", body.get("model")),
            "choices": [
                {
                    "index": 0,
                    "message": {"role": "assistant", "content": text},
                    "finish_reason": result.get("stop_reason"),
                }
            ],
            "usage": {
                "prompt_tokens": usage.get("input_tokens", 0),
                "completion_tokens": usage.get("output_tokens", 0),
                "total_tokens": usage.get("input_tokens", 0) + usage.get("output_tokens", 0),
            },
        }


def build_providers(settings: Any, client: httpx.AsyncClient) -> dict[str, Provider]:
    """Wire the adapters a deployment has credentials for.

    Ollama is always present, which is what makes a clean checkout runnable with no key.
    """
    providers: dict[str, Provider] = {
        "ollama": OpenAICompatProvider(
            name="ollama", base_url=settings.ollama_base_url, client=client
        ),
        "openai": OpenAICompatProvider(
            name="openai",
            base_url=settings.openai_base_url,
            client=client,
            api_key=settings.openai_api_key,
        ),
        "anthropic": AnthropicProvider(
            base_url=settings.anthropic_base_url,
            client=client,
            api_key=settings.anthropic_api_key,
        ),
    }
    return providers
