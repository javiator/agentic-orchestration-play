"""Context management: what to send the model as a conversation grows.

A model only "remembers" what is in the request. Every strategy below
replays the SAME scripted conversation (conversation.json, with facts
planted early, mid-way, late — and one fact that CHANGES), then asks probe
questions about those facts.

  FULL HISTORY   send everything.            Best recall; cost grows every
                                             turn, and long contexts dilute attention.
  SLIDING WINDOW send the last N messages.   Cheap and flat; forgets anything
                                             older than the window, completely.
  SUMMARY        every K messages, a model   Middle ground; summaries keep the
                 call folds older messages   gist but drop specifics (numbers,
                 into a running summary;     names) unless told to keep them.
                 keep the last few raw.
  NOTES          every K messages, a model   Precise on facts it decided to keep
                 call updates a list of      (and can overwrite changed ones);
                 durable facts; keep the     misses details nobody flagged as
                 last few raw.               important. The idea behind "memory" features.

Scripted assistant turns are replayed, not generated — only compaction
calls and probe answers hit the model, so the comparison is cheap and every
strategy sees exactly the same conversation.
Token counts are estimates (~4 characters per token), equal for all.
"""

import asyncio
import json
import os
import re
from pathlib import Path
from typing import Any

from app.core.config import ModelSpec, load_model, load_research_models
from app.core.llm import complete, metered

SCRIPT = json.loads((Path(__file__).parent / "conversation.json").read_text())
WINDOW = max(2, int(os.getenv("CONTEXT_WINDOW_MESSAGES", "6")))
COMPACT_EVERY = max(2, int(os.getenv("CONTEXT_COMPACT_EVERY", "8")))
KEEP_RECENT = max(0, int(os.getenv("CONTEXT_KEEP_RECENT", "4")))

STRATEGIES = {
    "full": "Full history",
    "window": f"Sliding window (last {WINDOW} messages)",
    "summary": f"Running summary (+ last {KEEP_RECENT} messages)",
    "notes": f"Fact notes (+ last {KEEP_RECENT} messages)",
}


def context_model() -> ModelSpec:
    return load_model("CONTEXT_MODEL") or load_research_models()[0]


ASSISTANT_SYSTEM = "You are a helpful travel-planning assistant. Answer briefly, using what you know about this user from the conversation."

SUMMARY_SYSTEM = """\
Update the running summary of a conversation between a user and a travel
assistant. Merge the previous summary with the new messages into one concise
summary (max ~150 words). Output only the summary."""

NOTES_SYSTEM = """\
Maintain a list of durable facts about the user for future turns: plans,
constraints, preferences, people, numbers, decisions. Merge the current
notes with the new messages; when a fact changes, REPLACE the old value.
Output only the updated notes, one fact per line starting with "- "."""


def tokens(messages: list[dict[str, Any]]) -> int:
    return sum(len(str(m["content"])) for m in messages) // 4


def _fmt(messages: list[dict[str, Any]]) -> str:
    return "\n".join(f"{m['role']}: {m['content']}" for m in messages)


# ── Mock mode: imperfect, rule-based compaction + a "reader" ─────────────────


def _mock_summary(messages: list[dict[str, Any]], tools: Any) -> str:
    """Keeps the first sentence of each user message — like a real summary,
    it keeps the gist and drops details buried later in a sentence."""
    text = messages[-1]["content"]
    prev = text.split("Previous summary:\n", 1)[-1].split("\n\nNew messages:", 1)[0]
    firsts = [re.split(r"(?<=[.!?—])\s", line[len("user: "):])[0]
              for line in text.splitlines() if line.startswith("user: ")]
    return " ".join(x for x in [prev if prev != "(none)" else "", *firsts] if x)[:900]


def _mock_notes(messages: list[dict[str, Any]], tools: Any) -> str:
    """Keeps user sentences that mention a number, a name or a constraint."""
    text = messages[-1]["content"]
    prev = text.split("Current notes:\n", 1)[-1].split("\n\nNew messages:", 1)[0]
    notes = [l for l in prev.splitlines() if l.startswith("- ")]
    for line in text.splitlines():
        if line.startswith("user: "):
            for s in re.split(r"(?<=[.!?])\s", line[6:]):
                if re.search(r"\d|allerg|vegetarian|sister|skip", s, re.I):
                    if "budget" in s.lower():
                        notes = [n for n in notes if "budget" not in n.lower()]  # replace changed fact
                    notes.append(f"- {s.strip()}")
    return "\n".join(notes)


def _mock_answer(messages: list[dict[str, Any]], tools: Any) -> str:
    """Answers with the context sentence sharing the most words with the
    question — so it can only 'know' what the strategy actually sent."""
    stems = lambda text: {w[:5] for w in re.findall(r"[a-z0-9]+", text.lower()) if len(w) > 3}
    qwords = stems(messages[-1]["content"]) - {"what", "which", "where"}
    best, score = "I don't know — that isn't in what I can see.", 0
    sources = [messages[0]["content"].split("\n\n", 1)[-1]] + [str(m["content"]) for m in messages[1:-1]]
    for content in sources:
        for s in re.split(r"(?<=[.!?])\s|\n", content):
            overlap = len(qwords & stems(s))
            if overlap > score or (overlap == score and overlap and "you" not in s.lower()):
                best, score = s.strip(), overlap
    return f"(mock) {best}"


# ── Replaying the conversation under one strategy ───────────────────────────


async def _compact(kind: str, model: ModelSpec, state: str, chunk: list[dict[str, Any]]) -> str:
    if kind == "summary":
        prompt = f"Previous summary:\n{state or '(none)'}\n\nNew messages:\n{_fmt(chunk)}"
        system, mock = SUMMARY_SYSTEM, _mock_summary
    else:
        prompt = f"Current notes:\n{state or '(none)'}\n\nNew messages:\n{_fmt(chunk)}"
        system, mock = NOTES_SYSTEM, _mock_notes
    reply = await complete(model, [{"role": "system", "content": system}, {"role": "user", "content": prompt}], mock=mock)
    return reply.content.strip()


def _context(kind: str, history: list[dict[str, Any]], raw: list[dict[str, Any]], state: str) -> list[dict[str, Any]]:
    """The messages this strategy would send at this point (before a new user turn)."""
    system = {"role": "system", "content": ASSISTANT_SYSTEM}
    if kind == "full":
        return [system, *history]
    if kind == "window":
        return [system, *history[-WINDOW:]]
    if state:
        label = "Summary of the earlier conversation" if kind == "summary" else "Notes about the user"
        system = {"role": "system", "content": f"{ASSISTANT_SYSTEM}\n\n{label}:\n{state}"}
    return [system, *raw]


async def run_strategy(kind: str, model: ModelSpec, events: asyncio.Queue) -> dict[str, Any]:
    history: list[dict[str, Any]] = []
    raw: list[dict[str, Any]] = []  # messages not yet folded into summary/notes
    state = ""
    per_turn: list[int] = []  # context tokens sent at each user turn
    for i, msg in enumerate(SCRIPT["messages"], 1):
        if msg["role"] == "user":
            per_turn.append(tokens(_context(kind, history, raw, state) + [msg]))
        history.append(msg)
        raw.append(msg)
        if kind in ("summary", "notes") and len(raw) >= COMPACT_EVERY:
            fold, raw = raw[:-KEEP_RECENT] if KEEP_RECENT else raw, raw[-KEEP_RECENT:] if KEEP_RECENT else []
            state = await _compact(kind, model, state, fold)
            await events.put({"type": "compaction", "strategy": kind, "after_message": i,
                              "folded": len(fold), "state": state})

    final_context = _context(kind, history, raw, state)

    async def probe(p: dict[str, Any]) -> dict[str, Any]:
        msgs = final_context + [{"role": "user", "content": p["question"]}]
        answer = (await complete(model, msgs, mock=_mock_answer)).content
        correct = any(e.lower() in answer.lower() for e in p["expected"])
        result = {"probe": p["id"], "answer": answer, "correct": correct}
        await events.put({"type": "probe_result", "strategy": kind, **result})
        return result

    results = await asyncio.gather(*(probe(p) for p in SCRIPT["probes"]))
    return {"per_turn": per_turn, "final_tokens": tokens(final_context),
            "total_tokens": sum(per_turn), "state": state,
            "score": sum(r["correct"] for r in results), "probes": len(results)}


async def run_all(events: asyncio.Queue, kinds: list[str] | None = None) -> None:
    """All strategies in parallel. Always ends with done/error."""
    try:
        model = context_model()
        kinds = [k for k in (kinds or list(STRATEGIES)) if k in STRATEGIES]
        await events.put({"type": "run_started", "model": model.label, "strategies": kinds})

        async def one(kind: str) -> dict[str, Any]:
            try:
                result, usage = await metered(run_strategy(kind, model, events))
                out = {"strategy": kind, **result, "usage": usage}
            except Exception as exc:
                out = {"strategy": kind, "error": str(exc)[:300]}
            await events.put({"type": "strategy_finished", **out})
            return out

        results = await asyncio.gather(*(one(k) for k in kinds))
        await events.put({"type": "done", "results": {r["strategy"]: r for r in results}})
    except Exception as exc:
        await events.put({"type": "error", "message": f"Context error: {exc}"})
