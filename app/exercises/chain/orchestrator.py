"""Prompt chain vs single prompt — the same writing task, two strategies.

SINGLE PROMPT: one call carries every requirement at once.

    spec + topic ──▶ model ──▶ text                       (1 call)

PROMPT CHAIN: a fixed sequence of small steps, with CODE GATES between them.

    topic ──▶ ① outline (JSON) ──▶ [gate: valid JSON, 3 sections?] ── no → retry once
                    │
                    ▼  ② write each section — in PARALLEL, one call each
              ┌─────┼─────┐
              ▼     ▼     ▼
            [code: assemble title + sections + takeaway]
                    │
                    ▼
             [gate: check the full spec]  ── fails → ③ targeted fix call
                    │                              (only the failed checks)
                    ▼
                  text                            (~5–7 calls)

This is a WORKFLOW, not an agent: the steps and their order are decided by
code, not by a model. Use one when the task decomposes predictably — each
step gets a small, focused prompt, and code can verify the output between
steps. Agents (supervisor, planner) earn their extra unpredictability only
when the steps can't be known in advance.
Trade-off to watch: more calls and latency vs higher compliance — and
whether a strong model already gets it right in one shot.
"""

import asyncio
import json
import os
import re
from typing import Any

from app.core.config import ModelSpec, load_model, load_research_models
from app.core.llm import complete, last_user_text, metered

from .spec import BANNED, MAX_AVG_SENTENCE, SPEC_TEXT, check

CHAIN_FIX_ROUNDS = max(0, int(os.getenv("CHAIN_FIX_ROUNDS", "2")))


def chain_model() -> ModelSpec:
    """Both strategies use the same model — the comparison is about structure."""
    return load_model("CHAIN_MODEL") or load_research_models()[0]


SINGLE_SYSTEM = f"Write a short explainer on the user's topic. Requirements:\n{SPEC_TEXT}\nOutput only the explainer."

OUTLINE_SYSTEM = """\
Plan a short beginner explainer on the user's topic. Respond with ONLY JSON:
{"title": "<title>", "sections": [{"heading": "<heading>", "point": "<the one idea this section explains>"}, ...exactly 3...], "takeaway": "<one sentence>"}"""

SECTION_SYSTEM = f"""\
Write ONE section body (no heading) of a beginner explainer: 50–60 words,
short sentences (under {MAX_AVG_SENTENCE} words), plain words — never use:
{", ".join(BANNED)}. Output only the paragraph."""

FIX_SYSTEM = f"""\
Revise the explainer so it passes the failed checks listed by the user,
changing as little as possible. Requirements:\n{SPEC_TEXT}\nOutput only the revised explainer."""


# ── Mock replies (deterministic text shaped like real output) ───────────────

_FILLER = ("It starts with a simple idea. Small parts work together. Each part has one job. "
           "Over time the pattern repeats. You can see it in daily life. The result is easy to notice. ")


def _mock_single(messages: list[dict[str, Any]], tools: Any) -> str:
    topic = last_user_text(messages, 80)
    # A typical one-shot miss: 4 sections, too long, a banned word, no takeaway.
    body = "\n\n".join(f"## Part {i}\n{_FILLER * 2}We leverage this idea." for i in range(1, 5))
    return f"# {topic}\n\n{body}"


def _mock_outline(messages: list[dict[str, Any]], tools: Any) -> str:
    topic = last_user_text(messages, 60)
    return json.dumps({"title": topic, "takeaway": f"{topic} is simpler than it looks.",
                       "sections": [{"heading": h, "point": f"{h} of {topic}"}
                                    for h in ("The basic idea", "How it works", "Why it matters")]})


def _mock_section(messages: list[dict[str, Any]], tools: Any) -> str:
    return _FILLER + "That is the core of it here."


def _mock_fix(messages: list[dict[str, Any]], tools: Any) -> str:
    draft = messages[-1]["content"].split("Explainer:\n", 1)[-1]
    if "length" in messages[-1]["content"]:  # pad the last section a little
        draft = draft.replace("\n\nKey takeaway:", " Now you know the basics.\n\nKey takeaway:")
    return draft


# ── Strategies ──────────────────────────────────────────────────────────────


async def run_single(topic: str, events: asyncio.Queue, model: ModelSpec) -> str:
    await events.put({"type": "step", "variant": "single", "name": "one prompt with the full spec", "kind": "llm", "status": "running"})
    text = (await complete(model, [{"role": "system", "content": SINGLE_SYSTEM},
                                   {"role": "user", "content": topic}], mock=_mock_single)).content.strip()
    await events.put({"type": "step", "variant": "single", "name": "one prompt with the full spec", "kind": "llm", "status": "ok",
                      "detail": f"{len(text.split())} words"})
    return text


def _parse_outline(text: str) -> dict[str, Any]:
    match = re.search(r"\{.*\}", text, re.DOTALL)
    data = json.loads(match.group(0) if match else text)
    sections = data.get("sections")
    if not isinstance(sections, list) or len(sections) != 3:
        raise ValueError(f"expected 3 sections, got {len(sections) if isinstance(sections, list) else 'none'}")
    if not all(isinstance(s, dict) and s.get("heading") and s.get("point") for s in sections):
        raise ValueError("each section needs a heading and a point")
    if not data.get("title") or not data.get("takeaway"):
        raise ValueError("missing title or takeaway")
    return data


async def run_chain(topic: str, events: asyncio.Queue, model: ModelSpec) -> str:
    async def step(name: str, kind: str, status: str, detail: str = "") -> None:
        await events.put({"type": "step", "variant": "chain", "name": name, "kind": kind, "status": status, "detail": detail})

    # ① Outline, behind a gate. One retry, telling the model what was wrong.
    messages = [{"role": "system", "content": OUTLINE_SYSTEM}, {"role": "user", "content": topic}]
    outline = None
    for attempt in (1, 2):
        await step(f"① outline (attempt {attempt})", "llm", "running")
        reply = await complete(model, messages, mock=_mock_outline)
        try:
            outline = _parse_outline(reply.content)
            await step(f"① outline (attempt {attempt})", "llm", "ok", " · ".join(s["heading"] for s in outline["sections"]))
            await step("gate: valid outline", "gate", "ok", "JSON with title, 3 sections, takeaway")
            break
        except Exception as exc:
            await step(f"① outline (attempt {attempt})", "llm", "ok")
            await step("gate: valid outline", "gate", "fail", str(exc)[:160])
            messages += [reply.as_message(), {"role": "user", "content": f"Invalid: {exc}. Reply again with ONLY the JSON."}]
    if outline is None:
        raise RuntimeError("outline failed the gate twice")

    # ② Sections in parallel — independent sub-tasks, so no reason to wait.
    async def write(i: int, sec: dict[str, Any]) -> str:
        name = f"② section {i}: {sec['heading']}"
        await step(name, "llm", "running")
        prompt = (f"Explainer title: {outline['title']}\nThis section: {sec['heading']}\n"
                  f"Explain this one idea: {sec['point']}")
        text = (await complete(model, [{"role": "system", "content": SECTION_SYSTEM},
                                       {"role": "user", "content": prompt}], mock=_mock_section)).content.strip()
        await step(name, "llm", "ok", f"{len(text.split())} words")
        return text

    bodies = await asyncio.gather(*(write(i, s) for i, s in enumerate(outline["sections"], 1)))
    text = "\n\n".join(
        [f"# {outline['title']}"]
        + [f"## {s['heading']}\n{b}" for s, b in zip(outline["sections"], bodies)]
        + [f"Key takeaway: {outline['takeaway'].removeprefix('Key takeaway:').strip()}"]
    )
    await step("assemble", "code", "ok", "title + 3 sections + takeaway")

    # ③ Full-spec gate; targeted fixes for whatever failed.
    for round_ in range(CHAIN_FIX_ROUNDS + 1):
        failed = [c for c in check(text) if not c.ok]
        await step(f"gate: full spec{' (recheck)' if round_ else ''}", "gate",
                   "ok" if not failed else "fail",
                   "all checks pass" if not failed else "; ".join(f"{c.name}: {c.detail}" for c in failed))
        if not failed or round_ == CHAIN_FIX_ROUNDS:
            break
        await step(f"③ fix (round {round_ + 1})", "llm", "running")
        issues = "\n".join(f"- {c.name}: {c.detail}" for c in failed)
        text = (await complete(model, [{"role": "system", "content": FIX_SYSTEM},
                                       {"role": "user", "content": f"Failed checks:\n{issues}\n\nExplainer:\n{text}"}],
                               mock=_mock_fix)).content.strip()
        await step(f"③ fix (round {round_ + 1})", "llm", "ok", f"{len(text.split())} words")
    return text


async def run_compare(topic: str, events: asyncio.Queue) -> None:
    """Both strategies in parallel; each scored by the same code checks.
    Always ends with done/error."""
    try:
        model = chain_model()
        await events.put({"type": "run_started", "topic": topic, "model": model.label})

        async def one(name: str, fn) -> dict[str, Any]:
            try:
                text, usage = await metered(fn(topic, events, model))
                checks = [c.__dict__ for c in check(text)]
                result = {"variant": name, "text": text, "usage": usage, "checks": checks,
                          "passed": sum(c["ok"] for c in checks), "total": len(checks)}
            except Exception as exc:
                result = {"variant": name, "error": str(exc)[:300]}
            await events.put({"type": "variant_finished", **result})
            return result

        results = await asyncio.gather(one("single", run_single), one("chain", run_chain))
        await events.put({"type": "done", "results": {r["variant"]: r for r in results}})
    except Exception as exc:
        await events.put({"type": "error", "message": f"Chain error: {exc}"})
