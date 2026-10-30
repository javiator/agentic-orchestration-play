"""One function for every model call: `complete()`.

All exercises call models through here instead of litellm directly, so
cross-cutting concerns live in one place:

  * provider-agnostic routing (LiteLLM picks the SDK from the model prefix),
  * per-call timeout,
  * a normalized reply (`LLMReply`: text + tool calls) that callers can read
    without knowing provider response shapes,
  * MOCK MODE (MOCK_MODELS=true): canned replies with a short delay instead of
    a network call, so every UI and orchestration flow can be explored for
    free. Callers that need a specific shape (e.g. the judge's JSON) pass a
    `mock=` function; otherwise a generic mock answers in text, or — when
    tools are offered — calls a couple of them first, like a real agent would.
  * USAGE TRACKING: inside `with track_usage() as stats:` every call made by
    that code (including concurrent sub-tasks it spawns) is counted — how the
    eval harness measures what an orchestration pattern costs.
"""

import asyncio
import json
import random
import time
import uuid
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Any

import litellm

from .config import AGENT_TIMEOUT, MAX_TOKENS, MOCK_MODELS, ModelSpec

# Keep LiteLLM quiet; errors are surfaced through our own events.
litellm.suppress_debug_info = True


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: str  # JSON string, exactly as the model produced it
    # The provider's own tool-call dict, echoed back verbatim in the next
    # request (some providers attach fields, e.g. reasoning signatures, that
    # must round-trip).
    raw: dict[str, Any] | None = None


@dataclass
class LLMReply:
    content: str
    tool_calls: list[ToolCall] = field(default_factory=list)

    def as_message(self) -> dict[str, Any]:
        """This reply as an assistant message to append to a conversation."""
        msg: dict[str, Any] = {"role": "assistant", "content": self.content}
        if self.tool_calls:
            msg["tool_calls"] = [
                tc.raw
                or {
                    "id": tc.id,
                    "type": "function",
                    "function": {"name": tc.name, "arguments": tc.arguments},
                }
                for tc in self.tool_calls
            ]
        return msg


# A mock receives (messages, tools) and returns reply text, an LLMReply, or
# None to fall back to the generic mock.
MockFn = Callable[[list[dict[str, Any]], list[dict] | None], "str | LLMReply | None"]


async def complete(
    spec: ModelSpec,
    messages: list[dict[str, Any]],
    *,
    tools: list[dict] | None = None,
    timeout: float | None = AGENT_TIMEOUT,
    max_tokens: int = MAX_TOKENS,
    mock: MockFn | None = None,
) -> LLMReply:
    """One model call. Raises on provider errors / timeout — callers decide
    whether a failure is fatal (judge) or contained (one research agent)."""
    if MOCK_MODELS:
        reply = await _mock_complete(spec, messages, tools, mock)
        # No real usage in mock mode: estimate ~4 chars per token.
        chars = sum(len(str(m.get("content", ""))) for m in messages) + len(reply.content)
        _record_usage(chars // 4)
        return reply

    call = litellm.acompletion(
        model=spec.name,
        api_base=spec.api_base,
        api_key=spec.api_key,
        max_tokens=max_tokens,
        messages=messages,
        tools=tools,
    )
    response = await (asyncio.wait_for(call, timeout=timeout) if timeout else call)
    usage = getattr(response, "usage", None)
    _record_usage(getattr(usage, "total_tokens", 0) or 0, _cost(response))
    msg = response.choices[0].message
    return LLMReply(
        content=msg.content or "",
        tool_calls=[
            ToolCall(
                id=tc.id,
                name=tc.function.name,
                arguments=tc.function.arguments or "{}",
                raw=tc.model_dump(),
            )
            for tc in (getattr(msg, "tool_calls", None) or [])
        ],
    )


async def embed(spec: ModelSpec, texts: list[str], *, timeout: float | None = AGENT_TIMEOUT) -> list[list[float]]:
    """Embedding vectors for `texts` (one per text), via LiteLLM like
    `complete()` — any provider's embedding model, chosen in .env. Not
    available in mock mode: callers fall back to keyword search."""
    if MOCK_MODELS:
        raise RuntimeError("embeddings are not mocked")
    call = litellm.aembedding(
        model=spec.name, input=texts, api_base=spec.api_base, api_key=spec.api_key
    )
    response = await (asyncio.wait_for(call, timeout=timeout) if timeout else call)
    usage = getattr(response, "usage", None)
    _record_usage(getattr(usage, "total_tokens", 0) or 0, _cost(response))
    return [
        item["embedding"] if isinstance(item, dict) else item.embedding for item in response.data
    ]


# ── Usage tracking ───────────────────────────────────────────────────────────

# A ContextVar is inherited by asyncio tasks created inside the `with` block
# (asyncio.gather included), and they all share the same stats dict, so
# fan-outs and specialists are counted toward the run that spawned them.
# Trackers nest: a call counts toward every enclosing `with` block, so an
# exercise can meter its own parts while the eval meters the whole run.
_usage: ContextVar[tuple[dict[str, Any], ...]] = ContextVar("llm_usage", default=())


def _cost(response: Any) -> float:
    """USD cost from LiteLLM's price table; 0.0 for models it doesn't know
    (local models, custom gateways) — so treat cost as a lower bound."""
    try:
        return float(litellm.completion_cost(completion_response=response) or 0.0)
    except Exception:
        return 0.0


@contextmanager
def track_usage() -> Iterator[dict[str, Any]]:
    """Yields {"calls", "tokens", "cost"} — cost in USD, 0 in mock mode."""
    stats: dict[str, Any] = {"calls": 0, "tokens": 0, "cost": 0.0}
    token = _usage.set((*_usage.get(), stats))
    try:
        yield stats
    finally:
        _usage.reset(token)


def _record_usage(tokens: int, cost: float = 0.0) -> None:
    for stats in _usage.get():
        stats["calls"] += 1
        stats["tokens"] += tokens
        stats["cost"] += cost


# ── Mock mode ────────────────────────────────────────────────────────────────


def mock_tool_call(name: str, args: dict[str, Any]) -> ToolCall:
    return ToolCall(id=f"mock_{uuid.uuid4().hex[:8]}", name=name, arguments=json.dumps(args))


def last_user_text(messages: list[dict[str, Any]], limit: int = 140) -> str:
    for m in reversed(messages):
        if m.get("role") == "user":
            text = " ".join(str(m.get("content", "")).split())
            return text if len(text) <= limit else text[:limit] + "…"
    return ""


async def _mock_complete(
    spec: ModelSpec,
    messages: list[dict[str, Any]],
    tools: list[dict] | None,
    mock: MockFn | None,
) -> LLMReply:
    await asyncio.sleep(random.uniform(0.4, 1.2))  # so progress UIs have something to show
    reply = mock(messages, tools) if mock else None
    if reply is None:
        reply = _generic_mock(spec, messages, tools)
    return LLMReply(reply) if isinstance(reply, str) else reply


def _generic_mock(
    spec: ModelSpec, messages: list[dict[str, Any]], tools: list[dict] | None
) -> LLMReply:
    """Text answer, or — if tools are offered — first call up to two of them
    in turn (one per round), so tool traces show up in mock mode too."""
    used = sum(1 for m in messages if m.get("role") == "tool")
    if tools and used < min(2, len(tools)):
        fn = tools[used]["function"]
        params = fn.get("parameters", {}).get("properties", {})
        args = {
            k: (p["enum"][0] if "enum" in p else f"mock {k}")
            for k, p in params.items()
        }
        return LLMReply("", [mock_tool_call(fn["name"], args)])
    return LLMReply(f"(mock answer from {spec.label}) Responding to: {last_user_text(messages)}")


async def metered(awaitable: Any) -> tuple[Any, dict[str, Any]]:
    """Await something and return (result, {calls, tokens, cost, latency}) —
    the cost of one strategy, for side-by-side comparisons."""
    started = time.perf_counter()
    with track_usage() as usage:
        result = await awaitable
    return result, {**usage, "cost": round(usage["cost"], 6),
                    "latency": round(time.perf_counter() - started, 2)}
