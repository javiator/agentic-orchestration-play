"""Slides: HTML (one template per kind) rendered by Playwright to PNG at 1280×800.

Kinds and their fields (all take `say`, optional `foot` and `pause`):
    title      title, lead, chips: [..]
    boxes      title, boxes: [{head, items: [..]}, ..]   (2–4 boxes read best)
    diagram    title, diagram: |  (monospace text; the font shrinks to fit)
    takeaways  title (default "Takeaways"), items: [..]

The theme is templates/slide.css; pass another file (same class names) to restyle.
"""

from __future__ import annotations

import html
from pathlib import Path

from common import H, SKILL, W

DEFAULT_CSS = SKILL / "templates" / "slide.css"


def slide_html(slide: dict, kicker: str, css: str) -> str:
    e = html.escape
    kind = slide["kind"]
    body = f'<div class="kicker">{e(kicker)}</div>' if kicker else ""
    if kind == "title":
        body += f'<h1>{e(slide["title"])}</h1><p class="lead">{e(slide.get("lead", ""))}</p>'
        if slide.get("chips"):
            body += '<div class="chips">' + "".join(f'<span class="chip">{e(c)}</span>' for c in slide["chips"]) + "</div>"
    elif kind == "boxes":
        boxes = [(b["head"], b["items"]) if isinstance(b, dict) else tuple(b) for b in slide["boxes"]]
        body += f'<h2>{e(slide["title"])}</h2><div class="cols" style="--n:{len(boxes)}">'
        for head, items in boxes:
            body += f'<div class="box"><h3>{e(head)}</h3><ul>' + "".join(f"<li>{e(i)}</li>" for i in items) + "</ul></div>"
        body += "</div>"
    elif kind == "diagram":
        body += f'<h2>{e(slide["title"])}</h2><pre id="d">{e(slide["diagram"].rstrip())}</pre>'
    elif kind == "takeaways":
        body += f'<h2>{e(slide.get("title", "Takeaways"))}</h2>'
        body += "".join(f'<div class="take"><div class="n">{i}</div><p>{e(t)}</p></div>'
                        for i, t in enumerate(slide["items"], 1))
    else:
        raise ValueError(f"unknown slide kind: {kind!r} (title | boxes | diagram | takeaways)")
    if slide.get("foot"):
        body += f'<div class="foot">{e(slide["foot"])}</div>'
    return f"<!doctype html><html><head><meta charset='utf-8'><style>{css}</style></head><body>{body}</body></html>"


# Shrink the diagram font until the block fits its box (diagrams vary a lot in size).
FIT_DIAGRAM = """() => { const p = document.getElementById('d'); if (!p) return;
  let s = 26; p.style.fontSize = s + 'px';
  while (s > 11 && (p.scrollWidth > p.clientWidth || p.scrollHeight > p.clientHeight)) { s -= 0.5; p.style.fontSize = s + 'px'; } }"""


def render_slides(browser, slides: list[dict], kicker: str, outdir: Path, css_path: Path | None = None) -> list[Path]:
    outdir.mkdir(parents=True, exist_ok=True)
    css = Path(css_path or DEFAULT_CSS).read_text()
    page = browser.new_page(viewport={"width": W, "height": H})
    paths = []
    for i, slide in enumerate(slides):
        page.set_content(slide_html(slide, kicker, css))
        page.evaluate(FIT_DIAGRAM)
        path = outdir / f"slide{i:02d}.png"
        page.screenshot(path=str(path))
        paths.append(path)
    page.close()
    return paths
