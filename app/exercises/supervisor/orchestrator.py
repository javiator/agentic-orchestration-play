"""Supervisor / handoff orchestration.

Consensus splits work STATICALLY (every agent gets the same task, decided in
code). Here the split is DYNAMIC: a supervisor model decides at runtime who
acts next, with what instructions, and when the job is done.

                 ┌──────────────────────────────────┐
     goal ─────▶ │ SUPERVISOR  (a tool-calling LLM) │ ◀──────────────────┐
                 └──────┬───────────────────┬───────┘                    │
         delegate(specialist, task)      finish(final_answer) ──▶ done   │
                        ▼                                                 │
                 ┌──────────────┐  appends   ┌────────────┐              │
                 │  SPECIALIST  │ ─────────▶ │ SCRATCHPAD │ shared state │
                 │ (own model)  │ ◀───reads─ └────────────┘              │
                 └──────┬───────┘                                        │
                        └── output returned as the tool result ──────────┘

Key ideas:
  * AGENTS AS TOOLS: specialists are exposed to the supervisor as one
    `delegate` tool. The supervisor's ReAct loop is the orchestrator.
  * TWO KINDS OF STATE: the supervisor's own conversation (its decisions and
    the results it was handed) vs. the shared scratchpad every specialist
    reads. Specialists never see the supervisor's reasoning — tasks must be
    self-contained, which is why delegation quality matters.
  * PARALLEL HANDOFFS: several `delegate` calls in one turn run concurrently
    (asyncio.gather), each seeing the same scratchpad snapshot.
  * LOOP GUARDS: a turn cap (the final turn only offers `finish`) and refusal
    of exact-repeat delegations, so a confused supervisor can't spin forever.
"""

import asyncio
import json
import os
import re
import time
from typing import Any

from app.core.agent_loop import run_agent_loop
from app.core.config import ModelSpec, load_judge_model, load_model, load_research_models
from app.core.llm import LLMReply, complete, mock_tool_call

from .specialists import BY_NAME, SPECIALISTS, Specialist, model_for

MAX_SUPERVISOR_TURNS = max(2, int(os.getenv("MAX_SUPERVISOR_TURNS", "8")))
MAX_RESULT_CHARS = 4_000      # specialist output handed back to the supervisor
MAX_SCRATCH_CHARS = 3_000     # per scratchpad entry shown to specialists


def load_supervisor_model() -> ModelSpec:
    return load_model("SUPERVISOR_MODEL") or load_judge_model()


# ── The supervisor's tools ──────────────────────────────────────────────────

DELEGATE_TOOL = {
    "type": "function",
    "function": {
        "name": "delegate",
        "description": (
            "Hand one well-scoped task to a specialist. Their output is returned to "
            "you and added to the shared scratchpad."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "specialist": {"type": "string", "enum": [s.name for s in SPECIALISTS]},
                "task": {
                    "type": "string",
                    "description": "Self-contained instructions: what to do and what to return.",
                },
            },
            "required": ["specialist", "task"],
        },
    },
}

FINISH_TOOL = {
    "type": "function",
    "function": {
        "name": "finish",
        "description": "End the run and give the user the final answer.",
        "parameters": {
            "type": "object",
            "properties": {
                "final_answer": {
                    "type": "string",
                    "description": "The complete answer for the user (usually the writer's "
                    "deliverable, verbatim or lightly edited).",
                },
            },
            "required": ["final_answer"],
        },
    },
}

SUPERVISOR_SYSTEM = """\
You are a supervisor coordinating a team of specialist agents to accomplish
the user's goal. You do not do the work yourself: each turn, either delegate
tasks with `delegate`, or end with `finish` once the scratchpad contains a
complete answer to the goal.

Your specialists:
{roster}

Guidelines:
- Specialists see the goal and the shared scratchpad, but NOT your reasoning.
  Make every task self-contained: say exactly what to do and what to return.
- A typical flow is research → analysis → writing, but skip steps the goal
  does not need (a simple question may need only research + writing).
- Independent tasks can be delegated in the same turn; they run in parallel.
- If a specialist fails or returns something weak, re-delegate with a better
  task or use a different specialist. Never repeat an identical task.
- Before your tool calls, state in one sentence why you chose them.
- You have at most {max_turns} turns."""

LAST_TURN_NOTE = (
    "This is your final turn: call `finish` now with the best complete answer you "
    "can give from the scratchpad."
)


# ── Mock supervisor (MOCK_MODELS=true) ──────────────────────────────────────


def _mock_supervisor(messages: list[dict[str, Any]], tools: list[dict] | None) -> LLMReply:
    """Scripted run: two researchers in parallel → analyst → writer → finish."""
    offered = {t["function"]["name"] for t in tools or []}
    results = [m["content"] for m in messages if m.get("role") == "tool"]
    script = {
        0: [("researcher", "(mock) Find the main facts and figures relevant to the goal."),
            ("researcher", "(mock) Find counter-arguments and known risks.")],
        2: [("analyst", "(mock) Compare the findings and flag any contradictions.")],
        3: [("writer", "(mock) Write the final answer from the scratchpad.")],
    }
    if "delegate" in offered and len(results) in script:
        calls = script[len(results)]
        who = " and ".join(sorted({c[0] for c in calls}))
        return LLMReply(
            f"(mock) Next I need the {who}.",
            [mock_tool_call("delegate", {"specialist": s, "task": t}) for s, t in calls],
        )
    draft = results[-1].split("\n", 1)[-1] if results else "(mock) No work was done."
    return LLMReply("(mock) The writer's draft answers the goal.",
                    [mock_tool_call("finish", {"final_answer": draft})])


# ── The run ─────────────────────────────────────────────────────────────────


def _format_scratchpad(entries: list[dict[str, Any]]) -> str:
    if not entries:
        return "(empty — nothing has been done yet)"
    blocks = []
    for e in entries:
        out = e["output"]
        if len(out) > MAX_SCRATCH_CHARS:
            out = out[:MAX_SCRATCH_CHARS] + "\n[... truncated ...]"
        blocks.append(f"[step {e['step']}] {e['specialist']} — task: {e['task']}\n{out}")
    return "\n\n".join(blocks)


def _normalize_task(task: str) -> str:
    return re.sub(r"\W+", " ", task.lower()).strip()


async def run_supervisor(goal: str, events: asyncio.Queue) -> None:
    """Full run. Pushes events; always terminates with 'done' or 'error'."""
    started = time.perf_counter()
    try:
        supervisor = load_supervisor_model()
        models = load_research_models()
        await events.put(
            {
                "type": "run_started",
                "supervisor": supervisor.label,
                "max_turns": MAX_SUPERVISOR_TURNS,
                "specialists": [
                    {"name": s.name, "model": model_for(s, models).label, "role": s.role}
                    for s in SPECIALISTS
                ],
            }
        )

        roster = "\n".join(f"- {s.name}: {s.role}" for s in SPECIALISTS)
        conversation: list[dict[str, Any]] = [
            {
                "role": "system",
                "content": SUPERVISOR_SYSTEM.format(roster=roster, max_turns=MAX_SUPERVISOR_TURNS),
            },
            {"role": "user", "content": f"Goal:\n{goal}"},
        ]
        scratchpad: list[dict[str, Any]] = []   # shared state, read by every specialist
        done_tasks: dict[tuple[str, str], int] = {}  # (specialist, task) → step, for the repeat guard
        step_counter = 0
        final_answer: str | None = None
        finished_by = ""

        async def delegate(
            turn: int, step: int, specialist: Specialist, task: str, snapshot: str
        ) -> str:
            """Run one specialist on one task; returns the text the supervisor sees."""
            model = model_for(specialist, models)
            await events.put(
                {"type": "delegated", "step": step, "turn": turn,
                 "specialist": specialist.name, "model": model.label, "task": task}
            )
            messages = [
                {"role": "system", "content": specialist.system},
                {
                    "role": "user",
                    "content": f"Overall goal:\n{goal}\n\n"
                    f"Shared scratchpad (your team's work so far):\n{snapshot}\n\n"
                    f"Your task from the supervisor:\n{task}",
                },
            ]
            t0 = time.perf_counter()
            try:
                if specialist.uses_tools:
                    result = await run_agent_loop(
                        model, messages, [], events, step, phase=specialist.name
                    )
                    if not result["ok"]:
                        raise RuntimeError(result["error"])
                    output = result["answer"]
                else:
                    output = (await complete(model, messages)).content
                ok, error = True, None
            except Exception as exc:  # contained: the supervisor decides what to do next
                ok, output = False, ""
                error = "timed out" if isinstance(exc, asyncio.TimeoutError) else str(exc)[:500]
            await events.put(
                {"type": "specialist_finished", "step": step, "specialist": specialist.name,
                 "model": model.label, "ok": ok, "output": output, "error": error,
                 "latency": round(time.perf_counter() - t0, 2)}
            )
            if not ok:
                return f"[step {step}] {specialist.name} FAILED: {error}"
            scratchpad.append(
                {"step": step, "specialist": specialist.name, "task": task, "output": output}
            )
            if len(output) > MAX_RESULT_CHARS:
                output = output[:MAX_RESULT_CHARS] + "\n[... truncated ...]"
            return f"[step {step}] {specialist.name} output:\n{output}"

        for turn in range(1, MAX_SUPERVISOR_TURNS + 1):
            last = turn == MAX_SUPERVISOR_TURNS
            await events.put({"type": "supervisor_thinking", "turn": turn, "last": last})
            if last:
                conversation.append({"role": "user", "content": LAST_TURN_NOTE})
            # The final turn only offers `finish` (still a tool, so providers
            # that require tool definitions alongside tool history stay happy).
            reply = await complete(
                supervisor,
                conversation,
                tools=[FINISH_TOOL] if last else [DELEGATE_TOOL, FINISH_TOOL],
                mock=_mock_supervisor,
            )
            if reply.content.strip():
                await events.put(
                    {"type": "supervisor_note", "turn": turn, "text": reply.content.strip()}
                )

            if not reply.tool_calls:
                # Answered in plain text instead of calling finish — accept it.
                final_answer, finished_by = reply.content, "text"
                break

            finish = next((tc for tc in reply.tool_calls if tc.name == "finish"), None)
            if finish:
                try:
                    final_answer = json.loads(finish.arguments).get("final_answer", "")
                except json.JSONDecodeError:
                    final_answer = finish.arguments
                finished_by = "finish"
                break

            conversation.append(reply.as_message())

            # Plan this turn's delegations, applying guards, then run the valid
            # ones in parallel. Every tool call MUST get a tool result message.
            snapshot = _format_scratchpad(scratchpad)
            jobs: list[tuple[str, Any]] = []  # (tool_call_id, coroutine or ready text)
            for tc in reply.tool_calls:
                try:
                    args = json.loads(tc.arguments or "{}")
                except json.JSONDecodeError:
                    args = {}
                specialist = BY_NAME.get(str(args.get("specialist", "")))
                task = str(args.get("task", "")).strip()
                if tc.name != "delegate" or specialist is None or not task:
                    msg = f"Invalid call {tc.name}({tc.arguments}). Use delegate(specialist, task) or finish."
                    await events.put({"type": "guard", "turn": turn, "message": msg})
                    jobs.append((tc.id, msg))
                    continue
                key = (specialist.name, _normalize_task(task))
                if key in done_tasks:
                    msg = (f"REFUSED: {specialist.name} already did exactly this in step "
                           f"{done_tasks[key]}. Use that result, change the task, or finish.")
                    await events.put({"type": "guard", "turn": turn, "message": msg})
                    jobs.append((tc.id, msg))
                    continue
                step_counter += 1
                done_tasks[key] = step_counter
                jobs.append((tc.id, delegate(turn, step_counter, specialist, task, snapshot)))

            async def resolve(item: Any) -> str:
                return item if isinstance(item, str) else await item

            outputs = await asyncio.gather(*(resolve(j) for _, j in jobs))
            for (call_id, _), output in zip(jobs, outputs):
                conversation.append({"role": "tool", "tool_call_id": call_id, "content": output})

        if final_answer is None:
            # The last turn ignored `finish` — fall back to the latest work.
            final_answer = scratchpad[-1]["output"] if scratchpad else "No answer was produced."
            finished_by = "turn_limit"

        await events.put(
            {
                "type": "done",
                "final_answer": final_answer,
                "finished_by": finished_by,
                "turns": turn,
                "delegations": step_counter,
                "elapsed": round(time.perf_counter() - started, 2),
            }
        )
    except Exception as exc:
        await events.put({"type": "error", "message": f"Supervisor error: {exc}"})
