---
name: living-docs
description: Write and maintain how-to guides for a web app that regenerate their own screenshots from the running app and fail like a test when the UI drifts (a renamed button, a moved field). Use when the user wants step-by-step product docs, a help-center or onboarding guide with screenshots, to refresh outdated screenshots, to check whether docs still match the UI, or a nightly docs check in CI. Builds on the demo-video skill.
---

# living-docs

Each guide is a small demo-video script; `scripts/docs.py` replays it against the running
app and writes `OUT/<slug>.md` + `OUT/img/<slug>/stepNN.png`. Selectors that stop
matching, failed actions, timeouts and failed `expect:` checks mark the guide **out of
date** (exit 1); screenshots that changed beyond a similarity threshold are listed in
`OUT/<slug>.changes.md` for review. Requires the **demo-video** skill next to this one
(`$ENGINE` = `$SKILL/../demo-video`).

## Workflow

1. **Pick the guides**: the questions users actually ask (support tickets, onboarding
   steps). One task per guide, 3–8 steps.
2. **Explore** the flow: `python3 $ENGINE/scripts/explore.py URL --out WORK/explore --do …`
   (selectors, sample inputs, result states).
3. **Write `guides/<slug>.yaml`** — see `examples/router-compare.yaml`:
   - `guide:` block: `title` ("How to …"), `intro`, `audience`, `prerequisites`, `max_wait`.
   - per step: `doc:` the instruction as it should read in the docs (bold UI labels, one
     action per step; falls back to `say:`), `caption:` the step heading, `highlight:` the
     control to point at, `actions:`, `wait:`, and `expect:` that proves the step worked.
   - `image: element` (default: the highlighted control with context) · `page` (full
     viewport, for results) · `none`.
4. **Generate:** `python3 $SKILL/scripts/docs.py guides/*.yaml --base URL --out docs/guides`.
   Read the `.md` files and look at the images. Run it **3 times**: no guide may report
   "changed" on a re-run — if one does, the screenshot catches an in-between state
   (spinner, disabled button, animation): wait for the final state (`wait:` on a `js:`
   condition) before the step ends.
5. **Commit** the guide sources, the markdown and the images (they are the docs).
6. **Keep them alive:** run the same command nightly or per PR (`templates/nightly-docs.yml`).
   Exit 1 = out of date → fix the guide (or the UI). `*.changes.md` = review the image diff
   (`stepNN.png` vs `stepNN.prev.png`) and commit the new screenshots if they're right.

## Tips

- Run against **stable data** — a mock or seeded mode — so screenshots only change when
  the UI does. Live model output or timestamps in a crop will show up as changes.
- The caption bar is hidden in docs screenshots; the highlight outline stays, so readers
  see where to click.
- `--threshold` (default 0.985 SSIM) trades sensitivity for noise; `--fail-on-change`
  makes any visual change fail CI.
- A guide is also a demo: `python3 $ENGINE/scripts/build.py guides/x.yaml --base URL --out
  videos` makes a narrated video of it (add `concept:`/`outro:` slides if you like).
