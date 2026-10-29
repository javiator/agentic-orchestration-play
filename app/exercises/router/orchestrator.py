"""Router: a cheap model decides which path each request takes.

    request ──▶ ROUTER (small model, one short call → JSON)
                  │ route + confidence + reason
                  ├── simple    ──▶ small model, one call        (cheap, fast)
                  ├── reasoning ──▶ strong model, one call       (math, code, analysis)
                  ├── research  ──▶ strong model + web tools     (fresh facts)
                  └── decline   ──▶ canned reply, no model call  (out of scope)
                  │
                  └─ confidence < ROUTER_MIN_CONFIDENCE?  ESCALATE to the
                     safer, more capable path — a wrong cheap answer usually
                     costs more than the price difference.

Why route: most real traffic is easy. Sending everything to the strongest
model with every tool is simple and good — and pays top price for "hi".
A router spends one small call to save many big ones.
What can go wrong: misroutes (a hard question judged easy), router latency
on every request, and a router prompt that silently drifts as traffic
changes. The fix is measurement: compare against "always strong" on a
question set (the Eval Harness lists both as targets).
"""

import asyncio
import json
import os
import re
from dataclasses import dataclass
from typing import Any

from app.core.agent_loop import run_agent_loop
from app.core.config import ModelSpec, load_judge_model, load_model, load_research_models
from app.core.llm import complete, metered

ROUTER_MIN_CONFIDENCE = float(os.getenv("ROUTER_MIN_CONFIDENCE", "0.6"))


def small_model() -> ModelSpec:
    return load_model("ROUTER_SMALL_MODEL") or load_research_models()[0]


def strong_model() -> ModelSpec:
    return load_model("ROUTER_STRONG_MODEL") or load_judge_model()


def router_model() -> ModelSpec:
    return load_model("ROUTER_MODEL") or small_model()


@dataclass(frozen=True)
class Route:
    name: str
    handler: str        # what runs: shown in the UI
    when: str           # routing criterion: shown to the router AND the user
    tier: str           # "small" | "strong" | "none"
    tools: bool = False


ROUTES = {
    r.name: r
    for r in (
        Route("simple", "small model, one call",
              "greetings, definitions, well-known facts, rewording, short explanations", "small"),
        Route("reasoning", "strong model, one call",
              "math, logic puzzles, multi-step reasoning, writing or reviewing code, careful analysis", "strong"),
        Route("research", "strong model + web search tools",
              "needs current or recent information (news, prices, this week/year) or facts it must look up", "strong", tools=True),
        Route("decline", "canned reply, no model call",
              "clearly harmful or illegal requests", "none"),
    )
}
# Where a low-confidence decision escalates to: the next safer path.
ESCALATE = {"simple": "reasoning", "decline": "reasoning"}
DECLINE_REPLY = "Sorry — I can't help with that request."
ANSWER_SYSTEM = "Answer the user's question accurately. Be concise but complete."

ROUTER_SYSTEM = """\
You are a request router. Pick the ONE route that handles the user's request
best at the lowest cost:

{routes}

Respond with ONLY JSON:
{{"route": "<name>", "confidence": <0.0-1.0>, "reason": "<one short sentence>"}}"""


def _router_prompt() -> str:
    return ROUTER_SYSTEM.format(routes="\n".join(f'- "{r.name}": {r.when}' for r in ROUTES.values()))


def _mock_route(messages: list[dict[str, Any]], tools: Any) -> str:
    """Keyword heuristic standing in for the router model in mock mode."""
    q = messages[-1]["content"].lower()
    if re.search(r"\b(pick a lock|weapon|steal|hack into)\b", q):
        route, conf = "decline", 0.9
    elif re.search(r"\b(this week|today|current|latest|price of|news|right now)\b", q):
        route, conf = "research", 0.85
    elif re.search(r"\d|\b(function|code|prove|calculate|puzzle|why does)\b", q):
        route, conf = "reasoning", 0.8
    else:
        route, conf = "simple", 0.55 if len(q) > 55 else 0.9  # long + "simple" → unsure
    return json.dumps({"route": route, "confidence": conf, "reason": f"(mock) keyword heuristic → {route}"})


async def decide(question: str) -> dict[str, Any]:
    """One router call → {route, confidence, reason, escalated_from}. Never
    raises: an unusable router reply falls back to the safe 'reasoning' path."""
    try:
        reply = await complete(
            router_model(),
            [{"role": "system", "content": _router_prompt()}, {"role": "user", "content": question}],
            mock=_mock_route,
        )
        match = re.search(r"\{.*\}", reply.content, re.DOTALL)
        data = json.loads(match.group(0) if match else reply.content)
        route = str(data.get("route", "")).strip().lower()
        if route not in ROUTES:
            raise ValueError(f"unknown route {route!r}")
        decision = {"route": route, "confidence": max(0.0, min(1.0, float(data.get("confidence", 0)))),
                    "reason": str(data.get("reason", ""))[:300], "escalated_from": None}
    except Exception as exc:
        return {"route": "reasoning", "confidence": 0.0, "escalated_from": None,
                "reason": f"Router reply unusable ({str(exc)[:120]}) — fell back to the safe path."}
    if decision["confidence"] < ROUTER_MIN_CONFIDENCE and decision["route"] in ESCALATE:
        decision["escalated_from"] = decision["route"]
        decision["route"] = ESCALATE[decision["route"]]
    return decision


async def answer_with(route: Route, question: str, events: asyncio.Queue, index: int) -> str:
    """Execute one route. `index` tags tool-call events for the UI column."""
    if route.tier == "none":
        return DECLINE_REPLY
    model = small_model() if route.tier == "small" else strong_model()
    messages = [{"role": "system", "content": ANSWER_SYSTEM}, {"role": "user", "content": question}]
    if route.tools:
        result = await run_agent_loop(model, messages, [], events, index=index, phase="research")
        if not result["ok"]:
            raise RuntimeError(result["error"])
        return result["answer"]
    return (await complete(model, messages)).content


async def run_routed(question: str, events: asyncio.Queue) -> dict[str, Any]:
    async def routed() -> dict[str, Any]:
        decision = await decide(question)
        await events.put({"type": "route_decided", **decision,
                          "handler": ROUTES[decision["route"]].handler})
        answer = await answer_with(ROUTES[decision["route"]], question, events, index=0)
        return {"answer": answer, "route": decision["route"]}

    result, usage = await metered(routed())
    return {**result, "usage": usage}


async def run_always_strong(question: str, events: asyncio.Queue) -> dict[str, Any]:
    """The no-router baseline: strongest model with tools, every time."""
    answer, usage = await metered(answer_with(ROUTES["research"], question, events, index=1))
    return {"answer": answer, "usage": usage}


async def run_router(question: str, events: asyncio.Queue, compare: bool) -> None:
    """Always ends with done/error. With `compare`, the baseline runs in
    parallel so the two costs can be read side by side."""
    try:
        await events.put({"type": "run_started", "question": question, "compare": compare})

        async def guarded(name: str, coro) -> dict[str, Any]:
            try:
                result = await coro
            except Exception as exc:
                result = {"error": str(exc)[:300]}
            await events.put({"type": "path_finished", "path": name, **result})
            return result

        jobs = [guarded("routed", run_routed(question, events))]
        if compare:
            jobs.append(guarded("baseline", run_always_strong(question, events)))
        results = await asyncio.gather(*jobs)
        await events.put({"type": "done", "routed": results[0],
                          "baseline": results[1] if compare else None})
    except Exception as exc:
        await events.put({"type": "error", "message": f"Router error: {exc}"})


async def classify_all(questions: list[str], events: asyncio.Queue) -> None:
    """Route a batch without answering — shows the traffic mix and what the
    router costs on its own."""
    try:
        async def one(i: int, q: str) -> dict[str, Any]:
            decision, usage = await metered(decide(q))
            row = {"i": i, "question": q, **decision, "usage": usage}
            await events.put({"type": "classified", **row})
            return row

        rows = await asyncio.gather(*(one(i, q) for i, q in enumerate(questions)))
        mix: dict[str, int] = {}
        for r in rows:
            mix[r["route"]] = mix.get(r["route"], 0) + 1
        await events.put({"type": "done", "mix": mix,
                          "router_tokens": sum(r["usage"]["tokens"] for r in rows)})
    except Exception as exc:
        await events.put({"type": "error", "message": f"Router error: {exc}"})
