"""Tools the research agents can call (PHASE 1 — TOOL USE).

Each tool has two parts:
  1. An OpenAI-style JSON schema (the `TOOL_SCHEMAS` list) — this is what the
     model sees. LiteLLM translates this format for every provider, so the
     same schemas work for GPT, Claude, and Gemini alike.
  2. An async executor in `execute_tool()` — what actually runs when the
     model requests a call.

The model decides *whether* and *when* to call tools; we only execute and
return results. Different models make different choices here, which is part
of what the consensus comparison surfaces.
"""

import asyncio
import json
import re
from typing import Any

import httpx

MAX_TOOL_RESULT_CHARS = 6_000  # keep any single tool result from flooding context


def tool_schemas(has_documents: bool) -> list[dict]:
    """Schemas offered to the model. search_documents is only offered when
    the user actually attached files — offering a tool that can't return
    anything just wastes the model's loop iterations."""
    schemas = [
        {
            "type": "function",
            "function": {
                "name": "search_web",
                "description": (
                    "Search the web (DuckDuckGo) for current information. "
                    "Returns titles, URLs and snippets of the top results."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "query": {"type": "string", "description": "Search query"},
                    },
                    "required": ["query"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "fetch_url",
                "description": (
                    "Fetch a web page and return its readable text content. "
                    "Use after search_web to read a promising result in full."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "url": {"type": "string", "description": "Absolute http(s) URL"},
                    },
                    "required": ["url"],
                },
            },
        },
    ]
    if has_documents:
        schemas.append(
            {
                "type": "function",
                "function": {
                    "name": "search_documents",
                    "description": (
                        "Keyword-search the user's attached documents and return "
                        "the most relevant passages."
                    ),
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "query": {"type": "string", "description": "Keywords to look for"},
                        },
                        "required": ["query"],
                    },
                },
            }
        )
    return schemas


async def _search_web(query: str) -> str:
    """DuckDuckGo search via the ddgs package (no API key). The library is
    synchronous, so run it in a thread to keep the fan-out non-blocking."""
    from ddgs import DDGS

    def _run() -> list[dict]:
        with DDGS() as ddgs:
            return list(ddgs.text(query, max_results=5))

    try:
        results = await asyncio.to_thread(_run)
    except Exception as exc:
        return f"search_web failed: {exc}"
    if not results:
        return "No results found."
    return "\n\n".join(
        f"[{i + 1}] {r.get('title', '')}\nURL: {r.get('href', '')}\n{r.get('body', '')}"
        for i, r in enumerate(results)
    )


async def _fetch_url(url: str) -> str:
    """Fetch a page and reduce it to readable text: drop script/style blocks,
    strip tags, collapse whitespace. Crude but dependency-free."""
    if not re.match(r"^https?://", url):
        return "fetch_url failed: only absolute http(s) URLs are supported."
    try:
        async with httpx.AsyncClient(follow_redirects=True, timeout=20) as client:
            resp = await client.get(url, headers={"User-Agent": "consensus-research-agent"})
            resp.raise_for_status()
            html = resp.text
    except Exception as exc:
        return f"fetch_url failed: {exc}"
    text = re.sub(r"(?is)<(script|style|noscript)[^>]*>.*?</\1>", " ", html)
    text = re.sub(r"(?s)<[^>]+>", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text or "Page contained no readable text."


def _search_documents(query: str, files: list[dict[str, str]]) -> str:
    """Tiny keyword retriever over attached files: split into paragraph-ish
    chunks, score each by how many distinct query terms it contains, return
    the top chunks. Deliberately simple — swap in embeddings later if you
    want to learn vector retrieval."""
    terms = {t for t in re.findall(r"\w+", query.lower()) if len(t) > 2}
    if not terms:
        return "Query too short to search with."
    scored: list[tuple[int, str, str]] = []
    for f in files:
        # Chunk on blank lines, then merge to ~1200-char pieces.
        chunks, current = [], ""
        for para in f["content"].split("\n\n"):
            current = f"{current}\n\n{para}" if current else para
            if len(current) >= 1200:
                chunks.append(current)
                current = ""
        if current:
            chunks.append(current)
        for chunk in chunks:
            lower = chunk.lower()
            score = sum(1 for t in terms if t in lower)
            if score:
                scored.append((score, f["name"], chunk))
    if not scored:
        return "No passages matched those keywords."
    scored.sort(key=lambda s: -s[0])
    return "\n\n".join(f"--- from {name} ---\n{chunk}" for _, name, chunk in scored[:4])


async def execute_tool(name: str, arguments: str, files: list[dict[str, str]]) -> str:
    """Dispatch one model-requested tool call. Never raises: errors come back
    as text so the model can read them and adjust course."""
    try:
        args: dict[str, Any] = json.loads(arguments or "{}")
    except json.JSONDecodeError:
        return f"Could not parse tool arguments: {arguments!r}"

    if name == "search_web":
        result = await _search_web(str(args.get("query", "")))
    elif name == "fetch_url":
        result = await _fetch_url(str(args.get("url", "")))
    elif name == "search_documents":
        result = _search_documents(str(args.get("query", "")), files)
    else:
        result = f"Unknown tool: {name}"

    if len(result) > MAX_TOOL_RESULT_CHARS:
        result = result[:MAX_TOOL_RESULT_CHARS] + "\n[... truncated ...]"
    return result
