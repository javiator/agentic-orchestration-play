---
name: pr-walkthrough
description: Make a short narrated walkthrough video of a pull request, branch or release — what changed and why, the new behaviour shown live in the running app, key code on slides, and how to review it. Use when the user asks for a PR/branch/release walkthrough, a video for reviewers or product managers, "show what this PR changes", video release notes, or a CI job that records PR videos. Builds on the demo-video skill.
---

# pr-walkthrough

Turns a diff into a 2–4 minute video for reviewers: title → what changed (bullets) →
the change working in the real app → key code (highlighted added lines) → how to review.
It writes a demo YAML and builds it with the **demo-video** skill, which must be installed
next to this one (`<skills>/demo-video/`). `$SKILL` = this folder, `$ENGINE` =
`$SKILL/../demo-video`.

## Workflow

1. **Brief.** `python3 $SKILL/scripts/pr_context.py --base origin/main --out WORK/brief.md`
   (`--pr-body body.md` to include the PR description; any refs work, e.g. `--base v1.4.0`
   for release notes). Read the brief: commits, files by area, **hooks** (new element ids,
   routes, CLI flags, env vars — what can be shown live) and the largest hunks with
   ready-made `code` slide snippets (for long new files it lists definitions with line
   ranges; pick one ≤ 30 lines). Read the actual diff of the files that matter.
2. **Decide what to show.** User-visible changes are shown live; internal changes get a
   `code` slide or a `diagram`. If nothing is user-visible (a refactor, a library), the
   video is slides only (no `steps:`, no app needed) — say so instead of inventing a demo.
   Write the narration for a reviewer: why first, then what, then where to look.
3. **Run the branch.** Start the app from this branch the way the project documents it
   (README / AGENTS.md / HANDOFF); prefer a mock/fast mode for the dry run. Explore the
   changed pages with `python3 $ENGINE/scripts/explore.py URL --out WORK/explore`.
4. **Write `WORK/walkthrough.yaml`** from `templates/walkthrough.yaml` (demo-video format;
   `code` slides take `file:` paths relative to the YAML — use absolute paths or put the
   YAML at the repo root, e.g. `.walkthrough/<branch>.yaml`). Add `expect:` checks to the
   steps that prove the change works (they fail the build if it doesn't).
5. **Dry run, review, record** with the engine:
   `python3 $ENGINE/scripts/build.py WORK/walkthrough.yaml --base URL --out WORK/out --dry-run`,
   show the user the screenshots + narration, then build without `--dry-run`.
   Verify per demo-video's step 5 (timeline.json problems, frames vs narration).
6. **Deliver.** Give the MP4 + `.srt`. To put it on the PR: GitHub has no API to attach
   video, so upload it where the team can play it (an artifact page, a release asset, the
   CI run's artifacts) and comment with the link — only when the user asks you to post.

## Good walkthroughs

- 2–4 minutes; 1 title, 1 bullets slide, ≤ 3 code slides, live steps for each visible change.
- Lead with the user-facing effect ("Routing now escalates low-confidence requests"), not
  the file list. Name risks and what reviewers should test in the takeaways.
- Code slides: ≤ 30 lines, highlight only the added lines that carry the idea.
- Before/after: if the base branch can run too, record the old behaviour as a short
  step sequence first (a second YAML built against the base), or describe it on a slide.

## In CI

`templates/github-action.yml` builds `.walkthrough/*.yaml` committed on the PR (written
with this skill locally, or by a Claude Code GitHub Action step) when the PR gets the
`walkthrough` label, uploads the MP4 as a run artifact and comments with the link. Copy
it to `.github/workflows/`, then fill in the "start the app" step for the project.
