---
name: bug-repro
description: Reproduce a reported bug in a running web app by driving it with Playwright, then save the reproduction as a replayable script with checks, a report (screenshots, console errors, failed requests) and optionally a narrated evidence video; replay it on a fix branch to prove the fix. Use when the user shares a bug report or issue for a web UI and wants it reproduced, verified, turned into a regression check, or recorded; or asks "does this bug still happen?". Builds on the demo-video skill.
---

# bug-repro

You are the agent: you read the report, drive the app, and keep what worked as a
**repro script** — a demo-video YAML whose `expect:` checks describe the **correct**
behaviour. `scripts/repro.py` replays it headless and gives a verdict:

| verdict | meaning | exit |
|---|---|---|
| REPRODUCED | an `expect:` check failed — the bug happens | 1 |
| NOT REPRODUCED | every check passed — bug absent or fixed | 0 |
| INCONCLUSIVE | the script broke (selector/action/wait) or replays disagree (flaky) | 2 |

Requires the **demo-video** skill next to this one (`$ENGINE` = `$SKILL/../demo-video`).

## Workflow

1. **Read the report.** Write down: expected vs actual, the page, the inputs, the
   environment (browser, account, data). Missing facts become variations to try.
2. **Safe target only.** Run against a local/dev/staging app with test data, never
   production. Actions that send email, move money, delete data or touch real users:
   ask the user first (the same tiers as a human-in-the-loop approval).
3. **Explore and try.** `python3 $ENGINE/scripts/explore.py URL --out WORK/explore
   --do 'fill:#qty=' --do 'click:#apply' --do 'shot:after-apply'`. Each run starts fresh
   and replays the `--do` steps, so a working sequence is already reproducible. The
   inventory `.md` lists selectors and the **console errors / failed requests** seen since
   the last snapshot. Log every attempt (steps → outcome) in `WORK/attempts.md`.
4. **Write the repro** (`examples/shop-total.yaml` is a complete one): a `bug:` block
   (title, issue, expected, actual, `max_wait`), the **minimal** steps, and `expect:` on the
   step where correct behaviour should show: `text: [SEL, regex]`, `visible`, `hidden`,
   `js`, `no_errors: true` (page/console errors, failed requests, HTTP 5xx). Narration
   (`say`) states what is done and seen, in plain words — it becomes the video.
5. **Confirm:** `python3 $SKILL/scripts/repro.py WORK/repro.yaml --base URL --out WORK/out --runs 3`.
   Must say REPRODUCED all 3 times. INCONCLUSIVE → fix the script (selectors, waits,
   test data) until it's deterministic. Read `WORK/out/<slug>/report.md`.
6. **Evidence.** `--video` adds the narrated MP4 (title slide → steps → takeaways slide
   explaining the cause if you found it). Give the user: verdict, report, video, the YAML.
   Suggest committing the YAML (e.g. `repro/issue-42.yaml`) as a regression check.
7. **Verify a fix:** start the fix branch (another port) and run
   `repro.py repro.yaml --base BUGGY_URL --also FIXED_URL` → REPRODUCED then NOT REPRODUCED.
   In CI, `repro.py repro.yaml --base URL` exits 0 only when the bug is gone.

**Could not reproduce?** Say so plainly. Report what was tried (`attempts.md`), the
environment, what differs from the reporter's, and the questions that would unblock it.
Never write a script that "reproduces" by forcing the outcome (e.g. a `js:` action that
injects the error).

## Tips

- Check the starting state with an `expect:` too (step 1 in the example): it proves the
  script is testing the right page and data.
- Bugs that depend on timing: wait for a visible result (`wait:`), never fixed sleeps.
- Read the source once the bug reproduces; a one-line cause on the takeaways slide saves
  the developer an hour.
