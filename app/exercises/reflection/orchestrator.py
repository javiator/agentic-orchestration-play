"""Reflection: generate → critique → revise, until good enough.

    task ──▶ WRITER ──▶ draft v1
               ▲           │
               │           ▼
               │        CRITIC: score 1–10 + concrete issues (JSON)
               │           │
               │     stop? ├─ score ≥ target          → good enough
               │           ├─ no better than before   → plateau
               │           └─ rounds used up          → budget
               │           │ no
               └── revise ◀┘  (writer gets the draft + the issues)

    result = the BEST-scoring version, not necessarily the last —
             a revision can make things worse.

What to watch:
  * DIMINISHING RETURNS — most of the gain is usually in round 1; later
    rounds cost the same and add little.
  * WHO CRITIQUES — a model grading its own work tends to be generous
    ("self" mode). A different model ("independent") is usually stricter.
    The critic is a model too: its score is an opinion, not ground truth.
  * Feedback quality decides everything: vague critiques ("make it better")
    produce churn; specific, checkable issues produce real fixes.
"""

import asyncio
import json
import os
import re
from typing import Any

from app.core.config import ModelSpec, load_judge_model, load_model, load_research_models
from app.core.llm import complete, last_user_text, metered

MAX_ROUNDS = max(1, int(os.getenv("REFLECTION_MAX_ROUNDS", "3")))
TARGET_SCORE = int(os.getenv("REFLECTION_TARGET", "8"))


def writer_model() -> ModelSpec:
    return load_model("REFLECTION_WRITER_MODEL") or load_research_models()[0]


def critic_model(mode: str) -> ModelSpec:
    if mode == "self":
        return writer_model()
    return load_model("REFLECTION_CRITIC_MODEL") or load_judge_model()


WRITER_SYSTEM = "Complete the user's task. Follow every constraint in it exactly. Output only the result."

CRITIC_SYSTEM = """\
You are a demanding reviewer. Score the response to the task from 1 to 10:
  10 = flawless: correct, complete, clear, every constraint met exactly
   7 = good, but with specific fixable issues
   4 = significant problems (wrong facts, missed constraints)
   1 = unusable
Check every explicit constraint (length, count, audience, format) literally.
List concrete issues, each saying what is wrong AND how to fix it. No praise.

Respond with ONLY JSON:
{"score": <1-10>, "issues": ["<issue + fix>", ...]}"""

REVISE_PROMPT = """\
Task:
{task}

Your previous response:
{draft}

A reviewer scored it {score}/10 and found these issues:
{issues}

Rewrite the response to fix every issue while keeping what was good.
Output only the new response."""


# ── Mock mode: drafts that improve, a critic whose score rises ──────────────


def _mock_write(messages: list[dict[str, Any]], tools: Any) -> str:
    text = messages[-1]["content"]
    n = text.count("Rewrite the response") and (text.count("(revision") + 1)
    base = f"(mock draft) A first attempt at: {last_user_text(messages[:2], 80)}. It covers the main idea but is a bit long and vague."
    if not n:
        return base
    return (f"(mock draft) A tighter answer to the task, with a concrete example and the length "
            f"constraint respected. (revision {n})")


def _mock_critique(messages: list[dict[str, Any]], tools: Any) -> str:
    draft = messages[-1]["content"]
    rev = int(m.group(1)) if (m := re.search(r"\(revision (\d+)\)", draft)) else 0
    score = min(9, 5 + 2 * rev - (rev > 1))  # 5 → 7 → 8: shrinking gains
    issues = [] if score >= 9 else [
        "(mock) Too long for the stated limit — cut the second sentence.",
        "(mock) No concrete example — add one everyday analogy.",
    ][: 2 - min(rev, 1)]
    return json.dumps({"score": score, "issues": issues})


async def _critique(critic: ModelSpec, task: str, draft: str) -> tuple[int, list[str]]:
    reply = await complete(
        critic,
        [{"role": "system", "content": CRITIC_SYSTEM},
         {"role": "user", "content": f"Task:\n{task}\n\nResponse to review:\n{draft}"}],
        mock=_mock_critique,
    )
    match = re.search(r"\{.*\}", reply.content, re.DOTALL)
    data = json.loads(match.group(0) if match else reply.content)
    score = max(1, min(10, int(data["score"])))
    issues = [str(i) for i in data.get("issues", [])][:8]
    return score, issues


async def run_reflection(task: str, mode: str, events: asyncio.Queue) -> None:
    """Pushes version/critique events; always ends with done/error."""
    try:
        writer, critic = writer_model(), critic_model(mode)
        await events.put({"type": "run_started", "task": task, "writer": writer.label,
                          "critic": critic.label, "mode": mode, "max_rounds": MAX_ROUNDS,
                          "target": TARGET_SCORE})

        async def loop() -> dict[str, Any]:
            versions: list[dict[str, Any]] = []
            draft = (await complete(writer, [{"role": "system", "content": WRITER_SYSTEM},
                                             {"role": "user", "content": task}], mock=_mock_write)).content.strip()
            stop = "budget"
            for round_ in range(1, MAX_ROUNDS + 1):
                await events.put({"type": "version", "n": round_, "text": draft})
                try:
                    score, issues = await _critique(critic, task, draft)
                except Exception as exc:  # unusable critique: keep what we have
                    await events.put({"type": "critique", "n": round_, "score": None, "issues": [],
                                      "note": f"Critique unusable: {str(exc)[:120]}"})
                    stop = "critic failed"
                    versions.append({"n": round_, "text": draft, "score": None})
                    break
                versions.append({"n": round_, "text": draft, "score": score})
                await events.put({"type": "critique", "n": round_, "score": score, "issues": issues})
                prev = versions[-2]["score"] if len(versions) > 1 else None
                if score >= TARGET_SCORE:
                    stop = "good enough"
                    break
                if prev is not None and score <= prev:
                    stop = "plateau"
                    break
                if round_ == MAX_ROUNDS:
                    break
                await events.put({"type": "revising", "n": round_ + 1})
                prompt = REVISE_PROMPT.format(task=task, draft=draft, score=score,
                                              issues="\n".join(f"- {i}" for i in issues) or "- (none listed)")
                draft = (await complete(writer, [{"role": "system", "content": WRITER_SYSTEM},
                                                 {"role": "user", "content": prompt}], mock=_mock_write)).content.strip()
            scored = [v for v in versions if v["score"] is not None]
            best = max(scored, key=lambda v: (v["score"], v["n"])) if scored else versions[-1]
            return {"best": best, "stop": stop, "scores": [v["score"] for v in versions]}

        result, usage = await metered(loop())
        await events.put({"type": "done", **result, "usage": usage})
    except Exception as exc:
        await events.put({"type": "error", "message": f"Reflection error: {exc}"})
