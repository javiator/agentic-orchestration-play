---
name: briefing
description: Turn data or documents (CSV exports, reports, PDFs, notes, a topic) into a short narrated briefing — a 2–4 minute video of title, key-number, chart and takeaway slides, plus an audio-only version for listening on the go, and an archive page of past briefs. Use when the user asks for a video or audio summary, a weekly/monthly update, a narrated report, a "podcast" of a document, an explainer for stakeholders, or slides with voice-over from their data. Builds on the demo-video skill; no running app needed.
---

# briefing

Data or documents in → narrated slides out (`OUT/<slug>.mp4` + `OUT/<slug>.m4a` +
subtitles). It writes a slides-only demo-video YAML (no `steps:`), so the **demo-video**
skill must be installed next to this one (`$ENGINE` = `$SKILL/../demo-video`).
Everything runs locally; source data never leaves the machine.

## Workflow

1. **Intake — ask only what's missing:** topic and objective (inform / persuade /
   update), audience (exec, technical, general — pick one per brief), sources, target
   length (default 3 min), video, audio or both, brand colours (→ a `--css` theme).
2. **Facts, with provenance.** Keep `WORK/facts.md`: every number or claim you'll use,
   each with its source.
   - CSV / spreadsheet export: `python3 $SKILL/scripts/data_brief.py profile data.csv`, then
     `agg` queries (`--value "units*price" --by region --split "year(month)"`,
     `--pct-change 2024 2025`, `--where`, `--func mean`). Its JSON is already a `chart:`
     block and `stats` tiles: paste it, don't retype numbers. Record each command in facts.md.
   - PDFs and documents: read them (the Read tool handles PDFs page by page); quote page
     numbers in facts.md.
   - Web research only if the user wants it; keep researched facts apart from given
     ones and cite URLs.
3. **Storyline** (`WORK/storyline.md`): hook (the one-sentence headline) → the numbers
   that prove it → what's changing and why → what to do. One idea per slide; 5–8 slides.
4. **Write `WORK/<slug>.yaml`** — see `examples/sales-brief.yaml` (built from
   `examples/sales.csv`): `title` (headline as the title), `stats` (3 key numbers),
   `chart` slides (one message each, stated in the title), `bullets`/`boxes` for
   qualitative points, `takeaways` = actions. Narration: ~150 words/min, short sentences,
   round numbers the way people say them ("seven hundred and sixty thousand dollars");
   never a number that isn't on the slide or in facts.md.
5. **Review:** `python3 $ENGINE/scripts/build.py WORK/<slug>.yaml --out OUT --dry-run` renders
   every slide (`OUT/<slug>/shots/`) and prints the narration. Show the user; wait for OK.
6. **Build:** `python3 $ENGINE/scripts/build.py WORK/<slug>.yaml --out OUT --audio`.
7. **QA:** every number traceable to facts.md · narration matches each slide · length on
   target · chart titles state the message · no slide overflows (look at the PNGs).
8. **Archive** (optional): keep briefs in one folder and render a page of them:
   `python3 $ENGINE/scripts/hub.py briefs/*.yaml --build-dir OUT --posters --title "Briefs"`
   (newest first: pass the YAMLs in that order; set `hub: {eyebrow: "<date>"}` in each).

## Slide toolkit (demo-video kinds)

`title` · `stats` (`[{value, label, delta, good}]`) · `chart` (`bar` for comparisons,
`hbar` for rankings and % changes, `line` for trends; `unit`, `note`) · `bullets` ·
`boxes` (2–4 columns) · `diagram` (ASCII) · `image` (an existing PNG/SVG chart) ·
`takeaways`. Charts are SVG, drawn from the numbers — no plotting libraries needed.

## Notes

- Audio-only listeners can't see slides: when `--audio` matters, say the key number
  in words in every chart's narration (the example does).
- Voice: the YAML's `tts:`/`voice:` (Kokoro `am_michael` in the example); see demo-video.
- Later (designed for, not built): ElevenLabs voices via a new TTS backend; image
  generation for illustrations via an `image` slide.
