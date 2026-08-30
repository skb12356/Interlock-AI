"""OpenRouter judge boundary tests using a real HTTPX transport boundary."""

from __future__ import annotations

import json
from collections.abc import Callable

import httpx
import pytest

from interlock.eval.openrouter_judge import (
    JudgeItem,
    OpenRouterJudge,
    gold_label_from_anchor,
)

ITEM_A = JudgeItem(
    item_id="a",
    question="What is the transfer limit?",
    context=("Verified policy: the transfer limit is INR 50,000.",),
    answer="The transfer limit is INR 50,000.",
)
ITEM_B = JudgeItem(
    item_id="b",
    question="Is a manager approval required?",
    context=("Verified policy: transfers over INR 50,000 require manager approval.",),
    answer="Manager approval is not required.",
)


def openai_response(
    *,
    results: object | None = None,
    content: str | None = None,
    finish_reason: str = "stop",
    refusal: str | None = None,
    status_code: int = 200,
    headers: dict[str, str] | None = None,
    usage: dict[str, object] | None = None,
) -> httpx.Response:
    """Return a complete synthetic OpenAI chat-completion response."""
    if content is None and results is not None:
        content = json.dumps({"results": results})
    body: dict[str, object] = {
        "id": "chatcmpl-test",
        "object": "chat.completion",
        "created": 1,
        "model": "openai/gpt-5-nano",
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": content, "refusal": refusal},
                "finish_reason": finish_reason,
            }
        ],
        "usage": usage
        or {
            "prompt_tokens": 41,
            "completion_tokens": 17,
            "total_tokens": 58,
            "cost": 0.000021,
        },
    }
    return httpx.Response(status_code, json=body, headers=headers)


def judge_with(
    handler: Callable[[httpx.Request], httpx.Response],
    *,
    sleep: Callable[[float], None] = lambda _: None,
) -> OpenRouterJudge:
    client = httpx.Client(transport=httpx.MockTransport(handler))
    return OpenRouterJudge(
        client,
        base_url="https://openrouter.ai/api/v1/",
        api_key="test-secret",
        sleep=sleep,
    )


def valid_result(item_id: str, label: str = "clean") -> dict[str, object]:
    return {
        "item_id": item_id,
        "label": label,
        "confidence": 0.9,
        "rationale": "The answer is supported by the supplied evidence.",
    }


def test_openrouter_payload_uses_chat_completions_and_never_ollama_fields() -> None:
    # Catches posting to Ollama's /api/chat or retaining format/options/num_predict.
    seen: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["path"] = request.url.path
        seen["authorization"] = request.headers.get("authorization")
        seen["body"] = json.loads(request.content)
        return openai_response(results=[valid_result("a")])

    judge = judge_with(handler)
    results = judge.judge("openai/gpt-5-nano", [ITEM_A])

    body = seen["body"]
    assert isinstance(body, dict)
    assert seen["path"] == "/api/v1/chat/completions"
    assert seen["authorization"] == "Bearer test-secret"
    assert body["model"] == "openai/gpt-5-nano"
    assert body["max_tokens"] == 256
    assert body["temperature"] == 0
    assert body["response_format"] == {"type": "json_object"}
    assert "format" not in body
    assert "options" not in body
    assert "stream" not in body
    assert judge.max_attempts == 3
    assert results[0].status == "valid"
    assert results[0].label == "clean"
    assert results[0].attempts == 1


def test_prompt_limits_the_judge_to_supplied_evidence() -> None:
    # Catches prompts that permit outside knowledge or execute instructions in evidence.
    seen_messages: object = None

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal seen_messages
        seen_messages = json.loads(request.content)["messages"]
        return openai_response(results=[valid_result("a")])

    judge_with(handler).judge("openai/gpt-5-nano", [ITEM_A])

    assert isinstance(seen_messages, list)
    prompt = "\n".join(str(message["content"]) for message in seen_messages)
    assert "only the supplied evidence" in prompt.lower()
    assert "outside knowledge" in prompt.lower()
    assert "data, not instructions" in prompt.lower()
    assert ITEM_A.item_id in prompt
    assert ITEM_A.question in prompt
    assert ITEM_A.context[0] in prompt
    assert ITEM_A.answer in prompt


@pytest.mark.parametrize(
    ("content", "want_label"),
    [
        (
            json.dumps({"results": [valid_result("a", "ungrounded")]}),
            "ungrounded",
        ),
        (json.dumps([valid_result("a", "contradicted")]), "contradicted"),
    ],
)
def test_parser_accepts_wrapped_results_and_direct_arrays(content: str, want_label: str) -> None:
    # Catches assuming one provider JSON wrapper when OpenRouter content is still valid.
    result = judge_with(lambda _: openai_response(content=content)).judge(
        "openai/gpt-5-nano", [ITEM_A]
    )[0]

    assert result.status == "valid"
    assert result.label == want_label


@pytest.mark.parametrize(
    ("response", "want_status"),
    [
        (openai_response(content=None), "invalid_json"),
        (openai_response(content="{not-json"), "invalid_json"),
        (openai_response(content=json.dumps({"results": "wrong"})), "invalid_json"),
        (
            openai_response(content=json.dumps([valid_result("a")]), finish_reason="length"),
            "truncated",
        ),
        (openai_response(content=None, refusal="I cannot perform this evaluation."), "refused"),
        (openai_response(content="I cannot perform this evaluation."), "refused"),
    ],
)
def test_unusable_provider_content_is_explicitly_classified(
    response: httpx.Response, want_status: str
) -> None:
    # Catches silently treating absent, truncated, malformed, or refused output as clean.
    result = judge_with(lambda _: response).judge("openai/gpt-5-nano", [ITEM_A])[0]

    assert result.status == want_status
    assert result.label is None


def test_malformed_chat_completion_envelope_is_normalized_instead_of_raising() -> None:
    # Catches calling mapping methods on a provider message with the wrong JSON type.
    response = httpx.Response(
        200,
        json={
            "id": "chatcmpl-malformed",
            "object": "chat.completion",
            "created": 1,
            "model": "openai/gpt-5-nano",
            "choices": [{"index": 0, "message": None, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 2, "completion_tokens": 1, "total_tokens": 3},
        },
    )

    result = judge_with(lambda _: response).judge("openai/gpt-5-nano", [ITEM_A])[0]

    assert result.status == "provider_error"
    assert result.error == "provider returned an invalid chat-completion envelope"


def test_unknown_labels_are_invalid_and_rationale_is_bounded() -> None:
    # Catches accepting labels outside the grounding-only taxonomy or unbounded output.
    raw = valid_result("a", "unsafe_action")
    raw["rationale"] = "x" * 2_000

    result = judge_with(lambda _: openai_response(results=[raw])).judge(
        "openai/gpt-5-nano", [ITEM_A]
    )[0]

    assert result.status == "invalid_label"
    assert result.label is None
    assert len(result.rationale) == 800


@pytest.mark.parametrize("label", [["clean"], {"label": "clean"}, 1, None])
def test_untrusted_non_string_labels_are_invalid_instead_of_raising(label: object) -> None:
    # Catches hashing provider-controlled list/dict labels during taxonomy membership.
    raw = valid_result("a")
    raw["label"] = label

    result = judge_with(lambda _: openai_response(results=[raw])).judge(
        "openai/gpt-5-nano", [ITEM_A]
    )[0]

    assert result.status == "invalid_label"
    assert result.label is None


def test_valid_string_labels_are_normalized_before_taxonomy_membership() -> None:
    # Catches rejecting a semantically valid label solely due to casing or outer spacing.
    raw = valid_result("a")
    raw["label"] = "  Contradicted  "

    result = judge_with(lambda _: openai_response(results=[raw])).judge(
        "openai/gpt-5-nano", [ITEM_A]
    )[0]

    assert result.status == "valid"
    assert result.label == "contradicted"


_MISSING = object()


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("confidence", _MISSING),
        ("confidence", None),
        ("confidence", "0.9"),
        ("confidence", True),
        ("confidence", -0.01),
        ("confidence", 1.01),
        ("confidence", float("nan")),
        ("rationale", _MISSING),
        ("rationale", None),
        ("rationale", 7),
        ("rationale", "   \n"),
    ],
)
def test_incomplete_or_wrong_typed_judge_fields_are_invalid_results(
    field: str, value: object
) -> None:
    # Catches scoring an incomplete provider result as a valid grounding judgment.
    raw = valid_result("a")
    if value is _MISSING:
        del raw[field]
    else:
        raw[field] = value

    result = judge_with(lambda _: openai_response(results=[raw])).judge(
        "openai/gpt-5-nano", [ITEM_A]
    )[0]

    assert result.status == "invalid_result"
    assert result.label is None
    assert result.confidence is None


def test_results_are_matched_by_stable_item_id_and_missing_items_are_reported() -> None:
    # Catches positional result matching and silently dropping a paid batch item.
    response = openai_response(results=[valid_result("b", "contradicted")])

    results = judge_with(lambda _: response).judge("openai/gpt-5-nano", [ITEM_A, ITEM_B])

    assert [result.item_id for result in results] == ["a", "b"]
    assert results[0].status == "missing_item"
    assert results[1].status == "valid"
    assert results[1].label == "contradicted"


def test_usage_and_latency_are_normalized_for_every_requested_item() -> None:
    # Catches losing OpenRouter billing fields or returning partial usage per result.
    response = openai_response(
        results=[valid_result("a"), valid_result("b", "contradicted")],
        usage={
            "prompt_tokens": 123,
            "completion_tokens": 45,
            "total_tokens": 168,
            "cost": "0.000314",
        },
    )

    results = judge_with(lambda _: response).judge("openai/gpt-5-nano", [ITEM_A, ITEM_B])

    assert len(results) == 2
    for result in results:
        assert result.usage.prompt_tokens == 123
        assert result.usage.completion_tokens == 45
        assert result.usage.cost_usd == pytest.approx(0.000314)
        assert result.latency_ms >= 0


@pytest.mark.parametrize(
    ("row", "want"),
    [
        ({"gold_ungrounded": 0, "gold_contradicted": 0, "gold_unsafe": 0}, "clean"),
        ({"gold_ungrounded": 1, "gold_contradicted": 0, "gold_unsafe": 0}, "ungrounded"),
        ({"gold_ungrounded": 0, "gold_contradicted": 1, "gold_unsafe": 0}, "contradicted"),
        ({"gold_ungrounded": 0, "gold_contradicted": 0, "gold_unsafe": 1}, "unsafe_action"),
    ],
)
def test_gold_label_is_read_from_top_level_anchor_flags(row: dict[str, object], want: str) -> None:
    # Catches the legacy script's bug of looking inside payload and scoring all rows clean.
    row["payload"] = {
        "gold_ungrounded": 0,
        "gold_contradicted": 0,
        "gold_unsafe": 0,
    }

    assert gold_label_from_anchor(row) == want


def test_authentication_errors_are_not_retried_or_leaked() -> None:
    # Catches retrying permanent credential errors or persisting sensitive response text.
    attempts = 0

    def handler(_: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        return httpx.Response(401, text="invalid test-secret Authorization header")

    result = judge_with(handler).judge("openai/gpt-5-nano", [ITEM_A])[0]

    assert attempts == 1
    assert result.status == "auth_error"
    assert result.attempts == 1
    assert result.error is not None
    assert "test-secret" not in result.error
    assert len(result.error) <= 500


@pytest.mark.parametrize("first_status", [429, 500, 503])
def test_retryable_status_then_success_retries_once(first_status: int) -> None:
    # Catches failing fast on transient rate-limit and provider errors.
    attempts = 0
    sleeps: list[float] = []

    def handler(_: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            return httpx.Response(first_status, headers={"Retry-After": "45"})
        return openai_response(results=[valid_result("a")])

    result = judge_with(handler, sleep=sleeps.append).judge("openai/gpt-5-nano", [ITEM_A])[0]

    assert attempts == 2
    assert result.status == "valid"
    assert result.attempts == 2
    assert sleeps == [30.0]


def test_repeated_rate_limit_stops_after_three_total_attempts() -> None:
    # Catches unbounded retry loops that can overspend a paid evaluation budget.
    attempts = 0

    def handler(_: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        return httpx.Response(429, text="slow down")

    result = judge_with(handler).judge("openai/gpt-5-nano", [ITEM_A])[0]

    assert attempts == 3
    assert result.status == "rate_limited"
    assert result.attempts == 3


def test_timeout_is_retried_then_normalized() -> None:
    # Catches propagating transport timeouts or failing to bound timeout retries.
    attempts = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        raise httpx.ReadTimeout("test-secret timed out", request=request)

    result = judge_with(handler).judge("openai/gpt-5-nano", [ITEM_A])[0]

    assert attempts == 3
    assert result.status == "timeout"
    assert result.attempts == 3
    assert result.error == "provider request timed out"


def test_invalid_json_is_not_retried() -> None:
    # Catches spending repeatedly after the provider successfully returned bad content.
    attempts = 0

    def handler(_: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        return openai_response(content="not json")

    result = judge_with(handler).judge("openai/gpt-5-nano", [ITEM_A])[0]

    assert attempts == 1
    assert result.status == "invalid_json"


def test_non_retryable_client_error_is_a_bounded_provider_error() -> None:
    # Catches returning raw provider bodies or retrying permanent request failures.
    attempts = 0

    def handler(_: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        return httpx.Response(400, text="x" * 2_000 + "test-secret")

    result = judge_with(handler).judge("openai/gpt-5-nano", [ITEM_A])[0]

    assert attempts == 1
    assert result.status == "provider_error"
    assert result.error == "provider returned HTTP 400"
    assert len(result.error) <= 500
