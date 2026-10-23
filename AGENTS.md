# AGENTS.md — project guide for future development

Multi-model research consensus app: one query fans out to N LLMs in parallel
(LiteLLM), a separate judge model merges the answers and flags disagreements,
a no-build-step web UI streams progress live via SSE. Three agentic features
are layered on top, each toggleable per run: tool use (ReAct loop), a rebuttal
round (agents defend/revise against each other's claims), and memory (session
follow-ups + long-term SQLite recall).

The repo is a playground of **exercises**: each is a self-contained module in
`app/exercises/<name>/` mounted at `/<slug>/`, sharing the building blocks in
`app/core/`. The consensus app above is the `consensus` exercise. The root
app (`app/main.py`) auto-discovers exercises and serves a landing page at `/`
to pick one; every exercise page includes `/static/nav.js` (home link +
switcher). To add one: `cp -r app/exercises/_template app/exercises/<name>`,
set a unique `slug` in its `__init__.py`, restart. Folders starting with `_`
are skipped. Exercise pages use relative URLs (`api/...`, `static/...`) so the
slug lives only in `EXERCISE`.

Exercises: `consensus` (static fan-out + judge), `supervisor` (a
supervisor LLM delegates to researcher/analyst/writer specialists via a
`delegate` tool, shared scratchpad, turn cap + repeat guard — see the
diagram in `app/exercises/supervisor/orchestrator.py`), `rag` (plain RAG vs
agentic RAG side by side over a fictional handbook in
`app/exercises/rag/corpus/`; shared BM25/hybrid search in `retrieval.py`,
both pipelines + grounding check in `pipelines.py`), and `eval` (runs
single-model baselines + every exercise with `Exercise.run` over a JSON
question set in `app/exercises/eval/datasets/`; scores exact match + LLM
grader 1–5, latency, calls/tokens; results in `eval.db`, gitignored).

Shared plumbing every exercise should use:
- `app/core/llm.py` — `complete()` is the ONLY place that calls LiteLLM.
  Returns `LLMReply` (text + normalized tool calls; `as_message()` appends
  it to a conversation). Pass `mock=` when a caller needs a specific shape in
  mock mode (the judge's JSON, the supervisor's scripted plan).
- `app/core/jobs.py` — `JobRegistry.start(pipeline)` / `.stream(job_id)` for
  background runs relayed as SSE; `run_to_completion()` for headless runs.
- `track_usage()` in `llm.py` — counts calls/tokens of everything run inside
  it (ContextVar, so gathered sub-tasks count too); trackers nest, so an
  exercise can meter its parts while the eval meters the whole run.
- `embed()` in `llm.py` — embeddings via LiteLLM (not mocked; the rag
  exercise falls back to BM25 without it).
- `Exercise.run` (optional) — `async (question) -> answer` without a UI; the
  eval harness lists every exercise that sets it as a target;
  `Exercise.variants` adds more `(name, run)` targets (rag: plain/agentic).
  Headless runs
  must not write to user-facing stores (consensus passes
  `RunOptions(remember=False)`).
- `/static/util.js` — `$`, `esc`, `mdLite`, `show`, `hide` for exercise pages.
- **Mock mode** (`MOCK_MODELS=true`, works without `.env`): canned replies +
  fake tool results. Use it to verify UI/flow changes with no keys; it does
  not replace a real end-to-end run.

## Run / verify

```bash
./start.sh          # venv + deps on first run; serves http://localhost:8000, logs to server.log
./stop.sh
```

Requires a `.env` (copy `.env.example`) with `MODEL_1..N`, `JUDGE_MODEL`, and
matching provider API keys. There is no test suite; verify changes by running
a real query end-to-end:

```bash
# toggles/session are optional form fields; booleans as "true"/"false"
curl -s -X POST localhost:8000/consensus/api/query -F 'query=...' \
  -F 'use_tools=true' -F 'rebuttal=true' -F 'use_memory=true' -F 'session_id=...'
# → {"job_id": ..., "session_id": ...}
curl -s -N "localhost:8000/consensus/api/stream/<job_id>?session_id=<sid>&query=<q>"   # SSE until done/error
```

## Architecture (read in this order)

1. **`app/core/config.py`** — models are `.env` data, never code. `ModelSpec` =
   LiteLLM model string + optional `api_base`/`api_key` override. Research
   models are `MODEL_1..N` (contiguous numbering — a gap stops the scan).
2. **`app/exercises/consensus/orchestrator.py`** — the pipeline, `run_consensus(..., options)`:
   - `RunOptions` = per-run toggles (`use_tools`, `rebuttal`, `use_memory`)
     plus `history` (session turns).
   - `_build_user_message()` builds ONE message (recalled memories + session
     history + query + context + parsed files) reused for every agent — this
     identical-input property keeps the comparison fair. Don't give agents
     divergent inputs.
   - Stage 1 fan-out: `asyncio.gather` over `_run_research_agent()`.
   - Stage 2 independence: tools off → one `complete()` call; tools on →
     `run_agent_loop()` (still isolated per agent). ALL exceptions are caught
     inside the agent and returned as `{ok: False, error}` so one provider
     failure never sinks the run.
   - Stage 3 judge: `_run_judge()` is its own model call. It gets successful
     answers verbatim plus an explicit failed-agent list, and must return the
     JSON shape in `JUDGE_SYSTEM`; `_parse_judge_json()` defensively strips
     fences and falls back to raw-text-as-merged-answer.
   - Stage 4 (optional) rebuttal: only when round-1 disagreements exist.
     Agents that fail their rebuttal keep their round-1 answer, so round 2
     never has fewer voices than round 1. Judge runs again with `round=2`.
   - Memory: `remember()` runs after every verdict (unless
     `RunOptions.remember=False`, used by headless/eval runs);
     `recall()` only when `use_memory` — so the store is already populated
     the first time the user enables the toggle.
   - All progress goes onto an `asyncio.Queue` as event dicts. Event types:
     `memory_recalled`, `agent_started`, `tool_call`, `agent_finished` (with
     `phase: research|rebuttal`), `fanout_complete`, `rebuttal_started`,
     `judge_started` (with `round`), `done`, `error`. The stream always
     terminates with `done` or `error` — preserve that invariant or the SSE
     reader hangs. `done` carries `results`, `verdict`, and (when rebuttal
     ran) `initial_verdict` + `rebuttal_results`.
3. **`app/core/agent_loop.py`** — the ReAct loop, heavily commented: completion
   with `tools=` → if `tool_calls`, execute + append `role:"tool"` results →
   repeat. The final allowed iteration passes `tools=None` to force an
   answer. The `messages` list is the agent's working memory.
4. **`app/core/tools.py`** — OpenAI-style schemas + async executors
   (`search_web`/ddgs, `fetch_url`/httpx, `search_documents`/keyword chunks).
   Executors never raise — errors return as text the model can react to.
   `search_documents` is only offered when files are attached. To add a tool:
   schema in `tool_schemas()`, branch in `execute_tool()` — nothing else.
5. **`app/exercises/consensus/memory.py`** — SQLite (`memory.db` at repo
   root, gitignored). Verdict-shaped, so it lives with the consensus exercise.
   Keyword-overlap `recall()` by design; swap in embeddings without touching
   callers. Recalled items are framed as "verify before relying on them".
6. **`app/exercises/consensus/routes.py`** — FastAPI router mounted at
   `/consensus/`. Serves the page, parses uploads, resolves the session
   (`_sessions` dict: session_id → prior turns), spawns `run_consensus` as a
   task, relays the queue as SSE. The SSE relay appends the finished turn to
   session memory when it sees `done`. Both stores are in-memory dicts —
   single-process, single-user by design.
7. **`app/core/files.py`** — upload parsing (txt/md/csv/pdf), truncated at
   `MAX_CHARS_PER_FILE`.
8. **`app/exercises/consensus/static/`** — vanilla HTML/JS/CSS, intentionally
   no framework or build step. `app.js` mirrors the event types above; if you
   add an event type, handle it in `es.onmessage` there. Shared look
   (`base.css`), nav bar and landing page live in `app/static/`.
9. **`app/main.py`** + **`app/exercises/__init__.py`** + **`app/core/exercise.py`**
   — root app, discovery, and the `Exercise` contract each exercise exports
   as `EXERCISE`.

## Conventions & gotchas

- **Provider-agnostic is the core constraint.** Adding a provider must never
  require code changes — only `.env`. If a change breaks that, rethink it.
- Judge ↔ UI contract: the verdict JSON keys (`merged_answer`,
  `disagreements[].topic/positions[].model/claim`, `agreement_summary`,
  `omissions`, `round`) are consumed directly by `renderDone()` in `app.js`.
  Change the schema in `JUDGE_SYSTEM`, `_parse_judge_json()` defaults, and
  `app.js` together.
- `MAX_TOKENS` default is 6000 because Gemini 2.5 models spend "thinking"
  tokens from the same budget — 2000 caused silently truncated answers.
- `AGENT_TIMEOUT` applies per model call, so a tool-using agent's total time
  can reach `MAX_TOOL_ITERATIONS × AGENT_TIMEOUT` in the worst case.
- The teaching comments (FAN-OUT / INDEPENDENT EXECUTION / JUDGE SYNTHESIS /
  REBUTTAL / the memory-layers docstring in memory.py and the loop diagram in
  agent_loop.py) are a deliberate part of the deliverable — keep them
  accurate when refactoring.
- `mdLite()` in `app.js` escapes HTML before rendering bold/headings; model
  output is untrusted — don't switch to `innerHTML` with raw text.
- ddgs (DuckDuckGo) needs no key but occasionally rate-limits; tool errors
  surface to the model as text, and it works around them.
- `.env` holds real keys; never commit it (only `.env.example`).

## Natural extension points

- More agents: add `MODEL_4=...` in `.env` — everything else adapts.
- New exercises: copy `app/exercises/_template` (see top of this file).
- New tools: one schema + one dispatch branch in `app/core/tools.py` (the
  pattern scales to APIs, code execution, databases...).
- Embeddings-based memory: replace `recall()` in
  `app/exercises/consensus/memory.py`.
- Streaming tokens (not just per-agent completion): add a streaming variant
  of `complete()` in `app/core/llm.py` and emit incremental events.
- New specialists: append to `SPECIALISTS` in
  `app/exercises/supervisor/specialists.py` (the `delegate` tool's enum and
  the supervisor prompt are generated from it).
- RAG: swap in your own documents by replacing `app/exercises/rag/corpus/`
  (markdown, one `# ` title, `## ` sections = chunks) and the sample/eval
  questions; better retrieval (stemming, rerankers) goes in `retrieval.py`
  and benefits both variants equally.
- Persistence: sessions currently die with the process; move `_sessions`
  into SQLite alongside long-term memory for durable multi-turn history.
