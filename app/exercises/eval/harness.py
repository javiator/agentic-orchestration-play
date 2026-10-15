"""Evaluation harness: measure orchestration instead of eyeballing it.

    dataset (questions + expected answers / rubrics)
          │
          ▼            for every (target, question) pair, limited concurrency
    ┌───────────┐   ┌─────────────────────────────────────────────────────┐
    │  TARGETS  │──▶│ run target → answer        (calls + tokens counted) │
    │ single    │   │ exact-match check          (when `expected` given)  │
    │ models,   │   │ LLM grader → 1–5 + reason  (rubric / reference)     │
    │ exercises │   └─────────────────────────────────────────────────────┘
    └───────────┘                       │
                                        ▼
               per-target summary: accuracy · grade · latency · cost

Targets are (a) each MODEL_N answering alone — the baseline every pattern
must beat — and (b) every exercise that exposes `Exercise.run`.

Two scorers, on purpose:
  * MATCH is cheap, deterministic and objective — but only works for short
    factual answers, and can be fooled by an answer that mentions the right
    value while concluding something else.
  * GRADER (LLM-as-judge) handles open-ended answers — but is itself a model:
    it has biases (favours longer answers, may favour its own provider's
    style) and some noise. Use a strong grader, ideally from a provider that
    isn't also a candidate, and read its reasons, not just its scores.
COST (model calls, tokens) and LATENCY are the price of each pattern: a
multi-agent setup that scores the same as one model at 5× the calls is a loss.
"""

import asyncio
import json
import os
import re
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.core.config import ModelSpec, load_judge_model, load_model, load_research_models
from app.core.llm import complete, track_usage

from . import store

DATASET_DIR = Path(__file__).parent / "datasets"
EVAL_CONCURRENCY = max(1, int(os.getenv("EVAL_CONCURRENCY", "2")))


def load_grader_model() -> ModelSpec:
    return load_model("GRADER_MODEL") or load_judge_model()


# ── Datasets ────────────────────────────────────────────────────────────────


def list_datasets() -> list[dict[str, Any]]:
    out = []
    for path in sorted(DATASET_DIR.glob("*.json")):
        data = json.loads(path.read_text())
        out.append(
            {"id": path.stem, "name": data.get("name", path.stem),
             "description": data.get("description", ""), "count": len(data["questions"])}
        )
    return out


def load_dataset(dataset_id: str) -> dict[str, Any]:
    if not re.fullmatch(r"[\w-]+", dataset_id):
        raise ValueError(f"Bad dataset id: {dataset_id!r}")
    path = DATASET_DIR / f"{dataset_id}.json"
    if not path.is_file():
        raise ValueError(f"Unknown dataset: {dataset_id}")
    return json.loads(path.read_text())


# ── Targets ─────────────────────────────────────────────────────────────────

BASELINE_SYSTEM = "Answer the user's question accurately. Be concise but complete."


@dataclass(frozen=True)
class Target:
    id: str
    label: str
    run: Callable[[str], Awaitable[str]]


def _single_model(spec: ModelSpec) -> Callable[[str], Awaitable[str]]:
    async def run(question: str) -> str:
        messages = [
            {"role": "system", "content": BASELINE_SYSTEM},
            {"role": "user", "content": question},
        ]
        return (await complete(spec, messages)).content

    return run


def list_targets() -> list[Target]:
    from app.exercises import discover_exercises  # lazy: this module is itself an exercise

    targets = [
        Target(f"model:{i}", f"{spec.label} (single model)", _single_model(spec))
        for i, spec in enumerate(load_research_models(), start=1)
    ]
    targets += [
        Target(f"exercise:{ex.slug}", ex.title, ex.run)
        for ex in discover_exercises()
        if ex.run is not None
    ]
    return targets


# ── Scoring ─────────────────────────────────────────────────────────────────


def _normalize(text: str) -> str:
    text = text.lower().replace("’", "'")
    text = re.sub(r"(?<=\d),(?=\d{3}\b)", "", text)  # 1,000 → 1000
    return re.sub(r"\s+", " ", text).strip()


def exact_match(answer: str, expected: str | list[str] | None) -> bool | None:
    """True if any acceptable answer appears as a whole word/number in the
    answer. None when the question has no `expected` (not applicable)."""
    if not expected:
        return None
    options = [expected] if isinstance(expected, str) else expected
    haystack = _normalize(answer)
    return any(
        re.search(rf"(?<![\w.]){re.escape(_normalize(opt))}(?![\w])", haystack) for opt in options
    )


GRADER_SYSTEM = """\
You are grading an answer to a question. Score it from 1 to 5:
  5 = fully correct and complete per the reference answer / rubric
  4 = correct with minor omissions
  3 = partially correct, or correct but missing important points
  2 = mostly wrong, with some correct elements
  1 = wrong, missing, or off-topic
Judge correctness and completeness against the reference/rubric only — not
length, tone or formatting. A longer answer is not a better answer.

Respond with ONLY a JSON object: {"score": <1-5>, "reason": "<one sentence>"}"""


def _mock_grade(messages: list[dict[str, Any]], tools: Any) -> str:
    """Mock-mode grader: a stable pseudo-score so the UI has varied numbers."""
    score = 2 + sum(map(ord, messages[-1]["content"])) % 4  # 2..5
    return json.dumps({"score": score, "reason": "(mock) Pseudo-random grade for demo purposes."})


async def grade(question: dict[str, Any], answer: str, grader: ModelSpec) -> tuple[int | None, str]:
    reference = []
    if question.get("expected"):
        exp = question["expected"]
        reference.append(f"Reference answer: {exp if isinstance(exp, str) else ' / '.join(exp)}")
    if question.get("rubric"):
        reference.append(f"Rubric: {question['rubric']}")
    prompt = (
        f"Question:\n{question['question']}\n\n"
        + "\n".join(reference or ["(No reference given — judge factual accuracy.)"])
        + f"\n\nAnswer to grade:\n{answer}"
    )
    try:
        reply = await complete(
            grader,
            [{"role": "system", "content": GRADER_SYSTEM}, {"role": "user", "content": prompt}],
            mock=_mock_grade,
        )
        match = re.search(r"\{.*\}", reply.content, re.DOTALL)
        data = json.loads(match.group(0) if match else reply.content)
        score = int(data["score"])
        if not 1 <= score <= 5:
            raise ValueError(f"score out of range: {score}")
        return score, str(data.get("reason", ""))
    except Exception as exc:
        return None, f"Grader failed: {str(exc)[:200]}"


# ── The run ─────────────────────────────────────────────────────────────────


def summarize(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Aggregate one target's per-question results."""
    ok = [r for r in rows if not r["error"]]
    matched = [r["match"] for r in rows if r["match"] is not None]
    grades = [r["grade"] for r in ok if r["grade"] is not None]

    def avg(values: list[float]) -> float | None:
        return round(sum(values) / len(values), 2) if values else None

    return {
        "questions": len(rows),
        "errors": len(rows) - len(ok),
        "match_accuracy": avg([1.0 if m else 0.0 for m in matched]),
        "match_count": len(matched),
        "grade_avg": avg(grades),
        "latency_avg": avg([r["latency"] for r in ok]),
        "calls_avg": avg([r["calls"] for r in ok]),
        "tokens_avg": avg([r["tokens"] for r in ok]),
    }


async def run_eval(dataset_id: str, target_ids: list[str], events: asyncio.Queue) -> None:
    """Run every selected target over the dataset. Pushes events; always ends
    with 'done' or 'error'."""
    try:
        dataset = load_dataset(dataset_id)
        by_id = {t.id: t for t in list_targets()}
        targets = [by_id[t] for t in target_ids if t in by_id]
        if not targets:
            raise ValueError("No valid targets selected")
        questions = dataset["questions"]
        grader = load_grader_model()
        await events.put(
            {"type": "eval_started", "dataset": dataset.get("name", dataset_id),
             "grader": grader.label, "concurrency": EVAL_CONCURRENCY,
             "targets": [{"id": t.id, "label": t.label} for t in targets],
             "questions": [{"id": q["id"], "question": q["question"],
                            "checkable": bool(q.get("expected"))} for q in questions]}
        )

        semaphore = asyncio.Semaphore(EVAL_CONCURRENCY)
        results: dict[str, list[dict[str, Any]]] = {t.id: [] for t in targets}

        async def run_item(target: Target, q: dict[str, Any]) -> None:
            async with semaphore:
                await events.put({"type": "item_started", "target": target.id, "qid": q["id"]})
                started = time.perf_counter()
                answer, error = "", None
                # Count only the target's own model calls — the grader runs
                # outside this block so it never inflates a target's cost.
                with track_usage() as usage:
                    try:
                        answer = await target.run(q["question"])
                    except Exception as exc:
                        error = "timed out" if isinstance(exc, asyncio.TimeoutError) else str(exc)[:300]
                latency = round(time.perf_counter() - started, 2)
                if error:
                    match, score, reason = None, None, ""
                else:
                    match = exact_match(answer, q.get("expected"))
                    score, reason = await grade(q, answer, grader)
                row = {"qid": q["id"], "question": q["question"], "answer": answer,
                       "error": error, "match": match, "grade": score, "reason": reason,
                       "latency": latency, "calls": usage["calls"], "tokens": usage["tokens"]}
                results[target.id].append(row)
                await events.put({"type": "item_finished", "target": target.id, **row})

        await asyncio.gather(*(run_item(t, q) for t in targets for q in questions))

        summaries = []
        for t in targets:
            rows = sorted(results[t.id], key=lambda r: [q["id"] for q in questions].index(r["qid"]))
            summary = summarize(rows)
            run_id = store.save_run(dataset_id, t.id, t.label, grader.label, summary, rows)
            summaries.append({"target": t.id, "label": t.label, "run_id": run_id, **summary})
        await events.put({"type": "done", "summaries": summaries})
    except Exception as exc:
        await events.put({"type": "error", "message": f"Eval error: {exc}"})
