# Multi-Model Research Consensus

Send one research question to 3+ LLMs from different providers **in parallel**,
then have a separate **judge agent** compare their answers, merge them into one
best answer with per-claim model attribution, and flag where the models
disagreed.

## How it works

```
                    ┌──────────────────────┐
 query + context ──▶│  FAN-OUT (parallel)  │   asyncio.gather — wall time ≈
 + parsed files     └──────────┬───────────┘   slowest agent, not the sum
              ┌────────────────┼────────────────┐
              ▼                ▼                ▼
        ┌──────────┐    ┌──────────┐     ┌──────────┐
        │ MODEL_1  │    │ MODEL_2  │     │ MODEL_3  │   independent agents:
        │  agent   │    │  agent   │     │  agent   │   identical input, zero
        └────┬─────┘    └────┬─────┘     └────┬─────┘   shared context
             └───────────────┼────────────────┘
                             ▼
                      ┌─────────────┐   its own model call; sees every answer
                      │ JUDGE agent │   verbatim; merges + attributes claims +
                      └──────┬──────┘   flags text-grounded disagreements
                             ▼
        merged answer · disagreements · per-model answers · timing
```

- **Provider-agnostic:** models are LiteLLM strings in `.env` — swap
  `openai/gpt-4o` for `gemini/gemini-2.5-pro` or a local `ollama_chat/llama3`
  without touching code. Add `MODEL_4`, `MODEL_5`, … to widen the panel.
- **Fault-tolerant:** a failed or timed-out agent doesn't block the others;
  the judge synthesizes from whatever succeeded and notes the omission.
- **Grounded disagreements:** the judge quotes/paraphrases the actual answer
  text per model and is instructed to return an empty list when models agree.

## Agentic features (toggleable per run)

Three classic agent concepts are layered on top of the base pipeline. Each has
a checkbox in the UI so you can study one at a time; all can coexist.

- **Tool use** (`app/core/tools.py`, `app/core/agent_loop.py`) — each agent becomes a
  ReAct loop: it can call `search_web` (DuckDuckGo), `fetch_url`, and
  `search_documents` (over your attachments), observe results, and iterate
  until it answers (capped by `MAX_TOOL_ITERATIONS`). Every tool call streams
  to the UI as a per-agent research trace, so you can watch different models
  research differently.
- **Rebuttal round** (stage 4 in `app/exercises/consensus/orchestrator.py`) — if the round-1
  judge finds disagreements, each agent is shown the disputed points and the
  other models' claims, defends or revises in parallel, and the judge
  re-synthesizes. The UI shows "N disagreements → M remaining after rebuttal".
- **Memory** (`memory.py` + sessions in `routes.py`, both in `app/exercises/consensus/`) — three layers:
  *working memory* (an agent's own tool-call transcript within one loop),
  *session memory* (ask follow-up questions; prior turns are injected into
  every agent's context), and *long-term memory* (every verdict is stored in
  SQLite `memory.db`; with the toggle on, related past findings are recalled
  into new queries and shown in the UI).

## Code tour

The repo is a playground of exercises: the landing page at `/` lists every
module in `app/exercises/` (this consensus app is `consensus`, served at
`/consensus/`); shared building blocks live in `app/core/`. Start a new one
with `cp -r app/exercises/_template app/exercises/<name>` and a unique slug.

| File | What it does |
|---|---|
| `app/main.py` | Root app: auto-discovers exercises, mounts each at `/<slug>/`, serves the landing page and `/api/exercises`. |
| `app/exercises/consensus/orchestrator.py` | The core. Commented stages: **fan-out** (`asyncio.gather` launches all agents at once), **independent execution** (each agent isolated, with its own timeout/error capture), **judge synthesis** (a separate model call that returns JSON: merged answer, disagreements, agreement summary, omissions), plus the optional **rebuttal round** and memory recall/write hooks. `RunOptions` carries the per-run feature toggles. |
| `app/core/agent_loop.py` | The ReAct tool loop (used when tool use is on): call model → execute requested tools → append results → repeat until a final answer; last iteration withholds tools to force an answer. |
| `app/core/tools.py` | Tool schemas + executors: `search_web` (DuckDuckGo via ddgs), `fetch_url` (httpx + HTML stripping), `search_documents` (keyword retrieval over attachments). |
| `app/exercises/consensus/memory.py` | Long-term memory: SQLite store of past verdicts, keyword `recall()`, and the context block injected when memory is enabled. |
| `app/core/config.py` | Env-driven model layer: reads `MODEL_1..N` + `JUDGE_MODEL` (LiteLLM model strings) and optional per-model `_API_BASE`/`_API_KEY` overrides. |
| `app/exercises/consensus/routes.py` | FastAPI router at `/consensus/`. `POST api/query` (multipart: query, context, files, toggles, session_id) returns a job id; `GET api/stream/{job_id}` relays orchestrator events as SSE. Also owns session memory (follow-up turns). |
| `app/core/files.py` | Parses uploads (txt/md/csv as text, pdf via pypdf); parsed text goes into the one shared user message every agent receives. |
| `app/exercises/consensus/static/` | No-build-step UI: `index.html` + `app.js` (submits the form, consumes SSE, renders panels/traces/rounds/memories) + `style.css`. Shared `base.css`, nav bar and landing page are in `app/static/`. |

A query flows: browser form → `POST /consensus/api/query` → background `run_consensus()`
task pushes events onto an `asyncio.Queue` → SSE relays them → UI flips each
model panel from pulsing to green/red as its agent finishes, then renders the
judge's verdict.

## Setup

```bash
cd play2
cp .env.example .env
# edit .env: pick MODEL_1..N + JUDGE_MODEL, set the matching API keys
```

## Run

```bash
./start.sh    # creates .venv + installs deps on first run, serves on :8000
./stop.sh
```

`PORT=9000 ./start.sh` to use another port. Logs go to `server.log`.
No API keys yet? `MOCK_MODELS=true ./start.sh` runs every exercise with canned
model replies and fake tool results, so you can explore the flows for free.
(Manual alternative: `.venv/bin/uvicorn app.main:app --port 8000`.)

Open http://localhost:8000, pick **Multi-Model Research Consensus** (or go
straight to http://localhost:8000/consensus/) — type a question, optionally add free-text context
and attach files (txt/md/csv/pdf; parsed and given identically to every agent
and the judge), and run. You'll see:

1. A live panel per model (pulsing while running, green/red when done, latency shown).
2. A timing badge: parallel wall time vs. what sequential would have cost.
3. The merged consensus answer with inline `(per model-x)` attributions.
4. A highlighted, collapsible **Disagreements** section — or an explicit
   "models agreed" note when there's nothing to flag.

## Configuration reference

| Variable | Meaning |
|---|---|
| `MODEL_1`, `MODEL_2`, … | Research agents (LiteLLM model strings, contiguous numbering) |
| `JUDGE_MODEL` | The judge agent's model |
| `MODEL_N_API_BASE` / `MODEL_N_API_KEY` | Optional per-model endpoint override (OpenAI-compatible gateways: Ollama cloud, OpenRouter, vLLM…) |
| `JUDGE_MODEL_API_BASE` / `JUDGE_MODEL_API_KEY` | Same, for the judge |
| `AGENT_TIMEOUT` | Per-agent timeout in seconds (default 120; applies per model call inside the tool loop) |
| `MAX_TOKENS` | Cap for research answers and judge synthesis (default 6000) |
| `MAX_TOOL_ITERATIONS` | Max reason→act→observe cycles per agent with tool use on (default 6) |
| `SUPERVISOR_MODEL` | Supervisor exercise: the model that delegates (default: `JUDGE_MODEL`) |
| `MAX_SUPERVISOR_TURNS` | Supervisor exercise: max decisions per run; the last must finish (default 8) |
| `MOCK_MODELS` | `true` = canned replies + fake tools, no keys or network (default false) |
| Provider keys | `OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, `GEMINI_API_KEY`, … as your models require |
