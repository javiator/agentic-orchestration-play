"""Human-in-the-loop: the agent acts on its own — until an action is risky.

    request ──▶ agent loop (tools over a fake shop)
                  │ tool call
                  ▼
            POLICY CHECK (code, see world.needs_approval)
              ├─ read / internal write / small refund ──▶ run it now
              └─ big refund / external email ──▶ PAUSE ⏸
                       │  the run's state (the conversation so far + the
                       │  proposed action) is held while a human decides:
                       ├─ approve           → run as proposed
                       ├─ edit + approve    → run with the human's arguments
                       └─ reject + feedback → NOT run; the agent is told why
                       ▼
                 loop continues ──▶ final summary for the operator

Why: full autonomy is fine for actions that are cheap to undo, and
dangerous for ones that aren't (money, messages, deletions). Gating by
RISK keeps humans out of the routine path and in the irreversible one.
The rejection feedback matters: it goes back into the conversation, so the
agent can adjust (smaller refund, different wording) instead of retrying.

Real systems persist that paused state (a "checkpoint") in a database, so a
pause can last hours and survive restarts (LangGraph interrupts, durable
workflow engines). Here it lives in memory for up to HITL_APPROVAL_TIMEOUT
seconds; no decision in time counts as a rejection.
"""

import asyncio
import json
import os
import re
import uuid
from dataclasses import dataclass, field
from typing import Any

from app.core.config import ModelSpec, load_model, load_research_models
from app.core.llm import LLMReply, complete, mock_tool_call, track_usage

from .world import POLICY, RISK, TOOLS, World, needs_approval

MAX_STEPS = max(2, int(os.getenv("HITL_MAX_STEPS", "10")))
APPROVAL_TIMEOUT = float(os.getenv("HITL_APPROVAL_TIMEOUT", "600"))


def agent_model() -> ModelSpec:
    return load_model("HITL_MODEL") or load_research_models()[0]


SYSTEM = f"""\
You are a customer-support operations agent for an online shop. Handle the
operator's request using your tools. Shop policy:
{POLICY}
Some actions need human approval; if one is rejected, read the reviewer's
feedback and adjust — don't repeat the same action unchanged.
Finish with a short summary for the operator of what you did and didn't do."""


@dataclass
class Run:
    """One agent run's live state — what a checkpoint would persist."""
    world: World = field(default_factory=World)
    pending: dict[str, asyncio.Future] = field(default_factory=dict)

    def decide(self, action_id: str, decision: dict[str, Any]) -> bool:
        fut = self.pending.get(action_id)
        if fut is None or fut.done():
            return False
        fut.set_result(decision)
        return True


# ── Mock agent: look up, refund, email, summarize ───────────────────────────


def _mock_agent(messages: list[dict[str, Any]], tools: Any) -> LLMReply:
    request = messages[1]["content"]
    email = (re.search(r"[\w.]+@[\w.]+", request) or [None])[0] or "jane@example.com"
    order = (re.search(r"\bA\d{4}\b", request) or [None])[0] or "A1002"
    results = [m["content"] for m in messages if m.get("role") == "tool"]
    n = len(results)
    if n == 0:
        return LLMReply("", [mock_tool_call("lookup_customer", {"email": email})])
    if n == 1:
        return LLMReply("", [mock_tool_call("lookup_order", {"order_id": order})])
    if n == 2:
        total = (json.loads(results[1]) if results[1].startswith("{") else {}).get("total", 0)
        amount = float(m.group(1)) if (m := re.search(r"\$(\d+(?:\.\d+)?)", request)) else total
        return LLMReply("(mock) Order verified; issuing the refund.",
                        [mock_tool_call("issue_refund", {"order_id": order, "amount": amount, "reason": "Customer reported a problem"})])
    if n == 3:
        refunded = results[2].startswith("Refunded")
        body = (f"Hi, we've refunded your order {order}. Sorry for the trouble!" if refunded
                else f"Hi, we're looking into your request about order {order} and will follow up soon.")
        return LLMReply("", [mock_tool_call("send_email", {"to": email, "subject": f"Your order {order}", "body": body})])
    return LLMReply(f"(mock summary) Looked up {email} and {order}. Refund: {results[2][:80]} Email: {results[3][:80]}")


# ── The loop ────────────────────────────────────────────────────────────────


async def run_hitl(request: str, run: Run, events: asyncio.Queue) -> None:
    """Always ends with done/error."""
    try:
        model = agent_model()
        await events.put({"type": "run_started", "request": request, "model": model.label,
                          "world": run.world.snapshot()})
        messages: list[dict[str, Any]] = [{"role": "system", "content": SYSTEM},
                                          {"role": "user", "content": request}]
        stats = {"auto": 0, "approved": 0, "edited": 0, "rejected": 0, "waited": 0.0}
        answer, finished_by = "", "answer"
        with track_usage() as usage:
            for step in range(1, MAX_STEPS + 1):
                reply = await complete(model, messages, tools=TOOLS, mock=_mock_agent)
                if not reply.tool_calls:
                    answer = reply.content
                    break
                if step == MAX_STEPS:
                    answer, finished_by = reply.content or "Stopped: step budget used up.", "step_limit"
                    break
                if reply.content.strip():
                    await events.put({"type": "agent_note", "step": step, "text": reply.content.strip()})
                messages.append(reply.as_message())
                for tc in reply.tool_calls:
                    try:
                        args = json.loads(tc.arguments or "{}")
                    except json.JSONDecodeError:
                        args = {}
                    result = await _handle(tc.name, args, step, run, events, stats)
                    messages.append({"role": "tool", "tool_call_id": tc.id, "content": result})
        await events.put({"type": "done", "answer": answer, "finished_by": finished_by,
                          "stats": stats, "world": run.world.snapshot(),
                          "usage": {**usage, "cost": round(usage["cost"], 6)}})
    except Exception as exc:
        await events.put({"type": "error", "message": f"HITL error: {exc}"})
    finally:
        for fut in run.pending.values():
            if not fut.done():
                fut.cancel()


async def _handle(tool: str, args: dict[str, Any], step: int, run: Run,
                  events: asyncio.Queue, stats: dict[str, Any]) -> str:
    """Policy check → (maybe) wait for a human → execute or refuse."""
    risk = RISK.get(tool, "unknown")
    reason = needs_approval(tool, args)
    if reason is None:
        result = run.world.execute(tool, args)
        stats["auto"] += 1
        await events.put({"type": "action", "step": step, "tool": tool, "args": args, "risk": risk,
                          "decision": "auto", "result": result, "world": run.world.snapshot()})
        return result

    action_id = uuid.uuid4().hex[:8]
    fut: asyncio.Future = asyncio.get_running_loop().create_future()
    run.pending[action_id] = fut
    await events.put({"type": "approval_required", "step": step, "action_id": action_id,
                      "tool": tool, "args": args, "risk": risk, "reason": reason})
    loop = asyncio.get_running_loop()
    started = loop.time()
    try:
        decision = await asyncio.wait_for(fut, timeout=APPROVAL_TIMEOUT)
    except (asyncio.TimeoutError, asyncio.CancelledError):
        decision = {"decision": "reject", "feedback": "No decision in time (timed out)."}
    finally:
        run.pending.pop(action_id, None)
    stats["waited"] = round(stats["waited"] + loop.time() - started, 1)

    kind = decision.get("decision")
    if kind in ("approve", "edit"):
        final_args = decision.get("args") if kind == "edit" else args
        result = run.world.execute(tool, final_args)
        stats["approved" if kind == "approve" else "edited"] += 1
        if kind == "edit":
            result = f"A human reviewer edited your arguments to {json.dumps(final_args)} before running it. {result}"
    else:
        final_args = args
        feedback = str(decision.get("feedback") or "no reason given")[:500]
        result = f"REJECTED by a human reviewer — not executed. Feedback: {feedback}"
        stats["rejected"] += 1
    await events.put({"type": "action", "step": step, "tool": tool, "args": final_args, "risk": risk,
                      "decision": kind if kind in ("approve", "edit") else "reject", "action_id": action_id,
                      "result": result, "world": run.world.snapshot()})
    return result
