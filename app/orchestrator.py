"""Multi-model consensus orchestration.

Base pipeline (always on):

  1. FAN-OUT: one user query is sent to N research agents concurrently.
  2. INDEPENDENT EXECUTION: each agent produces its answer in isolation —
     no shared context, no visibility into the other agents' answers.
  3. JUDGE SYNTHESIS: a separate agent (its own model call) receives all
     successful answers verbatim and produces a merged answer + a grounded
     list of disagreements with per-claim model attribution.

Three optional layers, each toggleable per run so they can be studied
independently (see the matching modules for the deep commentary):

  * TOOL USE (options.use_tools, agent_loop.py + tools.py) — agents become
    ReAct loops that can search the web, fetch pages, and search attached
    documents before answering.
  * REBUTTAL ROUND (options.rebuttal, stage 4 below) — a second multi-agent
    turn: agents see the judge's round-1 disagreements and each other's
    claims, defend or revise, and the judge re-synthesizes.
  * LONG-TERM MEMORY (options.use_memory, memory.py) — relevant findings
    from past sessions are recalled and injected into the shared context;
    every verdict is remembered for future recall.

Progress is pushed onto an asyncio.Queue as JSON-able event dicts so the web
layer can stream them to the browser via SSE while agents are still running.
"""

import asyncio
import json
import re
import time
from dataclasses import dataclass, field
from typing import Any

import litellm

from .config import AGENT_TIMEOUT, MAX_TOKENS, ModelSpec
from .agent_loop import run_agent_loop
from .memory import format_recalled, recall, remember

# Keep LiteLLM quiet; errors are surfaced through our own events.
litellm.suppress_debug_info = True


@dataclass
class RunOptions:
    """Feature toggles + session context for one run."""

    use_tools: bool = False
    rebuttal: bool = False
    use_memory: bool = False
    # Prior turns of this session: [{"query": ..., "merged_answer": ...}].
    # Session memory — distinct from long-term memory (past sessions).
    history: list[dict[str, str]] = field(default_factory=list)


RESEARCHER_SYSTEM = """\
You are an independent research agent. Answer the user's research question
thoroughly and accurately. Structure your final answer as:

1. A direct answer to the question.
2. Your reasoning and key supporting facts.
3. A short note on your confidence and any caveats or uncertainty.

If additional context documents are provided, ground your answer in them.
Do not hedge into vagueness — commit to concrete claims where the evidence
supports them, and say clearly when it does not."""

RESEARCHER_TOOLS_ADDENDUM = """

You have research tools available (web search, page fetch, document search).
Use them when they would improve accuracy — especially for anything recent,
numeric, or verifiable — then give your final answer incorporating what you
found. Cite the sources you used. Do not call tools once you have enough to
answer confidently."""

JUDGE_SYSTEM = """\
You are a judge agent. You receive several answers to the same research
question, each produced independently by a different model. Your job:

1. Compare the answers for factual disagreement, differing scope, and stated
   confidence.
2. Merge them into ONE best-answer synthesis. For each substantive claim in
   the merged answer, attribute it to the model(s) that made it, inline, like
   "(per model-a, model-b)".
3. List points of genuine disagreement. A disagreement must be grounded in
   the actual text of the answers — quote or closely paraphrase what each
   model said. Do NOT invent disagreements; if the answers substantively
   agree, say so and return an empty disagreements list.
4. If some agents failed and their answers are missing, note that the
   synthesis omits them.

Respond with ONLY a JSON object (no markdown fence) of this shape:
{
  "merged_answer": "markdown text with inline (per model-x) attributions",
  "disagreements": [
    {
      "topic": "short label for the point of contention",
      "positions": [
        {"model": "model name", "claim": "what this model actually said (quote/close paraphrase)"}
      ]
    }
  ],
  "agreement_summary": "one or two sentences: overall, how aligned were the models?",
  "omissions": "note about any failed/missing agents, or null"
}"""

REBUTTAL_SYSTEM = """\
You are a research agent in a rebuttal round. You previously answered a
research question; a judge compared your answer with other models' answers
and found specific points of disagreement, shown below with each model's
position.

Reconsider ONLY the disputed points: defend your position with evidence, or
revise it if another model's position is better supported. Then restate your
COMPLETE final answer (same structure as before: direct answer, reasoning,
confidence note), incorporating any revisions. Be explicit about what you
changed and what you stand by."""


def _build_user_message(
    query: str,
    context_text: str,
    files: list[dict[str, str]],
    history: list[dict[str, str]],
    recalled_block: str | None,
) -> str:
    """Assemble the exact message every research agent receives.

    Built ONCE and reused for all agents so the comparison is fair: identical
    query, context, file contents, session history, and recalled memories.
    """
    parts = []
    if recalled_block:  # long-term memory, when enabled and relevant
        parts.append(recalled_block)
    if history:  # session memory: prior turns make follow-up questions coherent
        turns = "\n\n".join(
            f"Q: {t['query']}\nConsensus answer: {t['merged_answer']}" for t in history
        )
        parts.append(f"Earlier in this research session:\n{turns}")
    parts.append(f"Research question:\n{query}")
    if context_text.strip():
        parts.append(f"Additional context from the user:\n{context_text.strip()}")
    for f in files:
        parts.append(
            f"Attached document: {f['name']}\n"
            f"--- BEGIN {f['name']} ---\n{f['content']}\n--- END {f['name']} ---"
        )
    return "\n\n".join(parts)


async def _run_research_agent(
    spec: ModelSpec,
    user_message: str,
    files: list[dict[str, str]],
    use_tools: bool,
    events: asyncio.Queue,
    index: int,
) -> dict[str, Any]:
    """STAGE 2 — INDEPENDENT EXECUTION.

    With tools off: one isolated model call. With tools on: an isolated
    ReAct loop (see agent_loop.py) — still independent, because each agent
    has its own conversation and never sees another agent's output.
    Failures and timeouts are caught here so one bad provider can't sink
    the whole run.
    """
    started = time.perf_counter()
    await events.put({"type": "agent_started", "index": index, "model": spec.label})
    system = RESEARCHER_SYSTEM + (RESEARCHER_TOOLS_ADDENDUM if use_tools else "")
    messages = [
        {"role": "system", "content": system},
        {"role": "user", "content": user_message},
    ]
    try:
        if use_tools:
            result = await run_agent_loop(spec, messages, files, events, index, phase="research")
        else:
            response = await asyncio.wait_for(
                litellm.acompletion(
                    model=spec.name,
                    api_base=spec.api_base,
                    api_key=spec.api_key,
                    max_tokens=MAX_TOKENS,
                    messages=messages,
                ),
                timeout=AGENT_TIMEOUT,
            )
            result = {
                "index": index,
                "model": spec.label,
                "ok": True,
                "answer": response.choices[0].message.content or "",
                "latency": round(time.perf_counter() - started, 2),
                "trace": [],
            }
    except Exception as exc:  # timeout, auth error, rate limit, network...
        reason = "timed out" if isinstance(exc, asyncio.TimeoutError) else str(exc)
        result = {
            "index": index,
            "model": spec.label,
            "ok": False,
            "error": reason[:500],
            "latency": round(time.perf_counter() - started, 2),
            "trace": [],
        }
    await events.put({"type": "agent_finished", "phase": "research", **result})
    return result


def _parse_judge_json(raw: str) -> dict[str, Any]:
    """The judge is asked for bare JSON, but models sometimes wrap it in a
    ```json fence or add prose. Extract defensively; fall back to treating
    the whole response as the merged answer so the user never gets nothing."""
    text = raw.strip()
    fence = re.search(r"```(?:json)?\s*(\{.*\})\s*```", text, re.DOTALL)
    if fence:
        text = fence.group(1)
    else:
        brace = re.search(r"\{.*\}", text, re.DOTALL)
        if brace:
            text = brace.group(0)
    try:
        data = json.loads(text)
        if isinstance(data, dict) and "merged_answer" in data:
            data.setdefault("disagreements", [])
            data.setdefault("agreement_summary", "")
            data.setdefault("omissions", None)
            return data
    except json.JSONDecodeError:
        pass
    return {
        "merged_answer": raw,
        "disagreements": [],
        "agreement_summary": "",
        "omissions": None,
        "parse_warning": "Judge response was not valid JSON; showing raw output.",
    }


async def _run_judge(
    spec: ModelSpec,
    user_message: str,
    results: list[dict[str, Any]],
    events: asyncio.Queue,
    round_no: int,
) -> dict[str, Any]:
    """STAGE 3 — JUDGE SYNTHESIS.

    A real model call, not a template. The judge gets each successful answer
    verbatim (so disagreement-flagging is grounded in actual text) plus an
    explicit list of failed agents so it can note omissions. In round 2 the
    same judge runs again over the post-rebuttal answers."""
    succeeded = [r for r in results if r["ok"]]
    failed = [r for r in results if not r["ok"]]

    blocks = [f"The original research question and context given to every agent:\n{user_message}"]
    if round_no == 2:
        blocks.append(
            "NOTE: these are REVISED answers from a rebuttal round — each agent has "
            "already seen the round-1 disagreements and defended or updated its position."
        )
    for r in succeeded:
        blocks.append(
            f"=== Answer from model \"{r['model']}\" ===\n{r['answer']}\n=== end of {r['model']} ==="
        )
    if failed:
        names = ", ".join(f"\"{r['model']}\" ({r['error'][:100]})" for r in failed)
        blocks.append(f"These agents FAILED and produced no answer: {names}. Note this omission.")

    await events.put({"type": "judge_started", "model": spec.label, "round": round_no})
    started = time.perf_counter()
    response = await litellm.acompletion(
        model=spec.name,
        api_base=spec.api_base,
        api_key=spec.api_key,
        max_tokens=MAX_TOKENS,
        messages=[
            {"role": "system", "content": JUDGE_SYSTEM},
            {"role": "user", "content": "\n\n".join(blocks)},
        ],
    )
    verdict = _parse_judge_json(response.choices[0].message.content or "")
    verdict["judge_model"] = spec.label
    verdict["judge_latency"] = round(time.perf_counter() - started, 2)
    verdict["round"] = round_no
    return verdict


def _format_disagreements(disagreements: list[dict[str, Any]]) -> str:
    lines = []
    for d in disagreements:
        lines.append(f"Disputed: {d.get('topic', '')}")
        for p in d.get("positions", []):
            lines.append(f"  - {p.get('model', '?')}: {p.get('claim', '')}")
    return "\n".join(lines)


async def _run_rebuttal_agent(
    spec: ModelSpec,
    user_message: str,
    original_answer: str,
    disagreement_block: str,
    events: asyncio.Queue,
    index: int,
) -> dict[str, Any]:
    """STAGE 4 (optional) — REBUTTAL. The one place agents stop being fully
    independent, deliberately: each sees the judge's disagreement list (which
    quotes the other models' positions) and gets one turn to defend or revise.
    This is agent-to-agent interaction mediated by the judge."""
    started = time.perf_counter()
    await events.put(
        {"type": "agent_started", "index": index, "model": spec.label, "phase": "rebuttal"}
    )
    try:
        response = await asyncio.wait_for(
            litellm.acompletion(
                model=spec.name,
                api_base=spec.api_base,
                api_key=spec.api_key,
                max_tokens=MAX_TOKENS,
                messages=[
                    {"role": "system", "content": REBUTTAL_SYSTEM},
                    {
                        "role": "user",
                        "content": (
                            f"{user_message}\n\n"
                            f"YOUR previous answer:\n{original_answer}\n\n"
                            f"The judge's disagreement report:\n{disagreement_block}"
                        ),
                    },
                ],
            ),
            timeout=AGENT_TIMEOUT,
        )
        result = {
            "index": index,
            "model": spec.label,
            "ok": True,
            "answer": response.choices[0].message.content or "",
            "latency": round(time.perf_counter() - started, 2),
            "trace": [],
        }
    except Exception as exc:
        reason = "timed out" if isinstance(exc, asyncio.TimeoutError) else str(exc)
        result = {
            "index": index,
            "model": spec.label,
            "ok": False,
            "error": reason[:500],
            "latency": round(time.perf_counter() - started, 2),
            "trace": [],
        }
    await events.put({"type": "agent_finished", "phase": "rebuttal", **result})
    return result


async def run_consensus(
    query: str,
    context_text: str,
    files: list[dict[str, str]],
    research_models: list[ModelSpec],
    judge_model: ModelSpec,
    events: asyncio.Queue,
    options: RunOptions,
) -> None:
    """Full pipeline. Pushes events; terminates with a 'done' or 'error' event."""
    try:
        # PHASE 3 — LONG-TERM MEMORY RECALL (optional). Look up related past
        # findings BEFORE building the shared message so every agent gets the
        # same recalled context.
        recalled_block = None
        if options.use_memory:
            recalled = recall(query)
            if recalled:
                recalled_block = format_recalled(recalled)
            await events.put({"type": "memory_recalled", "items": recalled})

        user_message = _build_user_message(
            query, context_text, files, options.history, recalled_block
        )

        # STAGE 1 — FAN-OUT. asyncio.gather launches every agent at once;
        # total fan-out wall time ≈ max(agent latencies), not sum(latencies).
        # return_exceptions is unnecessary because each agent catches its own.
        fanout_started = time.perf_counter()
        results = await asyncio.gather(
            *(
                _run_research_agent(spec, user_message, files, options.use_tools, events, i)
                for i, spec in enumerate(research_models)
            )
        )
        wall = round(time.perf_counter() - fanout_started, 2)

        # Latency accounting: what parallelism bought us vs. running the same
        # agents one after another (the sum of individual latencies).
        sequential = round(sum(r["latency"] for r in results), 2)
        timing = {
            "parallel_wall_seconds": wall,
            "sequential_estimate_seconds": sequential,
            "seconds_saved": round(sequential - wall, 2),
        }
        await events.put({"type": "fanout_complete", "timing": timing})

        if not any(r["ok"] for r in results):
            await events.put({"type": "error", "message": "All research agents failed.",
                              "results": results, "timing": timing})
            return

        verdict = await _run_judge(judge_model, user_message, results, events, round_no=1)

        # STAGE 4 — REBUTTAL ROUND (optional). Only runs when the judge found
        # real disagreements: agents see the dispute, revise in parallel, and
        # the judge re-synthesizes from the revised answers.
        initial_verdict = None
        rebuttal_results = None
        if options.rebuttal and verdict.get("disagreements"):
            initial_verdict = verdict
            disagreement_block = _format_disagreements(verdict["disagreements"])
            await events.put(
                {"type": "rebuttal_started", "disagreement_count": len(verdict["disagreements"])}
            )
            by_index = {r["index"]: r for r in results if r["ok"]}
            rebuttal_results = await asyncio.gather(
                *(
                    _run_rebuttal_agent(
                        research_models[i], user_message, by_index[i]["answer"],
                        disagreement_block, events, i,
                    )
                    for i in by_index
                )
            )
            # Agents whose rebuttal failed keep their round-1 answer, so the
            # final judgment never has fewer voices than round 1.
            final_inputs = [
                r if r["ok"] else by_index[r["index"]] for r in rebuttal_results
            ]
            verdict = await _run_judge(judge_model, user_message, final_inputs, events, round_no=2)

        # PHASE 3 — LONG-TERM MEMORY WRITE. Always remember (cheap, local);
        # recall is what the toggle gates. This means memory is already
        # populated the first time you switch the feature on.
        remember(query, verdict)

        await events.put(
            {
                "type": "done",
                "results": results,
                "verdict": verdict,
                "initial_verdict": initial_verdict,
                "rebuttal_results": rebuttal_results,
                "timing": timing,
            }
        )
    except Exception as exc:
        await events.put({"type": "error", "message": f"Pipeline error: {exc}"})
