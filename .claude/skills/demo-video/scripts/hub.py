"""Render a hub page (one self-contained HTML file) listing several videos.

    python3 hub.py demos/*.yaml --title "Lessons" --intro "One video per ..." \
        [--build-dir OUT --posters] [--urls urls.json] [--videos PREFIX] [--footer "..."] > hub.html

With --build-dir (build.py's --out), each card gets its duration, clickable
chapters and a transcript from timeline.json; --posters also writes a poster
frame per video (<build-dir>/<slug>.jpg) taken from the live demo.

Each YAML's optional `hub:` block supplies the card:
    hub:
      eyebrow: "Exercise 2 of 12"       # small label above the title
      title: Router                     # default: the first slide's title
      summary: "..."                    # one paragraph
      takeaways: [..]                   # default: the takeaways slide's items
      tries: [..]                       # "try this" suggestions
      diagram: |                        # default: the first diagram slide
      code: https://...                 # "Read the code →" link
Video sources: --urls maps slug → URL or {video, poster} (e.g. files uploaded
next to the page); otherwise <videos>/<slug>.mp4 and <videos>/<slug>.jpg. Cards follow the order of the YAML arguments.
"""

from __future__ import annotations

import argparse
import html
import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from setup import ensure_env  # noqa: E402

ensure_env()

import yaml  # noqa: E402

CSS = """
:root {
  --bg: #f3f5f9; --surface: #ffffff; --ink: #18203a; --muted: #5a6585; --line: #dbe1ee;
  --accent: #b7791f; --accent-soft: #fbf1df; --code-bg: #0f1628; --code-ink: #dbe7ff;
  --display: "Bricolage Grotesque", "DejaVu Sans", system-ui, sans-serif;
  --body: "Source Sans 3", "DejaVu Sans", system-ui, sans-serif;
  --mono: "JetBrains Mono", "DejaVu Sans Mono", ui-monospace, monospace;
}
@media (prefers-color-scheme: dark) { :root:not([data-theme="light"]) {
  --bg: #0d1324; --surface: #151d33; --ink: #e8ecf6; --muted: #9aa6c6; --line: #283456;
  --accent: #f5b94a; --accent-soft: #2a2a2a; --code-bg: #0a1020; --code-ink: #dbe7ff; color-scheme: dark } }
:root[data-theme="dark"] {
  --bg: #0d1324; --surface: #151d33; --ink: #e8ecf6; --muted: #9aa6c6; --line: #283456;
  --accent: #f5b94a; --accent-soft: #2a2a2a; --code-bg: #0a1020; --code-ink: #dbe7ff; color-scheme: dark }
body { background: var(--bg); color: var(--ink); font: 17px/1.55 var(--body); margin: 0; }
.wrap { max-width: 920px; margin: 0 auto; padding-block: 40px 80px;
  padding-left: max(16px, env(safe-area-inset-left, 0px)); padding-right: max(16px, env(safe-area-inset-right, 0px)); }
header h1 { font: 800 clamp(30px, 6vw, 46px)/1.1 var(--display); text-wrap: balance; margin: 0 0 10px; }
header p { color: var(--muted); max-width: 62ch; margin: 0; }
nav.toc { display: flex; flex-wrap: wrap; gap: 8px; margin: 24px 0 8px; }
nav.toc a { font-size: 14px; text-decoration: none; color: var(--ink); border: 1px solid var(--line);
  border-radius: 999px; padding: 4px 12px; background: var(--surface); }
nav.toc a:hover, nav.toc a:focus-visible { border-color: var(--accent); outline: none; }
nav.toc b { color: var(--accent); font-variant-numeric: tabular-nums; margin-right: 4px; }
section.ex { background: var(--surface); border: 1px solid var(--line); border-radius: 14px;
  padding: clamp(16px, 4vw, 28px); margin-top: 28px; display: grid; gap: 16px; min-width: 0; }
.eyebrow { font: 600 13px var(--body); letter-spacing: .08em; text-transform: uppercase; color: var(--accent); }
section.ex h2 { font: 750 clamp(22px, 4vw, 30px)/1.15 var(--display); margin: 2px 0 0; text-wrap: balance; }
video { width: 100%; max-width: 100%; aspect-ratio: 16 / 10; border-radius: 10px; background: #000; display: block; }
.summary { margin: 0; max-width: 68ch; }
details { border-top: 1px solid var(--line); padding-top: 12px; }
summary { cursor: pointer; font-weight: 600; }
pre { background: var(--code-bg); color: var(--code-ink); font: 12.5px/1.35 var(--mono); padding: 16px;
  border-radius: 10px; overflow-x: auto; margin: 12px 0 0; }
.cols { display: grid; grid-template-columns: 1fr 1fr; gap: 20px; }
@media (max-width: 640px) { .cols { grid-template-columns: 1fr; } }
.cols > div { min-width: 0; }
h3 { font: 700 14px var(--body); letter-spacing: .06em; text-transform: uppercase; color: var(--muted); margin: 0 0 8px; }
ol, ul { margin: 0; padding-left: 22px; } li { margin-bottom: 6px; }
.try li { background: var(--accent-soft); border-radius: 8px; padding: 6px 10px; list-style-position: inside; margin-left: -22px; }
a.code { color: var(--accent); font-weight: 600; }
footer { color: var(--muted); font-size: 14px; margin-top: 40px; }
nav.toc .dur { color: var(--muted); font-size: 12px; margin-left: 6px; font-variant-numeric: tabular-nums; }
.chapters { display: flex; flex-wrap: wrap; gap: 6px; }
.chapters button, .transcript button { font: inherit; font-size: 14px; color: var(--ink); background: var(--bg);
  border: 1px solid var(--line); border-radius: 999px; padding: 3px 10px; cursor: pointer; }
.chapters button span { color: var(--accent); font-variant-numeric: tabular-nums; margin-right: 6px; font-weight: 600; }
.chapters button:hover, .chapters button:focus-visible, .transcript button:hover { border-color: var(--accent); outline: none; }
.transcript p { margin: 8px 0; max-width: 72ch; }
.transcript button { padding: 1px 8px; margin-right: 8px; font-size: 12px; color: var(--accent);
  font-variant-numeric: tabular-nums; }
code { font-family: var(--mono); font-size: .9em; }
"""


def card(spec: dict) -> dict:
    """The hub fields for one demo, with defaults taken from its slides."""
    hub = dict(spec.get("hub") or {})
    slides = (spec.get("concept") or []) + (spec.get("outro") or [])
    first = next((s for s in slides if s.get("title")), {})
    hub.setdefault("title", first.get("title", spec["slug"]))
    hub.setdefault("summary", first.get("lead", ""))
    hub.setdefault("takeaways", next((s["items"] for s in slides if s["kind"] == "takeaways"), []))
    hub.setdefault("diagram", next((s["diagram"] for s in slides if s["kind"] == "diagram"), ""))
    for key in ("tries", "eyebrow", "code", "toc_label"):
        hub.setdefault(key, [] if key == "tries" else "")
    return hub


def _mmss(t: float) -> str:
    return f"{int(t) // 60}:{int(t) % 60:02d}"


def make_poster(mp4: Path, timeline: dict, out: Path) -> None:
    """A frame from the live demo (a few seconds into its first step), as JPEG."""
    demo = next((c["t"] for c in timeline.get("chapters", []) if c.get("part") == "demo"), 2.0)
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-ss", f"{demo + 4:.2f}", "-i", str(mp4), "-frames:v", "1",
                    "-q:v", "4", "-vf", "scale=960:-2", str(out)], check=True)


JS = """document.addEventListener('click', ev => {
  const b = ev.target.closest('[data-t]'); if (!b) return;
  const v = b.closest('section').querySelector('video'); if (!v) return;
  v.currentTime = parseFloat(b.dataset.t); v.play().catch(() => {});
  v.scrollIntoView({behavior: 'smooth', block: 'center'});
});"""


def render(specs: list[dict], urls: dict, videos: str, title: str, intro: str, footer: str,
           timelines: dict[str, dict] | None = None, posters: set[str] | None = None) -> str:
    e = html.escape
    timelines, posters = timelines or {}, posters or set()
    cards = [(s["slug"], card(s)) for s in specs]
    prefix = videos.rstrip("/") + "/"

    def dur(slug: str) -> str:
        tl = timelines.get(slug)
        return f'<span class="dur">{_mmss(tl["duration"])}</span>' if tl else ""

    toc = "".join(f'<a href="#{e(slug)}"><b>{e(str(c["toc_label"] or i))}</b>{e(c["title"])}{dur(slug)}</a>'
                  for i, (slug, c) in enumerate(cards, 1))
    sections = []
    for slug, c in cards:
        u = urls.get(slug) or {}
        u = {"video": u} if isinstance(u, str) else u
        src = u.get("video") or f"{prefix}{slug}.mp4"
        poster = u.get("poster") or (f"{prefix}{slug}.jpg" if slug in posters else "")
        lists = ""
        if c["takeaways"] or c["tries"]:
            lists = '<div class="cols">'
            if c["takeaways"]:
                lists += f"<div><h3>Takeaways</h3><ol>{''.join(f'<li>{e(t)}</li>' for t in c['takeaways'])}</ol></div>"
            if c["tries"]:
                lists += f"<div><h3>Try this</h3><ul class=\"try\">{''.join(f'<li>{e(t)}</li>' for t in c['tries'])}</ul></div>"
            lists += "</div>"
        tl = timelines.get(slug)
        chapters = transcript = ""
        if tl:
            chapters = ('<div><h3>Chapters</h3><div class="chapters">' + "".join(
                f'<button type="button" data-t="{ch["t"]}"><span>{_mmss(ch["t"])}</span>{e(ch["title"])}</button>'
                for ch in tl.get("chapters", [])) + "</div></div>")
            transcript = ('<details class="transcript"><summary>Transcript</summary>' + "".join(
                f'<p><button type="button" data-t="{ln["t"]}">{_mmss(ln["t"])}</button>{e(ln["text"])}</p>'
                for ln in tl.get("lines", [])) + "</details>")
        diagram = f"<details><summary>The diagram</summary><pre>{e(c['diagram'])}</pre></details>" if c["diagram"] else ""
        code = f'<div><a class="code" href="{e(c["code"])}">Read the code →</a></div>' if c["code"] else ""
        eyebrow = f'<div class="eyebrow">{e(c["eyebrow"])}{" · " + _mmss(tl["duration"]) if tl else ""}</div>' \
            if c["eyebrow"] or tl else ""
        poster_attr = f' poster="{e(poster)}"' if poster else ""
        sections.append(f"""<section class="ex" id="{e(slug)}">
<div>{eyebrow}<h2>{e(c['title'])}</h2></div>
<video controls preload="metadata" playsinline src="{e(src)}"{poster_attr}></video>
<p class="summary">{e(c['summary'])}</p>
{chapters}
{lists}
{transcript}
{diagram}
{code}
</section>""")
    return f"""<title>{e(title)}</title>
<meta name="claude:viewport-fit-sides" content="cover">
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Bricolage+Grotesque:wght@700;800&family=JetBrains+Mono&family=Source+Sans+3:wght@400;600;700&display=swap">
<style>{CSS}</style>
<div class="wrap">
<header><h1>{e(title)}</h1>
<p>{e(intro)}</p></header>
<nav class="toc">{toc}</nav>
{''.join(sections)}
<footer>{e(footer)}</footer>
</div>
<script>{JS}</script>
"""


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("demos", nargs="+", type=Path)
    ap.add_argument("--urls", type=Path, help="JSON: slug → video URL, or slug → {video, poster}")
    ap.add_argument("--videos", default=".", help="folder/URL prefix for <slug>.mp4/.jpg when not in --urls")
    ap.add_argument("--build-dir", type=Path, help="build.py --out folder: adds durations, chapters, transcripts")
    ap.add_argument("--posters", action="store_true", help="write <build-dir>/<slug>.jpg poster frames")
    ap.add_argument("--title", default="Demo videos")
    ap.add_argument("--intro", default="")
    ap.add_argument("--footer", default="Videos built with the demo-video skill.")
    args = ap.parse_args()
    specs = []
    for path in args.demos:
        spec = yaml.safe_load(path.read_text())
        spec.setdefault("slug", path.stem)
        specs.append(spec)
    urls = json.loads(args.urls.read_text()) if args.urls else {}
    timelines, posters = {}, set()
    if args.build_dir:
        for spec in specs:
            slug = spec["slug"]
            tl_path = args.build_dir / slug / "timeline.json"
            if not tl_path.exists():
                print(f"[hub] no timeline for {slug} (not built yet?)", file=sys.stderr)
                continue
            timelines[slug] = json.loads(tl_path.read_text())
            jpg = args.build_dir / f"{slug}.jpg"
            if args.posters and (args.build_dir / f"{slug}.mp4").exists():
                make_poster(args.build_dir / f"{slug}.mp4", timelines[slug], jpg)
            if jpg.exists():
                posters.add(slug)
    print(render(specs, urls, args.videos, args.title, args.intro, args.footer, timelines, posters))
    return 0


if __name__ == "__main__":
    sys.exit(main())
