"""Planner–executor: plan the whole job up front, then run it as a graph.

    goal ──▶ PLANNER (one call) ──▶ plan = steps + dependencies (a DAG)
                                     │   validated by code: ids, deps, no cycles
                                     ▼
              wave 1:  [s1 research]  [s2 research]     ← no deps: run in PARALLEL
              wave 2:        [s3 think: needs s1, s2]
              wave 3:        [s4 think: needs s3]
                                     │
                       a step FAILED? ──▶ REPLANNER: keeps finished work,
                                     │    plans only what's left (≤ MAX_REPLANS)
                                     ▼
                         SYNTHESIZER ──▶ final answer

Contrast with the Supervisor exercise, which decides ONE step at a time
after seeing each result:
  * planner: few decision calls, maximum parallelism, a plan you can read
    (and approve) before anything runs — but it commits early, so a wrong
    assumption or failed step needs an explicit REPLAN;
  * supervisor: adapts every turn, but pays a model call per decision and
    tends to work sequentially.
Waves are a simplification: a step waits for its whole wave. A real
scheduler starts each step the moment its own dependencies finish.
"""

import asyncio
import json
import os
import re
import time
from typing import Any

from app.core.agent_loop import run_agent_loop
from app.core.config import ModelSpec, load_judge_model, load_model, load_research_models
from app.core.llm import complete, metered

MAX_STEPS = max(2, int(os.getenv("PLANNER_MAX_STEPS", "6")))
MAX_REPLANS = max(0, int(os.getenv("PLANNER_MAX_REPLANS", "1")))
INJECTED_ERROR = "Injected failure: simulated tool outage (the 'inject a failure' option)"


def planner_model() -> ModelSpec:
    return load_model("PLANNER_MODEL") or load_judge_model()


PLAN_SCHEMA = """\
{"steps": [{"id": "s1", "task": "<what this step must produce>",
            "kind": "research" | "think", "depends_on": ["<ids of earlier steps>"]}]}"""

PLANNER_SYSTEM = f"""\
You plan work for a team of workers. Break the user's goal into at most
{MAX_STEPS} steps that together produce what's needed for a final answer.
- "research" steps can search the web and read pages; "think" steps only reason.
- A step's depends_on lists the steps whose output it needs. Steps without
  dependencies on each other run in parallel — prefer independent steps.
- Each task must be self-contained and specific (what to find or produce).
- Don't add a final "write the answer" step: a synthesizer does that.
Respond with ONLY JSON: {PLAN_SCHEMA}"""

REPLAN_SYSTEM = f"""\
You are re-planning after a step failed. Some steps already finished; their
ids may be used in depends_on. Plan ONLY the remaining work (new ids, at most
{MAX_STEPS} steps), working around the failure — e.g. a different source or
approach, or drop what isn't essential.
Respond with ONLY JSON: {PLAN_SCHEMA}"""

WORKER_SYSTEM = "You are one worker in a team. Do ONLY your task, using the inputs given. Be concise and factual."

SYNTH_SYSTEM = """\
Write the final answer to the user's goal from the workers' results.
Use only what the results support; if a part couldn't be done, say so."""


# ── Mock planner: a 4-step diamond, and a reroute on replan ─────────────────


def _mock_plan(messages: list[dict[str, Any]], tools: Any) -> str:
    if "FAILED step" in messages[-1]["content"]:
        steps = [
            {"id": "s2b", "task": "(mock) Find the same facts from a different source", "kind": "research", "depends_on": []},
            {"id": "s3b", "task": "(mock) Compare the options on cost and effort", "kind": "think", "depends_on": ["s1", "s2b"]},
            {"id": "s4b", "task": "(mock) Decide which option fits best and why", "kind": "think", "depends_on": ["s3b"]},
        ]
    else:
        steps = [
            {"id": "s1", "task": "(mock) Research option A: costs and trade-offs", "kind": "research", "depends_on": []},
            {"id": "s2", "task": "(mock) Research option B: costs and trade-offs", "kind": "research", "depends_on": []},
            {"id": "s3", "task": "(mock) Compare A and B on cost and effort", "kind": "think", "depends_on": ["s1", "s2"]},
            {"id": "s4", "task": "(mock) Decide which option fits best and why", "kind": "think", "depends_on": ["s3"]},
        ]
    return json.dumps({"steps": steps})


# ── Plan validation (code, not a model) ─────────────────────────────────────


def parse_plan(text: str, done_ids: set[str]) -> list[dict[str, Any]]:
    """Parse + validate a plan. Raises ValueError with a message the planner
    can act on. Returns steps in a valid execution order."""
    match = re.search(r"\{.*\}", text, re.DOTALL)
    data = json.loads(match.group(0) if match else text)
    steps = data.get("steps") if isinstance(data, dict) else None
    if not isinstance(steps, list) or not steps:
        raise ValueError("plan needs a non-empty 'steps' list")
    if len(steps) > MAX_STEPS:
        raise ValueError(f"plan has {len(steps)} steps; the maximum is {MAX_STEPS}")
    ids: set[str] = set()
    clean = []
    for s in steps:
        sid = str(s.get("id", "")).strip()
        if not sid or sid in ids or sid in done_ids:
            raise ValueError(f"step ids must be unique and new; problem with {sid!r}")
        ids.add(sid)
        kind = s.get("kind") if s.get("kind") in ("research", "think") else "think"
        clean.append({"id": sid, "task": str(s.get("task", "")).strip()[:500], "kind": kind,
                      "depends_on": [str(d) for d in (s.get("depends_on") or [])]})
    for s in clean:
        unknown = [d for d in s["depends_on"] if d not in ids and d not in done_ids]
        if unknown:
            raise ValueError(f"step {s['id']} depends on unknown step(s) {unknown}")
        if not s["task"]:
            raise ValueError(f"step {s['id']} has no task")
    waves(clean, done_ids)  # raises on cycles
    return clean


def waves(steps: list[dict[str, Any]], done_ids: set[str]) -> list[list[dict[str, Any]]]:
    """Group steps into waves: each wave's steps depend only on earlier waves."""
    remaining, finished, out = list(steps), set(done_ids), []
    while remaining:
        ready = [s for s in remaining if all(d in finished for d in s["depends_on"])]
        if not ready:
            raise ValueError(f"dependency cycle among {[s['id'] for s in remaining]}")
        out.append(ready)
        finished |= {s["id"] for s in ready}
        remaining = [s for s in remaining if s["id"] not in finished]
    return out


async def _plan(system: str, prompt: str, done_ids: set[str], events: asyncio.Queue) -> list[dict[str, Any]]:
    messages = [{"role": "system", "content": system}, {"role": "user", "content": prompt}]
    for attempt in (1, 2):
        reply = await complete(planner_model(), messages, mock=_mock_plan)
        try:
            return parse_plan(reply.content, done_ids)
        except (ValueError, json.JSONDecodeError) as exc:
            await events.put({"type": "plan_rejected", "attempt": attempt, "reason": str(exc)[:200]})
            messages += [reply.as_message(), {"role": "user", "content": f"Invalid plan: {exc}. Fix it and reply with ONLY the JSON."}]
    raise RuntimeError("planner produced no valid plan in two attempts")


# ── Execution ───────────────────────────────────────────────────────────────


async def _run_step(step: dict[str, Any], number: int, goal: str, results: dict[str, dict[str, Any]],
                    events: asyncio.Queue, workers: list[ModelSpec], inject: bool) -> dict[str, Any]:
    model = workers[(number - 1) % len(workers)]
    await events.put({"type": "step_started", "id": step["id"], "number": number, "model": model.label})
    started = time.perf_counter()
    try:
        if inject:
            await asyncio.sleep(0.5)
            raise RuntimeError(INJECTED_ERROR)
        inputs = "\n\n".join(f"Result of {d} ({results[d]['task']}):\n{results[d]['output'][:3000]}"
                             for d in step["depends_on"] if d in results)
        prompt = f"Overall goal: {goal}\n\nYour task: {step['task']}" + (f"\n\nInputs:\n{inputs}" if inputs else "")
        messages = [{"role": "system", "content": WORKER_SYSTEM}, {"role": "user", "content": prompt}]
        if step["kind"] == "research":
            r = await run_agent_loop(model, messages, [], events, index=number, phase="step")
            if not r["ok"]:
                raise RuntimeError(r["error"])
            output = r["answer"]
        else:
            output = (await complete(model, messages)).content
        out = {"ok": True, "output": output}
    except Exception as exc:
        out = {"ok": False, "error": "timed out" if isinstance(exc, asyncio.TimeoutError) else str(exc)[:300]}
    out |= {"id": step["id"], "task": step["task"], "latency": round(time.perf_counter() - started, 2)}
    await events.put({"type": "step_finished", **out})
    return out


async def run_planner(goal: str, events: asyncio.Queue, inject_failure: bool = False) -> None:
    """Plan → execute in waves → replan on failure → synthesize. Always ends
    with done/error."""
    try:
        workers = load_research_models()
        await events.put({"type": "run_started", "goal": goal, "planner": planner_model().label,
                          "workers": [w.label for w in workers], "max_steps": MAX_STEPS,
                          "max_replans": MAX_REPLANS})

        async def pipeline() -> dict[str, Any]:
            results: dict[str, dict[str, Any]] = {}
            failures: list[dict[str, Any]] = []
            plan = await _plan(PLANNER_SYSTEM, goal, set(), events)
            await events.put({"type": "plan_created", "version": 1, "steps": plan, "done_ids": []})
            replans, number, injected = 0, 0, not inject_failure
            while plan:
                failed_step = None
                for w, wave in enumerate(waves(plan, set(results)), 1):
                    await events.put({"type": "wave_started", "wave": w, "ids": [s["id"] for s in wave]})
                    jobs = []
                    for s in wave:
                        number += 1
                        # Inject into the 2nd step run, once — wherever it lands.
                        inject = not injected and number == 2
                        injected |= inject
                        jobs.append(_run_step(s, number, goal, results, events, workers, inject))
                    outs = await asyncio.gather(*jobs)
                    for o in outs:
                        if o["ok"]:
                            results[o["id"]] = o
                        else:
                            failures.append(o)
                            failed_step = failed_step or o
                    if failed_step:
                        break  # stop launching dependent waves; decide what to do
                if not failed_step:
                    break
                if replans >= MAX_REPLANS:
                    await events.put({"type": "replan_skipped", "reason": "replan budget used up — synthesizing from what finished"})
                    break
                replans += 1
                await events.put({"type": "replanning", "failed_id": failed_step["id"], "error": failed_step["error"]})
                done_text = "\n".join(f"- {r['id']} ({r['task']}): {r['output'][:400]}" for r in results.values()) or "(none)"
                prompt = (f"Goal: {goal}\n\nFinished steps:\n{done_text}\n\n"
                          f"FAILED step {failed_step['id']} ({failed_step['task']}): {failed_step['error']}")
                plan = await _plan(REPLAN_SYSTEM, prompt, set(results), events)
                await events.put({"type": "plan_created", "version": replans + 1, "steps": plan,
                                  "done_ids": list(results)})

            await events.put({"type": "synthesizing"})
            body = "\n\n".join(f"[{r['id']}] {r['task']}\n{r['output'][:4000]}" for r in results.values())
            if failures:
                body += "\n\nSteps that failed: " + "; ".join(f"{f['id']} ({f['error'][:100]})" for f in failures)
            answer = (await complete(planner_model(), [
                {"role": "system", "content": SYNTH_SYSTEM},
                {"role": "user", "content": f"Goal: {goal}\n\nWorker results:\n{body}"}])).content
            return {"answer": answer, "steps_run": number, "replans": replans,
                    "failed": [f["id"] for f in failures]}

        result, usage = await metered(pipeline())
        await events.put({"type": "done", **result, "usage": usage})
    except Exception as exc:
        await events.put({"type": "error", "message": f"Planner error: {exc}"})
