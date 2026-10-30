# Handoff — current state and how to resume

Read `AGENTS.md` first for the architecture and conventions.

## Where things stand (2026-10-30)

- **12 exercises**, all tested end to end with **real models**:
  `scripts/smoke_test.py` → 13/13 PASS. The Eval Harness was run on the
  `smoke`, `rag`, `code` and `guardrails` datasets.
- **Fixes from real-model testing** (see `git log`):
  - litellm pinned `<1.106`, because 1.106.x crashes on any call with `tools=`.
  - Web search retries, with at most 2 searches running at once.
  - Guardrails: the secret is now conditional, so social engineering can leak
    it; the output-policy check no longer blocks public pricing.
  - Context notes: compaction keeps the full list of notes.
- **Learning videos**: one narrated MP4 per exercise plus an overview (13
  total), built by `scripts/videos/`. The MP4s are gitignored.
- **Learning hub** (private claude.ai artifact, with the videos uploaded to it):
  https://claude.ai/artifact/SUThwKKxSD1rYuc57WdKYd.
  `scripts/videos/hub_videos.json` maps each slug to its uploaded file.
- PR: javiator/agentic-orchestration-play#1, branch `claude/cool-cray-49w0rp`.

## Resume in a fresh cloud session

1. **Models.** Only `OPENAI_API_KEY` and `GEMINI_API_KEY` exist here. Create
   `.env` from `.env.example`, delete its empty `*_API_KEY=` lines, and set:
   ```
   MODEL_1=openai/gpt-4.1-mini
   MODEL_2=gemini/gemini-2.5-flash
   MODEL_3=openai/gpt-5-mini
   JUDGE_MODEL=openai/gpt-4.1
   ```
2. **Start.** This environment exports its own `MODEL_*` / `JUDGE_MODEL`
   (Anthropic models with no key), and they override `.env`. Start with
   `.env` loaded into the shell so its values win:
   ```bash
   (set -a; . ./.env; set +a; ./start.sh)
   .venv/bin/python scripts/smoke_test.py
   ```
3. **Videos** (optional). Set up once, then build with the app running:
   ```bash
   uv venv .videovenv && uv pip install -r scripts/videos/requirements.txt --python .videovenv/bin/python
   .videovenv/bin/python scripts/videos/build.py rag         # one video; no args = all 13
   .videovenv/bin/python scripts/videos/build.py rag --reuse  # re-assemble, keep the last recording
   ```
   - Content: slide text and narration are in `content.py`; demo steps are in
     `demos.py`.
   - Dry-run demos against a mock server:
     `MOCK_MODELS=true .venv/bin/uvicorn app.main:app --port 8001`, then
     `BASE=http://localhost:8001`. Add `DEMO_SHOTS=<dir>` for a screenshot
     after every step.
   - Check `out/<slug>/demo/timeline.json` to confirm the narration matches
     what the models actually did.
4. **Updating the hub.** Upload new MP4s to the artifact above as assets. Put
   their URLs in `hub_videos.json`, run
   `scripts/videos/hub.py scripts/videos/hub_videos.json`, and republish the
   page to the same artifact URL.

## Known loose ends

- The consensus video says "1 remain"; it's fixed in `demos.py` and needs a
  re-record (`build.py consensus`).
- Videos run 2.3–3.3 min, a bit under the original 3–5 min target.
- Demo outcomes vary between runs; the narration adapts, but some runs miss
  a teaching moment (a failing code run, a chain fix round). Re-record when
  that happens.
