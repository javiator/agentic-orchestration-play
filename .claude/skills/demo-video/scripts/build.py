"""Build narrated demo videos from YAML demo scripts.

    python3 build.py DEMO.yaml [DEMO2.yaml ...] --base http://localhost:8000 --out ./videos
        [--dry-run]    screenshots per step + slide PNGs, no TTS, no MP4 (fast review)
        [--reuse]      re-assemble from the last recording (after editing slides/outro)
        [--tts auto|piper|espeak]   [--css theme.css]

Output, per demo (slug = `slug:` in the YAML, else the file name):
    OUT/<slug>.mp4            1152×720 H.264, narration + burned-in subtitles
    OUT/<slug>.srt            the same subtitles, as a soft-sub file
    OUT/<slug>/timeline.json  every narration line with its time in the video,
                              chapters, and problems found while recording
    OUT/<slug>/shots/         (dry run) step01.png … + slide PNGs

Exit status 1 when any problem was found (all are listed in timeline.json).
The first run creates the skill's venv (see setup.py) and re-runs itself in it.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from setup import ensure_env  # noqa: E402

ensure_env()

import yaml  # noqa: E402
from playwright.sync_api import sync_playwright  # noqa: E402

from assemble import concat_final, demo_clip, slide_clip, write_srt  # noqa: E402
from common import SetupError, check_ffmpeg, launch_browser, media_duration  # noqa: E402
from narrate import Narrator  # noqa: E402
from record import record_demo  # noqa: E402
from slides import render_slides  # noqa: E402

MAX_MB = 15


def load(path: Path) -> dict:
    spec = yaml.safe_load(path.read_text())
    spec.setdefault("slug", path.stem)
    spec.setdefault("kicker", "")
    for key in ("concept", "steps", "outro"):
        spec[key] = spec.get(key) or []
    for i, s in enumerate(spec["steps"], 1):
        if "say" not in s:
            raise ValueError(f"{path}: step {i} has no `say`")
    return spec


def dry_run(spec: dict, browser, base: str, out: Path, css: Path | None) -> list[str]:
    work = out / spec["slug"]
    shots = work / "shots"
    for old in shots.glob("*.png") if shots.exists() else []:
        old.unlink()
    pngs = render_slides(browser, spec["concept"] + spec["outro"], spec["kicker"], shots, css)
    os.environ.setdefault("DEMO_MAX_WAIT", "90")
    tl = record_demo(browser, None, base, spec, work / "dry", shots=shots)
    print(f"  slides: {len(pngs)} PNGs; steps: {len(tl['narration'])} screenshots in {shots}")
    for n in tl["narration"]:
        print(f"  step{n['step']:02d}  [{n['caption']}] {n['text']}")
    return tl["log"]


def build(spec: dict, browser, narrator: Narrator, base: str, out: Path, reuse: bool, css: Path | None) -> Path:
    slug = spec["slug"]
    work = out / slug
    work.mkdir(parents=True, exist_ok=True)
    parts: list[Path] = []
    cues: list[dict] = []
    chapters: list[dict] = []
    problems: list[str] = []
    clock = 0.0

    def add_slides(slides: list[dict], prefix: str) -> None:
        nonlocal clock
        pngs = render_slides(browser, slides, spec["kicker"], work / prefix, css)
        for i, (slide, png) in enumerate(zip(slides, pngs)):
            wav, dur = narrator.say(slide["say"])
            seconds = 0.4 + dur + float(slide.get("pause", 1.0))
            clip = work / f"{prefix}{i:02d}.mp4"
            slide_clip(png, wav, seconds, clip)
            title = slide.get("title") or slide["kind"].title()
            chapters.append({"t": round(clock, 2), "title": title, "part": prefix})
            cues.append({"t": clock + 0.4, "dur": dur, "text": slide["say"], "part": prefix})
            parts.append(clip)
            clock += media_duration(clip)

    add_slides(spec["concept"], "concept")

    if spec["steps"]:
        rec_path = work / "demo" / "recording.json"
        if reuse and rec_path.exists():
            timeline = json.loads(rec_path.read_text())
            if any(not n.get("wav") for n in timeline["narration"]):
                raise SystemExit(f"{rec_path} is from a dry run; record without --reuse first")
        else:
            print(f"  recording {slug} against {base} …", flush=True)
            timeline = record_demo(browser, narrator, base, spec, work / "demo")
        problems += timeline["log"]
        clip = work / "demo.mp4"
        _, narr = demo_clip(timeline, clip)
        last_caption = None
        for n in narr:
            if n.get("caption") and n["caption"] != last_caption:
                chapters.append({"t": round(clock + n["t"], 2), "title": n["caption"], "part": "demo"})
                last_caption = n["caption"]
            cues.append({"t": clock + n["t"], "dur": n["dur"], "text": n["text"], "part": "demo"})
        parts.append(clip)
        clock += media_duration(clip)

    add_slides(spec["outro"], "outro")

    srt = out / f"{slug}.srt"
    write_srt(cues, srt)
    final = out / f"{slug}.mp4"
    concat_final(parts, srt, final)

    duration = media_duration(final)
    size_mb = final.stat().st_size / 1e6
    if size_mb > MAX_MB:
        problems.append(f"file is {size_mb:.1f} MB (> {MAX_MB} MB)")
    if not 120 <= duration <= 300:
        problems.append(f"video is {duration / 60:.1f} min (aim for 2–5)")
    (work / "timeline.json").write_text(json.dumps({
        "video": str(final), "duration": round(duration, 2), "size_mb": round(size_mb, 2),
        "problems": problems, "chapters": chapters,
        "lines": [{"t": round(c["t"], 2), "dur": round(c["dur"], 2), "part": c["part"], "text": c["text"]}
                  for c in cues],
    }, indent=1, ensure_ascii=False))
    return final


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("demos", nargs="+", type=Path, help="YAML demo scripts")
    ap.add_argument("--base", default=os.getenv("BASE", "http://localhost:8000"), help="base URL of the running app")
    ap.add_argument("--out", type=Path, default=Path("demo-videos"), help="output folder")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--reuse", action="store_true")
    ap.add_argument("--tts", default=os.getenv("DEMO_VIDEO_TTS", "auto"))
    ap.add_argument("--css", type=Path, help="slide theme (default templates/slide.css)")
    args = ap.parse_args()

    try:
        check_ffmpeg()
        specs = [load(p) for p in args.demos]
        narrator = None if args.dry_run else Narrator(args.tts)
    except (SetupError, ValueError, FileNotFoundError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    args.out.mkdir(parents=True, exist_ok=True)
    base = args.base.rstrip("/")
    failed = 0
    with sync_playwright() as p:
        browser = launch_browser(p)
        for spec in specs:
            print(f"▶ {spec['slug']}{' (dry run)' if args.dry_run else ''}", flush=True)
            if args.dry_run:
                problems = dry_run(spec, browser, base, args.out, args.css)
            else:
                final = build(spec, browser, narrator, base, args.out, args.reuse, args.css)
                problems = json.loads((args.out / spec["slug"] / "timeline.json").read_text())["problems"]
                print(f"  ✓ {final}  {media_duration(final) / 60:.1f} min  {final.stat().st_size / 1e6:.1f} MB")
            for problem in problems:
                print(f"  ! {problem}")
            failed += bool(problems)
        browser.close()
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
