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
    _record_usage(getattr(usage, "total_tokens", 0) or 0)
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


# ── Usage tracking ───────────────────────────────────────────────────────────

# A ContextVar is inherited by asyncio tasks created inside the `with` block
# (asyncio.gather included), and they all share the same stats dict, so
# fan-outs and specialists are counted toward the run that spawned them.
_usage: ContextVar[dict[str, int] | None] = ContextVar("llm_usage", default=None)


@contextmanager
def track_usage() -> Iterator[dict[str, int]]:
    stats = {"calls": 0, "tokens": 0}
    token = _usage.set(stats)
    try:
        yield stats
    finally:
        _usage.reset(token)


def _record_usage(tokens: int) -> None:
    stats = _usage.get()
    if stats is not None:
        stats["calls"] += 1
        stats["tokens"] += tokens


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
