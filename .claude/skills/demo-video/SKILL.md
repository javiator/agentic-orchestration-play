---
name: demo-video
description: Turn a running web app into a narrated demo video (MP4 with title/concept slides, a live screen recording driven by Playwright, a takeaways slide, offline TTS narration, burned-in subtitles, fast-forwarded waits). Use when the user asks for a demo video, walkthrough, screencast, tutorial or "learning video" of a web app or feature, wants to re-record or fix an existing demo video, or wants a hub page listing several videos.
---

# demo-video

Input: the base URL of a **running** web app + a demo script in YAML (which you write).
Output, per script: `OUT/<slug>.mp4` (1152×720, H.264, CRF 28, < 15 MB, 2–5 min),
`OUT/<slug>.srt`, `OUT/<slug>/timeline.json` (every narration line with its time,
chapters, problems found). Optional: a hub page listing several videos.

All code lives in this folder; projects only supply YAML. Paths below are relative
to this skill folder (call it `$SKILL`). The venv, voice and TTS cache live in
`~/.cache/demo-video/` (override with `DEMO_VIDEO_CACHE`); outputs go to `--out`.

```bash
python3 $SKILL/scripts/explore.py URL --out EXPLORE_DIR [--do click:#run --do wait:#result --do shot:after]
python3 $SKILL/scripts/build.py DEMO.yaml --base URL --out OUT --dry-run   # screenshots, no TTS/MP4
python3 $SKILL/scripts/build.py DEMO.yaml --base URL --out OUT             # record + assemble
python3 $SKILL/scripts/build.py DEMO.yaml --base URL --out OUT --reuse     # re-assemble only
python3 $SKILL/scripts/hub.py demos/*.yaml --title T --intro I [--urls urls.json] > hub.html
```

Any `python3` works: the scripts create their venv on first run and re-exec in it.
Exit status of build.py is 1 when problems were found — read them, don't ignore them.

## Workflow

1. **Explore the app.** Run `explore.py` on each page you'll show. Look at the PNGs and
   read the `.md` inventories: they list inputs (with options), buttons, checkboxes,
   sample/chip lists and every id, as CSS selectors. Use `--do` steps to run the
   main flow once (click run → `wait:` result → `shot:`) so you also see the result
   state and its selectors. Look for a mock/fast mode (e.g. an env var) for dry runs,
   and sample inputs the app already offers. Read the app's README/docstrings for
   the concept slides.
2. **Draft the YAML** (format below; `examples/router.yaml` is a complete one).
   Title → problem/when-to-use → diagram slides; then the live steps; then 3
   takeaways. One idea per step. Highlight what the narration talks about. Read
   results from the page (`{text:...}`, `{if:...}`) instead of hard-coding them —
   real runs differ.
3. **Dry run** with `--dry-run` (against mock/fast mode if there is one;
   `DEMO_MAX_WAIT=20` caps waits). It prints each step's resolved narration and
   writes `OUT/<slug>/shots/step01.png…` and slide PNGs. Fix every problem it
   reports. **Show the user the screenshots and narration, and wait for approval
   before recording.**
4. **Record** against the real app (no `--dry-run`).
5. **Verify.** Read `OUT/<slug>/timeline.json` (problems, line times). Extract a few
   frames and look at them, especially at results-reading lines:
   `ffmpeg -ss 95 -i OUT/x.mp4 -frames:v 1 -y /tmp/f95.png`. Check the narration matches
   what's on screen (a templated line can be grammatical but wrong). Fix the YAML;
   if only slides/outro changed use `--reuse`; re-record only when steps changed or a
   run missed its teaching moment.

QA checklist before handing over: no problems in timeline.json · narration matches
the frames · 2–5 min · < 15 MB · subtitles readable and in sync · slide text fits ·
facts on slides are true for this app.

## Demo script format

```yaml
slug: router                     # output name (default: file name)
kicker: "Exercise 2 · My App"    # small label on every slide
start: /router/                  # first page (relative to --base, or a full URL)
defaults: {tag: DEMO, wait_caption: "waiting for the models…", hold: 0.8}

concept:                         # slides before the demo; every slide has `say`
  - {kind: title, title: Router, lead: "One sentence.", chips: [a, b], say: "…"}
  - kind: boxes
    title: "The problem — and when to use it"
    boxes: [{head: Problem, items: [..]}, {head: Use it when, items: [..]}]
    say: "…"
  - {kind: diagram, title: How it works, diagram: "ascii art…", say: "…", pause: 1.5}

steps:
  - say: "First, route all the samples at once."    # narration (templated)
    caption: Route all samples                      # caption bar, ≤ ~6 words
    highlight: "#classify"                          # CSS; first:/last: prefixes
    actions: [{click: "#classify"}]                 # run during the narration
    wait: {until: "js:/tokens/.test(document.body.innerText)", timeout: 120,
           caption: "classifying 8 requests…"}      # fast-forwarded if long
    hold: 1.2                                       # linger after (s)
  - say: "Here's the mix: {text:#batch-stats|before:·}."
    caption: The traffic mix
    highlight: "#batch"
  - say: "Every page has the same shape."
    goto: /other/                                   # navigate before this step's narration

outro:
  - {kind: takeaways, items: [.., .., ..], say: "…", pause: 2, foot: "Code: path/to/file"}

hub: {eyebrow: "Exercise 2 of 12", summary: "…", tries: [..], code: "https://…"}
```

**Actions** (in order): `goto: /path` · `fill: [sel, text]` (typed visibly) ·
`click: sel` or `click: {selector, nth, text, optional: true}` · `check: sel` ·
`uncheck: sel` · `select: [sel, value]` · `press: [sel, key]` · `wheel: 200` ·
`pause: 0.5` · `js: "code"`. Selectors take `first:` (default), `last:` or `all:`
(every match). A failing action is logged as a problem; the recording continues.

**Waits**: a selector (visible) or `js:<expression>`; `{until, timeout, caption}`.
`DEMO_MAX_WAIT` caps every timeout (dry runs). Waits > 4 s are fast-forwarded to
≤ 2.5 s in the final cut, with the caption shown meanwhile.

**Narration templating** (evaluated when the step starts, before its actions — so
read results in the step *after* the one that waits for them):
`{text:SEL}` · `{count:SEL}` · `{plural:SEL|turn}` → "3 turns" · `{js:EXPR}` ·
`{if:SEL}…{else}…{end}` (visible match; or `{if:js:EXPR}`; nests).
Filters on text/js: `|before:X` `|after:X` `|max:N` `|num` `|default:X`
`|replace:A=>B` `|strip`. Use `{{`/`}}` for literal braces. Empty `{text:}`
results are logged as problems.

**Speech clean-up** is automatic (subtitles keep the original): "-43%" → "minus 43%",
"6/6" → "6 out of 6", "×" → "times", "→" → "to".

## How to write a good demo

- **Length**: 2–5 minutes. Concept ≈ 30–60 s (3 slides), demo 1.5–3 min, takeaways 20 s.
  Narration runs ~150 words/min; a 40-word step is ~16 s.
- **Pacing**: one idea per step; let results sit (`hold: 1.5–3`) when the viewer
  needs to read them. "Run." is a fine step on its own.
- **Captions vs narration**: the caption is a 2–6 word headline of the step; the
  narration explains. Don't repeat one in the other.
- **Fast-forward** anything the viewer doesn't need to watch (model calls, builds):
  put it in a `wait`, with a caption saying what's happening. Never narrate during a
  wait — narration plays at real speed only.
- **Be truthful**: narrate results from the page; use `{if:}` for outcomes that
  vary ("the chain needed a fix round" vs "passed every gate"). If a run misses
  the teaching moment, re-record rather than narrate what didn't happen.
- **Highlight** the element being discussed on every step; scroll happens automatically.
- Write for the ear: short sentences, numbers as the page shows them, no
  parentheses or symbols you wouldn't say.

## Install

The folder is self-contained (no imports from the project it came from). To use it
from any project, copy or symlink it into your personal skills folder:

```bash
mkdir -p ~/.claude/skills && cp -r .claude/skills/demo-video ~/.claude/skills/demo-video
```

```bash
ln -s "$PWD/.claude/skills/demo-video" ~/.claude/skills/demo-video
```

Or keep it in a project's `.claude/skills/`, or in its own repo. Requirements:
Python 3.10+, ffmpeg with libass; everything else installs itself on first run.

## Setup and troubleshooting

- **First run** creates `~/.cache/demo-video/venv` (uv if present, else venv+pip)
  from `requirements.txt` and downloads the Piper voice `en_US-lessac-medium`
  from huggingface.co/rhasspy/piper-voices (other voice: `DEMO_VIDEO_VOICE`).
- **TTS**: `--tts auto` (Piper, else espeak-ng) | `piper` | `espeak`.
- **ffmpeg** with libass is required; build.py checks and explains how to install it.
- **Chromium**: Playwright's own if it matches; else a system one
  (`/opt/pw-browsers/chromium`, `DEMO_VIDEO_CHROMIUM=/path`); downloads only if none works.
- Apps behind env-specific config: start them exactly as the project's docs say
  before recording; the skill only needs the URL.

## Extension points (designed in, not built yet)

- **TTS backends** — `scripts/narrate.py`: subclass `Backend` (`synth(text, wav)`,
  unique `id`), register in `BACKENDS`. Planned: ElevenLabs (`ELEVENLABS_API_KEY`
  or the ElevenLabs MCP), and "bring your own audio" for sandboxes without network
  (export the lines from timeline.json, user records/generates WAVs, drop them in).
- **Themes** — `--css` with the class names of `templates/slide.css`.
- **New slide kinds** — one branch in `slides.slide_html` + CSS.
- **Upstream stages** for non-app videos (intake → brief, research with sources,
  storyline per audience, scene plan, QA checklist) would produce the same YAML,
  so the recording/assembly half stays unchanged.
