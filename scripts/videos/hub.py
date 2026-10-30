"""Render the learning-hub page (one HTML file) from content.py.

    .videovenv/bin/python scripts/videos/hub.py [video_urls.json] > hub.html

video_urls.json maps slug → video URL (the uploaded artifact assets);
without it, videos point at out/<slug>.mp4 for local viewing.
"""

from __future__ import annotations

import html
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import content  # noqa: E402

CSS = """
:root {
  /* Layout: one reading column; overview first, then one section per exercise in course order. */
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
body { background: var(--bg); color: var(--ink); font: 17px/1.55 var(--body); }
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
code { font-family: var(--mono); font-size: .9em; }
"""


def render(urls: dict[str, str]) -> str:
    e = html.escape
    specs = content.videos()
    toc = "".join(f'<a href="#{s["slug"]}"><b>{s["n"] or "▶"}</b>{e(s["title"])}</a>' for s in specs)
    sections = []
    for s in specs:
        src = urls.get(s["slug"], f"out/{s['slug']}.mp4")
        eyebrow = "Start here · overview" if s["slug"] == "overview" else f"Exercise {s['n']} of 12"
        extra = ""
        if s["takeaways"]:
            extra = f"""<div class="cols">
  <div><h3>Takeaways</h3><ol>{''.join(f'<li>{e(t)}</li>' for t in s['takeaways'])}</ol></div>
  <div><h3>Try this</h3><ul class="try">{''.join(f'<li>{e(t)}</li>' for t in s['tries'])}</ul></div>
</div>"""
        sections.append(f"""<section class="ex" id="{s['slug']}">
<div><div class="eyebrow">{eyebrow}</div><h2>{e(s['title'])}</h2></div>
<video controls preload="metadata" playsinline src="{e(src)}"></video>
<p class="summary">{e(s['summary'])}</p>
{extra}
<details><summary>The diagram</summary><pre>{e(s['diagram'])}</pre></details>
<div><a class="code" href="{e(s['code'])}">Read the code →</a></div>
</section>""")
    return f"""<title>Orchestration Playground Lessons</title>
<meta name="claude:viewport-fit-sides" content="cover">
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Bricolage+Grotesque:wght@700;800&family=JetBrains+Mono&family=Source+Sans+3:wght@400;600;700&display=swap">
<style>{CSS}</style>
<div class="wrap">
<header><h1>Agentic Orchestration Playground — lessons</h1>
<p>One narrated video per exercise: the concept, a live demo with real models, and three takeaways.
Start with the 3-minute overview, then go in order, or jump to a pattern.</p></header>
<nav class="toc">{toc}</nav>
{''.join(sections)}
<footer>Videos built by <code>scripts/videos/build.py</code> · demos recorded against real models (OpenAI + Gemini).</footer>
</div>
"""


if __name__ == "__main__":
    urls = json.loads(Path(sys.argv[1]).read_text()) if len(sys.argv) > 1 else {}
    print(render(urls))
