# Handoff — next session

Branch `claude/cool-cray-49w0rp` (PR into `main`). Everything below is
committed. Read `AGENTS.md` first for the architecture and conventions.

## Where things stand

12 exercises, all auto-discovered on the landing page (`/`). Each one was
built and checked in **mock mode only** (`MOCK_MODELS=true`); none has run
against real models yet. `MOCK_MODELS=true ./start.sh` plus
`scripts/smoke_test.py` → 13/13 PASS, with no console errors and no
horizontal scroll at phone width.

| Slug | Exercise | Has eval target(s) |
|---|---|---|
| consensus | Multi-Model Research Consensus | yes |
| router | Router | yes (routed, always strong) |
| supervisor | Supervisor & Specialists | yes |
| planner | Planner–Executor | yes |
| chain | Prompt Chain vs Single Prompt | — (writing task) |
| rag | Plain RAG vs Agentic RAG | yes (plain, agentic) + `rag` dataset |
| reflection | Reflection Loop | yes |
| code-agent | Code-Executing Agent | yes + `code` dataset |
| hitl | Human-in-the-Loop | — (needs a human) |
| guardrails | Guardrails | yes (guarded, unguarded) + `guardrails` dataset |
| context | Context Management | — (fixed script) |
| eval | Eval Harness | — |

## Next session: do these in order

### 1. Set up real models
- Provider keys are environment secrets in this cloud environment. **Never
  print, log or commit them.** Check which ones exist with
  `env | grep -o '^[A-Z_]*_API_KEY' | sort`.
- `cp .env.example .env`. Empty `*_API_KEY=` lines don't override real
  environment variables (python-dotenv doesn't override by default).
- Edit `MODEL_1..3` and `JUDGE_MODEL` in `.env` so they only use providers
  that have keys. Prefer 3 different providers. Use a cheap fast model for
  `MODEL_1` (it's the default for router/guard/context roles) and a strong
  one for `JUDGE_MODEL`.
- If `MODEL_*`/`JUDGE_MODEL` are already set as environment variables, those win.

### 2. Real-key test and fix
- Run `./start.sh`, then `.venv/bin/python scripts/smoke_test.py`.
- Fix every FAIL at its root cause, re-run, and repeat until all pass. Read
  `smoke_report.json` to check the answers are sensible, not just that runs
  completed.
- Then click through each page once, using its sample inputs.
- Known risks to watch:
  - **Tool calling** differs by provider. Agents keep tools offered on their
    last step with an "answer now" note. Some models also emit parallel tool
    calls; the supervisor, RAG agent, code agent and HITL loops handle lists.
  - **JSON replies** (router, planner, critic, grader, guards, judge) are all
    parsed defensively with fallbacks. Check that the fallbacks don't trigger
    on every call, e.g. a model wrapping JSON in prose.
  - **Gemini 2.5** spends "thinking" tokens from `MAX_TOKENS`, and calls must
    not lower it.
  - **`ddgs` web search** can rate-limit. Tool errors come back to the model as text.
  - **Code agent** runs model-written Python locally (see `sandbox.py`
    docstring). That is fine in this container.
- Run the Eval Harness once each on `smoke`, `rag`, `code` and `guardrails`
  with their relevant targets, to confirm the harness and grader work.

### 3. Learning videos (agreed format)
The user wants to learn each exercise by watching, not reading. For **each
exercise** produce one MP4 of about 3–5 minutes, plus one ~3-minute overview
of the whole playground:

1. **Concept** (~1 min): 2–4 slides rendered as HTML pages and screenshotted.
   They show the pattern diagram (from the exercise's teaching docstring),
   the problem it solves, and when to use it.
2. **Live demo** (2–3 min): a Playwright screen recording (`recordVideo`,
   1280×800) of the page running the demo below **with real models**.
   - Add on-screen captions (a fixed caption bar injected into the page) and
     highlight the element being discussed (outline or pulse).
   - Slow down: pause on key moments so the viewer can read.
3. **Takeaways** (~30 s): a slide with the 3 takeaways below.

**Narration:** text-to-speech, timed to the captions.
- Try Piper (neural TTS) first:
  - `pip install piper-tts` in a scratch venv;
  - download the voice `en_US-lessac-medium` (or `en_GB-alan-medium`) from
    huggingface.co/rhasspy/piper-voices.
- Check the voice sounds natural. Fallback: espeak-ng.
- Combine with `ffmpeg` (installed) and burn in subtitles.
- Target ≤ 15 MB per video (720p, H.264, CRF ~28) so videos can be
  uploaded as artifact assets.

**Learning hub:** publish one private Artifact page, mobile-friendly. For
each exercise it shows:
- the video (uploaded as an asset);
- a one-paragraph summary and the diagram;
- three "try this" prompts;
- a link to the code.

The overview video goes at the top. Give the user the link.

Keep the scripts that build videos in `scripts/videos/`, committed. Keep the
MP4s out of git (add them to `.gitignore`).

#### Demo script per exercise

| Exercise | Demo input | Moments to highlight | Takeaways |
|---|---|---|---|
| Consensus | "Is nuclear power safer than wind per unit of energy?" with tools and rebuttal on | parallel panels; judge's disagreements; rebuttal shrinking them | fan-out is parallel; a separate judge synthesizes; disagreements are signal |
| Router | "Route all samples", then the bat-and-ball sample with compare | traffic mix; route + confidence; savings line | most traffic is easy; escalate when unsure; measure misroutes |
| Supervisor | default goal placeholder | turn blocks; parallel delegations; finish | dynamic handoff; agents as tools; turn caps and guards |
| Planner | Postgres vs MongoDB, inject failure on | plan waves; failed step; revised plan keeping s1 | plan up front = fewer decisions + parallelism; replan on failure |
| Chain | "How vaccines train the immune system" | gate rows; fix round; checks 6/6 vs single | workflows beat agents when steps are known; code gates are free |
| RAG | postmortem-owner question, then Toronto desks | plain gets the role but not the person; agent's 2nd search; grounding check | agency helps multi-hop; costs ~5× calls; unanswerables need honesty |
| Reflection | blockchain-to-a-12-year-old task, then self-critique | score bars; diff between versions; stop reason | most gain in round 1; critic choice matters; keep the best |
| Code agent | region-growth question | failing run → traceback → fix; exact numbers | compute, don't guess; tracebacks are real feedback; sandbox caveat |
| HITL | sample 1; approve the refund, reject the email with feedback | pause card; shop table updating; revised email | gate by risk; feedback lets the agent adapt; checkpoints |
| Guardrails | samples 2, 3, 4 | unguarded leak vs blocked; which layer caught it; skipped layers | instructions aren't controls; layer cheap → expensive; false positives |
| Context | run once | quiz grid; token chart; summary vs notes content | full = recall + growing cost; window forgets; notes keep facts |
| Eval | `rag` set, both RAG targets + one single model | live grid; summary table | measure, don't eyeball; grader bias; small sets are noisy |

### 4. Finish
- Update the PR description: list all 12 exercises, the smoke test, and the
  video/hub links.
- Commit, push, and tell the user what passed, what was fixed, and the hub link.

## Prompt the user will paste into the new session

> Read HANDOFF.md on branch `claude/cool-cray-49w0rp` and do everything in its
> "Next session" section: real-key test every exercise and fix what breaks,
> then produce the narrated videos and the learning hub page.
