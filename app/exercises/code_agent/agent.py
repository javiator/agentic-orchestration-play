"""A code-executing agent: the model writes Python, runs it, reads the result.

    question ──▶ ┌──────────────────── loop (≤ CODE_MAX_STEPS) ───────────────────┐
                 │ model ── run_python(code) ──▶ sandbox: fresh process          │
                 │   ▲                              │                             │
                 │   └── exit code + stdout/stderr ─┘  error? the model reads    │
                 │                                     the traceback and fixes   │
                 │ model ── text answer ──▶ done                                 │
                 └───────────────────────────────────────────────────────────────┘

Why this pattern matters: models are unreliable at exact arithmetic,
counting and reading tables — but good at writing the code that does it.
Running the code turns "plausible" into "computed", and the execution result
is REAL FEEDBACK from the world (unlike a model critiquing itself): a
traceback says exactly what's wrong. The loop is ReAct with an interpreter
as the only tool.

Guards: a step budget whose last step must answer, a block on re-running
identical code, and the sandbox's own timeout/limits (see sandbox.py).
"""

import asyncio
import json
import os
import time
from typing import Any

from app.core.config import ModelSpec, load_model, load_research_models
from app.core.llm import LLMReply, complete, mock_tool_call, track_usage

from .sandbox import CODE_TIMEOUT, Workspace

CODE_MAX_STEPS = max(2, int(os.getenv("CODE_MAX_STEPS", "8")))


def load_code_model() -> ModelSpec:
    return load_model("CODE_MODEL") or load_research_models()[0]


RUN_TOOL = {
    "type": "function",
    "function": {
        "name": "run_python",
        "description": (
            "Run a Python 3 script in a fresh process and get its exit code, stdout and "
            "stderr. Only print()ed output is returned. Variables don't persist between "
            "runs; files written to the working directory do."
        ),
        "parameters": {
            "type": "object",
            "properties": {"code": {"type": "string", "description": "A complete Python script"}},
            "required": ["code"],
        },
    },
}

SYSTEM = """\
You solve questions by writing and running Python with the run_python tool.

Rules:
- Use code for every calculation, count, date difference or data lookup —
  never compute in your head. print() the values you need to see.
- Standard library only (csv, statistics, math, datetime, collections, json…);
  no third-party packages are installed.
- Files in the working directory: {files}. Inspect a file's columns and a few
  rows before analysing it.
- If a run fails, read the traceback, fix the cause, and run again.
- Each run must be a complete script (nothing persists between runs).
- When the output answers the question, reply with a concise final answer in
  plain text stating the key numbers — no more tool calls."""

LAST_STEP_NOTE = (
    "Step budget used up. Give your final answer now from the outputs you have, "
    "without running more code. Say clearly if the result is incomplete."
)

# ── Mock mode: a scripted agent that makes (and fixes) a mistake ────────────

_MOCK_BUGGY = """\
import csv
rows = list(csv.DictReader(open("sales.csv")))
print(len(row))  # typo on purpose: shows error → traceback → fix
"""
_MOCK_FIXED = """\
import csv
rows = list(csv.DictReader(open("sales.csv")))
print("columns:", list(rows[0]))
print("rows:", len(rows))
total = sum(int(r["units"]) * int(r["unit_price"]) for r in rows)
print("total revenue, all months:", total)
"""


def _mock_agent(messages: list[dict[str, Any]], tools: Any) -> LLMReply:
    results = [m["content"] for m in messages if m.get("role") == "tool"]
    if not results:
        return LLMReply("(mock) First I'll load the data.", [mock_tool_call("run_python", {"code": _MOCK_BUGGY})])
    if len(results) == 1:
        return LLMReply("(mock) NameError — I used `row` instead of `rows`. Fixing.",
                        [mock_tool_call("run_python", {"code": _MOCK_FIXED})])
    last_line = results[-1].strip().splitlines()[-1]
    return LLMReply(f"(mock answer — a real model would answer your question) The script ran: {last_line}")


# ── The loop ────────────────────────────────────────────────────────────────


async def run_code_agent(question: str, events: asyncio.Queue) -> None:
    """Pushes events; always ends with 'done' or 'error'."""
    workspace = Workspace()
    started = time.perf_counter()
    try:
        model = load_code_model()
        files = workspace.files()
        await events.put({"type": "run_started", "question": question, "model": model.label,
                          "files": files, "max_steps": CODE_MAX_STEPS, "timeout": CODE_TIMEOUT})
        messages: list[dict[str, Any]] = [
            {"role": "system", "content": SYSTEM.format(files=", ".join(files) or "(none)")},
            {"role": "user", "content": question},
        ]
        runs = failed = 0
        seen_code: set[str] = set()
        answer, finished_by, step = "", "answer", 0
        with track_usage() as usage:
            while True:
                step += 1
                last = step >= CODE_MAX_STEPS
                if last:
                    messages.append({"role": "user", "content": LAST_STEP_NOTE})
                # Tools stay offered on the last step for provider compatibility;
                # a call made anyway ends the run (step_limit).
                reply = await complete(model, messages, tools=[RUN_TOOL], mock=_mock_agent)
                if not reply.tool_calls:
                    answer = reply.content
                    break
                if last:
                    answer = reply.content or "Ran out of steps before reaching an answer."
                    finished_by = "step_limit"
                    break
                if reply.content.strip():
                    await events.put({"type": "agent_note", "step": step, "text": reply.content.strip()})
                messages.append(reply.as_message())
                for tc in reply.tool_calls:
                    try:
                        code = str(json.loads(tc.arguments or "{}").get("code", ""))
                    except json.JSONDecodeError:
                        code = ""
                    if tc.name != "run_python" or not code.strip():
                        observation = "Call run_python with a non-empty `code` string."
                        await events.put({"type": "code_run", "step": step, "code": code,
                                          "invalid": True, "message": observation})
                    elif code.strip() in seen_code:
                        # Same code → same result. Make the model change something.
                        observation = "You already ran exactly this code. Change it, or answer."
                        await events.put({"type": "code_run", "step": step, "code": code,
                                          "repeated": True, "message": observation})
                    else:
                        seen_code.add(code.strip())
                        result = await workspace.run_python(code)
                        runs += 1
                        failed += not result.ok
                        observation = result.for_model()
                        await events.put({"type": "code_run", "step": step, "code": code,
                                          "exit_code": result.exit_code, "stdout": result.stdout,
                                          "stderr": result.stderr, "duration": result.duration,
                                          "timed_out": result.timed_out})
                    messages.append({"role": "tool", "tool_call_id": tc.id, "content": observation})

        await events.put({"type": "done", "answer": answer, "finished_by": finished_by,
                          "steps": step, "runs": runs, "failed_runs": failed,
                          "latency": round(time.perf_counter() - started, 2),
                          "calls": usage["calls"], "tokens": usage["tokens"]})
    except Exception as exc:
        await events.put({"type": "error", "message": f"Code agent error: {exc}"})
    finally:
        workspace.cleanup()
