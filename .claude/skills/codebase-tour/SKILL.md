---
name: codebase-tour
description: Produce a narrated onboarding tour of a codebase — an overview video plus one short video per area, combining architecture diagrams taken from the code's own docstrings, highlighted code excerpts, and (when the project has a UI) the running app doing what that code does — collected on a hub page. Use when the user wants onboarding material, an architecture walkthrough, "explain this repo" as videos, a tour for new team members, or to refresh a stale architecture doc. Builds on the demo-video skill.
---

# codebase-tour

Reads the repo, plans a tour, and builds it as demo-video YAMLs: one **overview** and one
video per **area** (2–4 minutes each), then a hub page. Requires the **demo-video** skill
next to this one (`$ENGINE` = `$SKILL/../demo-video`).

## Workflow

1. **Map the code:** `python3 $SKILL/scripts/tour_context.py REPO --out WORK/tour-brief.md`
   (`--paths a b` to focus). The brief has the outline of AGENTS.md/README/CLAUDE.md, a
   folder map with sizes, entry points, routes (pages a live demo can open), every ASCII
   diagram already in docstrings/comments (with file:line), and per file its docstring
   and definitions with line ranges. Then read the docs and the key files yourself — the
   brief is a map, not an understanding.
2. **Plan the tour** (`WORK/tour-plan.md`): the overview, then areas in reading order
   (usually: entry point → core building blocks → features → tests/ops). Per area: the
   question it answers, its diagram, 1–3 key code excerpts (≤ 30 lines each), and the page
   that shows it working, if any. Ask the user to confirm the plan if the repo is large.
3. **Write one YAML per video** (`WORK/tour/NN-<area>.yaml`; `examples/skills-tour.yaml` is one):
   - `title` slide: what this area is for, in one sentence; chips = main modules.
   - `diagram`: copy the code's own diagram text (attribute it: `foot: "from app/core/x.py"`);
     if there is none, draw a small one — and suggest adding it to the docstring.
   - `code` slides: `file:` (relative to the YAML — use paths like `../../app/x.py` or
     absolute), `lines:` from the brief, `highlight:` the lines the narration talks about.
     Narrate the *why*: what problem this code solves and the one thing to notice.
   - `steps:` (optional): open the page this code serves and show it working — the
     strongest way to connect code to behaviour. Needs the app running (`--base`).
   - `takeaways`: where to start reading, what to change first, what not to touch.
   - `hub: {eyebrow: "Tour · 2 of 6", summary: …, code: <link to the folder>}`.
4. **Dry run all, review, build** with the engine (`--dry-run`, then without);
   verify per demo-video step 5. Slides-only videos need no app.
5. **Hub:** `python3 $ENGINE/scripts/hub.py WORK/tour/*.yaml --build-dir OUT --posters
   --title "<Project> — code tour" --intro "…" > OUT/index.html`.

## Keeping it current

Diagrams and code excerpts are read from the files at build time (`file:` + `lines:`), so
a rebuild picks up edits — but line ranges can drift. Before rebuilding after big changes,
re-run `tour_context.py` and update `lines:`. The best long-term fix is in the code:
keep a diagram in each module's docstring, and the tour (and readers) stay in sync.
