"""Plain RAG vs agentic RAG — same model, same documents, same search.

PLAIN RAG (a fixed pipeline: always one search, with the user's own words)

    question ──▶ search(question) ──▶ top-k chunks ──▶ model answers, citing chunks

AGENTIC RAG (search is a TOOL; the model decides how to use it)

    question ──▶ ┌──────────────── agent loop (≤ RAG_MAX_STEPS) ───────────────┐
                 │ model: what do I still need?                               │
                 │   ├─ search_docs(query)  ← its own query: one fact at a    │
                 │   │                        time, rephrased when it misses  │
                 │   ├─ read_doc(doc_id)    ← whole document when a snippet   │
                 │   │                        isn't enough                    │
                 │   └─ answer (text)                                         │
                 │        └─▶ GROUNDING CHECK: is every claim supported by    │
                 │             what was retrieved? no → feedback, search more │
                 └────────────────────────────────────────────────────────────┘

Why the agent can win: questions that need several facts from different
documents ("who owns X?" → a ROLE in one doc, the PERSON in another), or
whose wording doesn't match the documents. A single search can't follow a
lead it hasn't read yet.
Why it can lose: more model calls, more latency, more ways to wander. On a
one-fact question both get the same chunk — and plain RAG is far cheaper.

Guards (the same ideas as the supervisor exercise): a step budget whose
last step must answer, a repeated-query guard, and at most RAG_VERIFY_ROUNDS
grounding retries so the check can't loop forever.
"""

import asyncio
import json
import os
import re
import time
from typing import Any

from app.core.config import ModelSpec, load_model, load_research_models
from app.core.llm import LLMReply, complete, last_user_text, mock_tool_call, track_usage

from .retrieval import TOP_K, format_doc, format_hits, get_index, hit_summary, tokenize

RAG_MAX_STEPS = max(2, int(os.getenv("RAG_MAX_STEPS", "6")))
RAG_VERIFY_ROUNDS = max(0, int(os.getenv("RAG_VERIFY_ROUNDS", "1")))


def load_rag_model() -> ModelSpec:
    """Both variants use the SAME model — otherwise you'd be comparing
    models, not retrieval strategies."""
    return load_model("RAG_MODEL") or load_research_models()[0]


CITE_RE = re.compile(r"\[([\w-]+#\d+)\]")

# ── Plain RAG ───────────────────────────────────────────────────────────────

PLAIN_SYSTEM = """\
You answer questions about Tidewell Labs using ONLY the excerpts from its
internal documents given below. Cite every fact inline with its excerpt id,
e.g. [travel-policy#3]. If the excerpts don't contain the answer, say so
plainly and mention what they do say — never guess or use outside knowledge."""


def _mock_plain(messages: list[dict[str, Any]], tools: Any) -> str:
    text = messages[-1]["content"]
    ids = CITE_RE.findall(text)
    if not ids:
        return "(mock) The excerpts don't contain the answer."
    first = text.split(f"[{ids[0]}]", 1)[1].split("\n", 1)[-1]  # skip the title line
    snippet = " ".join(first.split())[:160]
    return f"(mock plain-RAG answer) From the top excerpt: {snippet}… [{ids[0]}]"


async def run_plain(question: str, events: asyncio.Queue, model: ModelSpec) -> dict[str, Any]:
    hits = await get_index().search(question)
    await events.put(
        {"type": "retrieval", "variant": "plain", "step": 1, "query": question,
         "hits": [hit_summary(h) for h in hits]}
    )
    prompt = f"Excerpts:\n\n{format_hits(hits)}\n\nQuestion: {question}"
    reply = await complete(
        model,
        [{"role": "system", "content": PLAIN_SYSTEM}, {"role": "user", "content": prompt}],
        mock=_mock_plain,
    )
    return {"answer": reply.content, "steps": 1, "searches": 1, "finished_by": "pipeline"}


# ── Agentic RAG ─────────────────────────────────────────────────────────────

SEARCH_TOOL = {
    "type": "function",
    "function": {
        "name": "search_docs",
        "description": (
            f"Keyword search over Tidewell Labs' internal documents. Returns the top {TOP_K} "
            "matching passages, each labelled with a citable id like [doc-id#n]."
        ),
        "parameters": {
            "type": "object",
            "properties": {"query": {"type": "string", "description": "Short keyword query — one fact at a time"}},
            "required": ["query"],
        },
    },
}
READ_TOOL = {
    "type": "function",
    "function": {
        "name": "read_doc",
        "description": "Read a whole document by its doc id (the part before '#' in a passage id).",
        "parameters": {
            "type": "object",
            "properties": {"doc_id": {"type": "string", "description": "e.g. travel-policy"}},
            "required": ["doc_id"],
        },
    },
}
TOOLS = [SEARCH_TOOL, READ_TOOL]

AGENT_SYSTEM = """\
You answer questions about Tidewell Labs. Its internal documents are only
visible through your tools.

How to work:
1. Work out which facts the answer needs. Questions often need facts from
   DIFFERENT documents — e.g. a policy names a role, and another document
   names the person in that role. Follow such leads.
2. search_docs with short keyword queries, one fact per search. If results
   miss, rephrase with the words a policy or handbook would likely use —
   don't repeat a query.
3. read_doc when a passage is cut off or you need its surrounding context.
4. Stop searching as soon as every part of the answer is supported.

Answer rules: only facts from the documents; cite each fact inline with its
passage id, e.g. [travel-policy#3]; if the documents don't contain the
answer, say so and say what you did find. Never guess."""

LAST_STEP_NOTE = (
    "Search budget used up. Answer now from the evidence you already have, "
    "without calling any more tools. Say clearly if something stays unanswered."
)

GROUNDING_SYSTEM = """\
You check an answer against the evidence it was based on. Decide:
  (a) is every factual claim in the answer supported by the evidence, and
  (b) does it answer every part of the question?
"The documents don't say" is a valid, supported answer when the evidence
indeed lacks that information.
Respond with ONLY JSON: {"supported": true|false, "missing": "<what is
unsupported or unanswered — empty when supported>"}"""


def _mock_agent(messages: list[dict[str, Any]], tools: Any) -> LLMReply:
    """Scripted agent for mock mode: search, search again with a narrower
    query, read a document only if the grounding check asks for more, then
    answer — so the full loop (incl. a correction round) is visible."""
    question = next(m["content"] for m in messages if m["role"] == "user")
    tool_results = [m["content"] for m in messages if m.get("role") == "tool"]
    feedback = any(m["role"] == "user" and m["content"].startswith("Grounding check") for m in messages)
    read = any(m.get("role") == "assistant" and "read_doc" in json.dumps(m.get("tool_calls") or [])
               for m in messages)
    keywords = [w for w in tokenize(question) if len(w) > 2]
    ids = [i for r in tool_results for i in CITE_RE.findall(r)]
    if tools and len(tool_results) == 0:
        return LLMReply("", [mock_tool_call("search_docs", {"query": " ".join(keywords)})])
    if tools and len(tool_results) == 1:
        return LLMReply("", [mock_tool_call("search_docs", {"query": " ".join(keywords[:3])})])
    if tools and feedback and not read and ids:
        return LLMReply("", [mock_tool_call("read_doc", {"doc_id": ids[0].split("#")[0]})])
    cite = f" [{ids[0]}]" if ids else ""
    return LLMReply(f"(mock agentic answer) Combining what the searches found: {last_user_text(messages[:2], 90)}{cite}")


def _mock_grounding(messages: list[dict[str, Any]], tools: Any) -> str:
    """Mock grounding check: rejects the first answer (no full document read
    yet), accepts after a read — demonstrates the corrective round."""
    if "read_doc result" in messages[-1]["content"]:
        return json.dumps({"supported": True, "missing": ""})
    return json.dumps({"supported": False, "missing": "(mock) Snippets were cut off — read the full document to confirm."})


async def _check_grounding(
    model: ModelSpec, question: str, evidence: list[str], answer: str
) -> tuple[bool, str]:
    evidence_text = "\n\n---\n\n".join(evidence)[-12000:]  # most recent evidence if long
    prompt = f"Question:\n{question}\n\nEvidence retrieved:\n{evidence_text}\n\nAnswer:\n{answer}"
    try:
        reply = await complete(
            model,
            [{"role": "system", "content": GROUNDING_SYSTEM}, {"role": "user", "content": prompt}],
            mock=_mock_grounding,
        )
        match = re.search(r"\{.*\}", reply.content, re.DOTALL)
        data = json.loads(match.group(0) if match else reply.content)
        return bool(data.get("supported")), str(data.get("missing", ""))
    except Exception as exc:
        # An unparseable check shouldn't block an answer — accept and say so.
        return True, f"(grounding check failed: {str(exc)[:120]})"


async def _execute(name: str, args: dict[str, Any], seen_queries: set[str]) -> tuple[str, dict[str, Any]]:
    """Run one tool call → (text for the model, event payload for the UI)."""
    index = get_index()
    if name == "search_docs":
        query = str(args.get("query", "")).strip()
        key = " ".join(sorted(tokenize(query)))
        if key in seen_queries:
            return (
                "You already ran this search (same keywords). Rephrase, search for a "
                "different fact, read a document, or answer.",
                {"tool": "search_docs", "query": query, "repeated": True, "hits": []},
            )
        seen_queries.add(key)
        hits = await index.search(query)
        return format_hits(hits), {"tool": "search_docs", "query": query, "hits": [hit_summary(h) for h in hits]}
    if name == "read_doc":
        doc_id = str(args.get("doc_id", "")).split("#")[0].strip()
        text = format_doc(doc_id)
        if text is None:
            return f"No document with id {doc_id!r}.", {"tool": "read_doc", "doc_id": doc_id, "found": False}
        title = text.splitlines()[0].lstrip("# ")
        return f"read_doc result:\n{text}", {"tool": "read_doc", "doc_id": doc_id, "title": title, "found": True}
    return f"Unknown tool {name!r}.", {"tool": name, "unknown": True}


async def run_agentic(question: str, events: asyncio.Queue, model: ModelSpec) -> dict[str, Any]:
    messages: list[dict[str, Any]] = [
        {"role": "system", "content": AGENT_SYSTEM},
        {"role": "user", "content": question},
    ]
    evidence: list[str] = []
    seen_queries: set[str] = set()
    searches = verify_rounds = 0
    step = 0
    while True:
        step += 1
        last = step >= RAG_MAX_STEPS
        if last:
            messages.append({"role": "user", "content": LAST_STEP_NOTE})
        # Tools stay offered even on the last step (some providers reject a
        # tool-less request once the history contains tool calls); the note
        # tells the model to stop, and a call made anyway ends the run below.
        reply = await complete(model, messages, tools=TOOLS, mock=_mock_agent)

        if reply.tool_calls and last:
            return {"answer": reply.content or "Ran out of search steps before answering.",
                    "steps": step, "searches": searches, "finished_by": "step_limit"}

        if reply.tool_calls:
            messages.append(reply.as_message())
            for tc in reply.tool_calls:
                try:
                    args = json.loads(tc.arguments or "{}")
                except json.JSONDecodeError:
                    args = {}
                result, payload = await _execute(tc.name, args, seen_queries)
                searches += tc.name == "search_docs" and not payload.get("repeated")
                evidence.append(result)
                messages.append({"role": "tool", "tool_call_id": tc.id, "content": result})
                await events.put({"type": "agent_step", "variant": "agentic", "step": step, **payload})
            continue

        # A text reply is a candidate answer. GROUNDING CHECK: a second look,
        # by the same model, at whether the evidence really supports it — the
        # "corrective" part of agentic RAG. Only while budget remains.
        answer = reply.content
        if verify_rounds < RAG_VERIFY_ROUNDS and not last:
            verify_rounds += 1
            supported, missing = await _check_grounding(model, question, evidence, answer)
            await events.put({"type": "grounding", "variant": "agentic", "step": step,
                              "supported": supported, "missing": missing})
            if not supported:
                messages.append(reply.as_message())
                messages.append({"role": "user", "content": (
                    f"Grounding check: your answer isn't fully supported by the evidence. "
                    f"Issue: {missing} Search or read more as needed, then answer again."
                )})
                continue
        return {"answer": answer, "steps": step, "searches": searches,
                "finished_by": "answer"}


# ── Running both, side by side ──────────────────────────────────────────────

VARIANTS = {"plain": run_plain, "agentic": run_agentic}


async def _run_variant(name: str, question: str, events: asyncio.Queue, model: ModelSpec) -> dict[str, Any]:
    """One variant with its own timing and usage counter (gather gives each
    its own task, so the counters don't mix). Failures are contained."""
    started = time.perf_counter()
    with track_usage() as usage:
        try:
            result = await VARIANTS[name](question, events, model)
            error = None
        except Exception as exc:
            result, error = {}, ("timed out" if isinstance(exc, asyncio.TimeoutError) else str(exc)[:300])
    summary = {"variant": name, "latency": round(time.perf_counter() - started, 2),
               "calls": usage["calls"], "tokens": usage["tokens"], "error": error, **result}
    await events.put({"type": "variant_finished", **summary})
    return summary


async def run_compare(question: str, events: asyncio.Queue, variants: tuple[str, ...] = ("plain", "agentic")) -> None:
    """Run the selected variants in parallel. Always ends with done/error."""
    try:
        model = load_rag_model()
        index = get_index()
        await events.put({"type": "run_started", "question": question, "model": model.label,
                          "backend": index.backend, "variants": list(variants)})
        results = await asyncio.gather(*(_run_variant(v, question, events, model) for v in variants))
        await events.put({"type": "done", "results": {r["variant"]: r for r in results}})
    except Exception as exc:
        await events.put({"type": "error", "message": f"RAG error: {exc}"})
