"""Build the narrated learning videos (one per exercise + an overview).

Setup (once; a separate venv so the app's dependencies stay small):

    uv venv .videovenv && uv pip install -r scripts/videos/requirements.txt --python .videovenv/bin/python

Then, with the app running against REAL models (./start.sh):

    .videovenv/bin/python scripts/videos/build.py              # every video
    .videovenv/bin/python scripts/videos/build.py router rag   # just these
    .videovenv/bin/python scripts/videos/build.py --reuse rag  # re-assemble, keep the last recording
    BASE=http://localhost:8001 ...                             # another server (e.g. a mock one for dry runs)

Output: scripts/videos/out/<slug>.mp4 (gitignored), ~3–5 min, 1152×720,
H.264, narration burned in as subtitles. Content (slides, narration,
takeaways) lives in content.py; the scripted demos in demos.py.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from playwright.sync_api import sync_playwright  # noqa: E402

import content  # noqa: E402
import demos  # noqa: E402
from lib import (OUT, Narrator, concat_final, demo_clip, media_duration, record_demo, render_slides,  # noqa: E402
                 slide_clip, write_srt)

BASE = os.getenv("BASE", "http://localhost:8000").rstrip("/")


def build(spec: dict, browser, narrator: Narrator, reuse: bool) -> Path:
    slug = spec["slug"]
    work = OUT / slug
    work.mkdir(parents=True, exist_ok=True)
    parts: list[Path] = []
    cues: list[dict] = []
    clock = 0.0

    def add_slides(slides: list[dict], prefix: str) -> None:
        nonlocal clock
        pngs = render_slides(browser, slides, spec["kicker"], work / prefix)
        for i, (slide, png) in enumerate(zip(slides, pngs)):
            wav, dur = narrator.say(slide["say"])
            seconds = 0.4 + dur + slide.get("pause", 1.0)
            clip = work / f"{prefix}{i:02d}.mp4"
            slide_clip(png, wav, seconds, clip)
            cues.append({"t": clock + 0.4, "dur": dur, "text": slide["say"]})
            parts.append(clip)
            clock += media_duration(clip)

    add_slides(spec["concept"], "concept")

    script = getattr(demos, spec["demo"], None) if spec.get("demo") else None
    if script:
        timeline_path = work / "demo" / "timeline.json"
        if reuse and timeline_path.exists():
            timeline = json.loads(timeline_path.read_text())
        else:
            print(f"  recording {slug} demo against {BASE} …", flush=True)
            timeline = record_demo(browser, narrator, BASE, script, work / "demo")
        for problem in timeline["log"]:
            print(f"  ! {problem}")
        clip = work / "demo.mp4"
        _, narr = demo_clip(timeline, clip)
        cues += [{"t": clock + n["t"], "dur": n["dur"], "text": n["text"]} for n in narr]
        parts.append(clip)
        clock += media_duration(clip)

    add_slides(spec["outro"], "outro")

    srt = work / "subs.srt"
    write_srt(cues, srt)
    final = OUT / f"{slug}.mp4"
    concat_final(parts, srt, final)
    return final


def main() -> int:
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    reuse = "--reuse" in sys.argv
    specs = content.videos()
    wanted = args or [s["slug"] for s in specs]
    unknown = set(wanted) - {s["slug"] for s in specs}
    if unknown:
        print(f"Unknown: {sorted(unknown)}. Choose from: {', '.join(s['slug'] for s in specs)}")
        return 2
    narrator = Narrator()
    with sync_playwright() as p:
        browser = p.chromium.launch()
        for spec in specs:
            if spec["slug"] not in wanted:
                continue
            print(f"▶ {spec['slug']}", flush=True)
            final = build(spec, browser, narrator, reuse)
            size = final.stat().st_size / 1e6
            print(f"  ✓ {final}  {media_duration(final) / 60:.1f} min  {size:.1f} MB", flush=True)
        browser.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
