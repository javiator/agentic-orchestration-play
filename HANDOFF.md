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
  total), built by the **`demo-video` skill** (`.claude/skills/demo-video/`,
  self-contained, reusable for any web app). This repo only holds the
  content: `demos/<slug>.yaml` (slides, narration, live steps, hub text).
  The MP4s are gitignored (`videos/`).
- **Learning hub** (private claude.ai artifact, with the videos uploaded to it):
  https://claude.ai/artifact/SUThwKKxSD1rYuc57WdKYd.
  `demos/hub_videos.json` maps each slug to its uploaded file.
- PR: javiator/agentic-orchestration-play#1, branch `claude/cool-cray-49w0rp`.
- **Next builds:** five ideas for tools built on the video pipeline are in `IDEAS.md`.

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
3. **Videos** (optional), with the app running. See
   `.claude/skills/demo-video/SKILL.md` for the workflow and YAML format. The
   first run creates the skill's venv in `~/.cache/demo-video/`.
   ```bash
   SKILL=.claude/skills/demo-video
   python3 $SKILL/scripts/build.py demos/rag.yaml --out videos            # one video
   python3 $SKILL/scripts/build.py demos/*.yaml --out videos              # all 13
   python3 $SKILL/scripts/build.py demos/rag.yaml --out videos --reuse    # re-assemble, keep the recording
   ```
   - Dry-run against a mock server (screenshots per step, no TTS):
     `MOCK_MODELS=true .venv/bin/uvicorn app.main:app --port 8001`, then
     `build.py demos/rag.yaml --base http://localhost:8001 --out videos --dry-run`
     (`videos/rag/shots/`). Mock HITL finishes after a reject and mock eval
     is slow; those two report expected problems.
   - Check `videos/<slug>/timeline.json` (problems + every narration line) to
     confirm the narration matches what the models actually did.
   - Slide diagrams are inlined in the YAML (they used to be read from each
     exercise's docstring): update them when a docstring diagram changes.
4. **Updating the hub.** Upload new MP4s to the artifact above as assets. Put
   their URLs in `demos/hub_videos.json`, then render and republish the page to
   the same artifact URL:
   ```bash
   python3 .claude/skills/demo-video/scripts/hub.py demos/{overview,consensus,router,supervisor,planner,chain,rag,reflection,code-agent,hitl,guardrails,context,eval}.yaml \
     --urls demos/hub_videos.json --title "Agentic Orchestration Playground — lessons" \
     --intro "One narrated video per exercise: the concept, a live demo with real models, and three takeaways. Start with the 3-minute overview, then go in order, or jump to a pattern." \
     --footer "Videos built with the demo-video skill · demos recorded against real models (OpenAI + Gemini)." > hub.html
   ```

## Known loose ends

- The consensus video says "1 remain"; it's fixed in `demos/consensus.yaml`
  and needs a re-record.
- Videos run 2.3–3.3 min, a bit under the original 3–5 min target.
- Demo outcomes vary between runs; the narration adapts, but some runs miss
  a teaching moment (a failing code run, a chain fix round). Re-record when
  that happens.
