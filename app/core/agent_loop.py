"""The agentic loop (PHASE 1 — TOOL USE + the inner MULTI-TURN loop).

A plain completion is one shot: prompt in, answer out. An *agent* is a loop:

    ┌──────────────────────────────────────────────┐
    │  call model with conversation so far + tools │◀──┐
    └──────────────────────┬───────────────────────┘   │
                           ▼                           │
             did it request tool calls? ──── yes ──▶ execute tools,
                           │                         append results
                          no                         to conversation
                           ▼
                    final answer, loop ends

This is the ReAct pattern with native function calling: the model reasons,
acts (tool call), observes (tool result), and repeats until it decides it has
enough to answer. The conversation `messages` list IS the agent's working
memory — every tool result it has seen stays in context for later turns.

Frameworks like LangChain wrap exactly this loop; here it's ~60 lines you can
read top to bottom. (Model calls go through llm.complete(), which normalizes
provider replies and handles mock mode.)
"""

import asyncio
import time
from typing import Any

from .config import MAX_TOOL_ITERATIONS, ModelSpec
from .llm import complete
from .tools import execute_tool, tool_schemas


async def run_agent_loop(
    spec: ModelSpec,
    messages: list[dict[str, Any]],
    files: list[dict[str, str]],
    events: asyncio.Queue,
    index: int,
    phase: str,
) -> dict[str, Any]:
    """Run one agent with tools until it produces a final text answer.

    Returns the same result shape as a plain agent call, plus `trace`: the
    list of tool calls it made (shown in the UI as the research trace).
    """
    started = time.perf_counter()
    conversation = list(messages)  # own copy — working memory for this agent only
    trace: list[dict[str, Any]] = []
    schemas = tool_schemas(has_documents=bool(files))

    for iteration in range(MAX_TOOL_ITERATIONS):
        # Force a final answer on the last allowed iteration by withholding
        # tools — otherwise a tool-happy model could loop forever.
        last_chance = iteration == MAX_TOOL_ITERATIONS - 1
        reply = await complete(spec, conversation, tools=None if last_chance else schemas)

        if not reply.tool_calls:
            # No tool requests → this is the final answer; the loop is done.
            return {
                "index": index,
                "model": spec.label,
                "ok": True,
                "answer": reply.content,
                "latency": round(time.perf_counter() - started, 2),
                "trace": trace,
            }

        # The model acted. Record its request in the conversation exactly as
        # made (role=assistant with tool_calls), execute each call, and append
        # each result as a role=tool message keyed by tool_call_id.
        conversation.append(reply.as_message())
        for tc in reply.tool_calls:
            result = await execute_tool(tc.name, tc.arguments, files)
            trace.append(
                {
                    "tool": tc.name,
                    "args": tc.arguments,
                    "result_preview": result[:300],
                }
            )
            # Live event so the UI can show the research trace as it happens.
            await events.put(
                {
                    "type": "tool_call",
                    "phase": phase,
                    "index": index,
                    "model": spec.label,
                    "tool": tc.name,
                    "args": tc.arguments[:200],
                }
            )
            conversation.append(
                {"role": "tool", "tool_call_id": tc.id, "content": result}
            )

    # Unreachable in practice (last iteration has no tools, so the model must
    # answer), but never leave the caller without a result.
    return {
        "index": index,
        "model": spec.label,
        "ok": False,
        "error": "Agent hit the tool-iteration limit without answering.",
        "latency": round(time.perf_counter() - started, 2),
        "trace": trace,
    }
