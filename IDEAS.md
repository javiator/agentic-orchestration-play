# Ideas — next builds

Follow-ups to the learning-video pipeline (`scripts/videos/`). What made that
work, and what each idea below keeps:

- **The real system is the source of truth.** The app actually runs, and
  outputs come from what it did, not from a script someone wrote by hand.
- **Narration and text are computed from real state** (scores, which layer
  blocked an attack), so they stay accurate.
- **Fully automated:** one command produces a finished, shareable artifact.
- **Verified:** a timeline/log shows what happened, so the output can be
  checked and rebuilt.

Status key: 💡 idea · 🧪 prototype · ✅ built

---

## 1. Walkthrough videos for every pull request or release 💡

**Problem.** Reviewers and product managers have to check out a branch to see
a UI change; release notes are text.

**How it works.** When a pull request opens, CI starts the app on that branch.
A model reads the diff and PR description and writes the demo steps (the
same `d.step(...)` format as `demos.py`). Playwright records them with
captions and highlights, and the 2-minute narrated MP4 is posted to the PR.

**Reuses.** `scripts/videos/lib.py` almost as is (recording, narration,
fast-forward, assembly).

**New parts.** Generating steps from the diff; uploading the video and
commenting on the PR from CI.

**First step.** A GitHub Action that runs `build.py` for one hand-written demo
and attaches the MP4 to the PR.

**Open questions.** Where to host videos (PR attachment vs artifact vs S3);
cost and time per PR; what happens when the generated steps fail.

---

## 2. Product docs that check themselves 💡

**Problem.** Screenshots and how-to guides go out of date as the UI changes,
and nobody notices until a user complains.

**How it works.** Each guide is a demo script. Every night it runs against the
app and regenerates its screenshots, GIFs or short clips. A selector that no
longer matches fails the build, so out-of-date docs show up like a failing
test.

**Reuses.** The demo steps (`click`, `fill`, `wait`, `highlight`) and the
overlay; output is screenshots/GIFs instead of a narrated MP4.

**First step.** Script the 5 most-asked support questions as short demos and
publish them as a docs page.

**Open questions.** Where the docs site lives; text-only guides vs short clips;
how to review changed screenshots (image diff?).

---

## 3. Personal briefing generator: data or documents in, short audio or video out 💡

**Problem.** Long reports, dashboards and papers take time to read; weekly
updates are repetitive to write.

**How it works.** Point it at a CSV, a dashboard export, a PDF or a topic. It
produces a 3-minute narrated brief: a title slide, 3–5 slides with charts and
key numbers computed from the data, and three takeaways. An archive page
(like the learning hub) keeps past briefs.

**Reuses.** Slide rendering, Piper narration, ffmpeg assembly, the hub page
generator.

**New parts.** A step that turns data into charts and numbers; summarizing
long PDFs; an audio-only (podcast) option for listening on the go.

**First step.** A script that takes one CSV or PDF and builds a 5-slide
narrated MP4.

**Open questions.** Privacy of the source data (keep everything local?);
how much to let the model choose vs fixed slide templates.

---

## 4. Bug reproduction recorder 💡

**Problem.** Bug reports are vague ("it breaks when I click save"); developers
lose time reproducing them, and fixes are verified by hand.

**How it works.** An agent reads the issue, drives the app with Playwright
(the same tools as the demos) until it reproduces the bug, and records it.
It attaches to the issue: a video, the exact steps as a replayable script,
console errors and failed network requests. When a fix PR opens, the script
replays on that branch to show whether the bug is gone.

**Reuses.** Recording and highlights from `lib.py`. The agent loop is the
same ReAct pattern as the code-agent exercise, with browser actions as tools.

**New parts.** Browser-action tools for the agent; capturing console and
network logs; a "could not reproduce" path that lists what was tried.

**First step.** Take 3 real past bugs and have the agent reproduce them
against a local app, with the script saved as a test.

**Open questions.** Test accounts and data; keeping the agent away from
destructive actions (use the HITL exercise's risk tiers); flaky
reproductions.

---

## 5. Codebase tour generator for onboarding 💡

**Problem.** New team members spend days learning how a codebase is
organized; architecture docs go stale.

**How it works.** It reads `AGENTS.md`, module docstrings and the folder
structure, then produces a narrated tour: an architecture slide (diagram from
docstrings, as these videos do), then for each key flow a recording of the
app doing it next to the code that runs it, with the relevant lines
highlighted. The output is a hub page with one short video per area.

**Reuses.** Docstring diagrams (`content.docstring_diagram`), slides, demo
recording, the hub page.

**New parts.** A code view with highlighted lines (render source as HTML
with line markers); mapping a UI action to the code it calls (from routes
and call graphs, or written by hand at first).

**First step.** A tour of this repo: one video for `app/core/` and one per
exercise group, generated from `AGENTS.md` and the docstrings.

**Open questions.** How much to automate the UI-to-code mapping; keeping the
tour current (rebuild on merge to `main`?).

---

## Suggested order

1. **#1 Walkthrough videos for PRs.** It reuses the most and pays off every
   week.
2. **#4 Bug reproduction recorder.** High value, and it adds agent-driven
   browsing, which #2 and #5 can also use.
3. **#3 Personal briefings.** Useful outside software work.
