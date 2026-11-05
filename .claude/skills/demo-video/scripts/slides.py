"""Slides: HTML (one template per kind) rendered by Playwright to PNG at 1280×800.

Kinds and their fields (all take `say`, optional `foot` and `pause`):
    title      title, lead, chips: [..]
    boxes      title, boxes: [{head, items: [..]}, ..]   (2–4 boxes read best)
    diagram    title, diagram: |  (monospace text; the font shrinks to fit)
    takeaways  title (default "Takeaways"), items: [..]
    bullets    title, lead (optional), items: [..]
    code       title, code: | or file: path + lines: "10-42"; highlight: [12, "15-18"]
               (file line numbers); path shown above the code. Font shrinks to fit.
    stats      title, stats: [{value, label, delta (optional, e.g. "+12%"), good: true|false}]
    chart      title, chart: {type: bar|hbar|line, labels: [..], series: [{name, values: [..]}],
               unit: "%" (optional), note (optional)}   — rendered as SVG, no dependencies
    image      title, image: path (PNG/JPG/SVG, embedded), caption (optional)
Paths (file:, image:) are relative to the YAML file.

The theme is templates/slide.css; pass another file (same class names) to restyle.
"""

from __future__ import annotations

import base64
import html
import mimetypes
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
    elif kind == "bullets":
        body += f'<h2>{e(slide["title"])}</h2>'
        if slide.get("lead"):
            body += f'<p class="lead tight">{e(slide["lead"])}</p>'
        body += '<ul class="bullets">' + "".join(f"<li>{e(i)}</li>" for i in slide["items"]) + "</ul>"
    elif kind == "code":
        body += f'<h2>{e(slide["title"])}</h2>'
        if slide.get("path"):
            body += f'<div class="path">{e(slide["path"])}</div>'
        hl = _line_set(slide.get("highlight") or [])
        first = int(slide.get("start", 1))
        lines = slide["code"].rstrip("\n").expandtabs(4).splitlines()
        rows = "".join(f'<div class="ln{" hl" if first + i in hl else ""}"><span class="no">{first + i}</span>'
                       f'<span class="src">{e(line) or " "}</span></div>'
                       for i, line in enumerate(lines))
        # Size the font so every line fits: height (~520px) and width (~1060px of text).
        widest = max((len(x) for x in lines), default=1) + 6
        size = max(10.0, min(24.0, 520 / (len(lines) * 1.38), 1060 / (widest * 0.61)))
        body += f'<div class="code" style="font-size:{size:.1f}px">{rows}</div>'
    elif kind == "stats":
        tiles = ""
        for st in slide["stats"]:
            delta = ""
            if st.get("delta") not in (None, ""):
                tone = "good" if st.get("good", True) else "bad"
                delta = f'<div class="delta {tone}">{e(str(st["delta"]))}</div>'
            tiles += f'<div class="stat"><div class="v">{e(str(st["value"]))}</div><div class="l">{e(st["label"])}</div>{delta}</div>'
        body += f'<h2>{e(slide["title"])}</h2><div class="stats" style="--n:{min(len(slide["stats"]), 4)}">{tiles}</div>'
    elif kind == "chart":
        body += f'<h2>{e(slide["title"])}</h2><div class="chart">{chart_svg(slide["chart"])}</div>'
        if slide["chart"].get("note"):
            body += f'<div class="note">{e(slide["chart"]["note"])}</div>'
    elif kind == "image":
        body += f'<h2>{e(slide["title"])}</h2><div class="image"><img src="{slide["image"]}" alt=""></div>'
        if slide.get("caption"):
            body += f'<div class="note">{e(slide["caption"])}</div>'
    else:
        raise ValueError(f"unknown slide kind: {kind!r} (title | boxes | diagram | takeaways | bullets | code | "
                         "stats | chart | image)")
    if slide.get("foot"):
        body += f'<div class="foot">{e(slide["foot"])}</div>'
    return f"<!doctype html><html><head><meta charset='utf-8'><style>{css}</style></head><body>{body}</body></html>"


def _line_set(spec: list) -> set[int]:
    out: set[int] = set()
    for item in spec:
        a, _, b = str(item).partition("-")
        out.update(range(int(a), int(b or a) + 1))
    return out


def resolve(slide: dict, base: Path) -> dict:
    """Load file-backed fields (code file:, image:) relative to the YAML's folder."""
    slide = dict(slide)
    if slide.get("kind") == "code" and slide.get("file") and "code" not in slide:
        path = (base / slide["file"]).resolve()
        lines = path.read_text(errors="replace").splitlines()
        a, _, b = str(slide.get("lines", f"1-{len(lines)}")).partition("-")
        a, b = int(a), int(b or a)
        slide["code"] = "\n".join(lines[a - 1:b])
        slide.setdefault("start", a)
        slide.setdefault("path", f'{slide["file"]}:{a}–{b}')
    if slide.get("kind") == "image" and not str(slide.get("image", "")).startswith("data:"):
        path = (base / slide["image"]).resolve()
        mime = mimetypes.guess_type(path.name)[0] or "image/png"
        slide["image"] = f"data:{mime};base64,{base64.b64encode(path.read_bytes()).decode()}"
    return slide


PALETTE = ["#f5b94a", "#6ea8ff", "#4fd1a5", "#f07ca8", "#b49cff", "#9aa6c6"]


def _fmt(v: float, unit: str = "") -> str:
    text = f"{v:,.0f}" if abs(v) >= 100 else f"{v:,.2f}".rstrip("0").rstrip(".")
    return f"{text}{unit}"


def _nice_range(lo: float, hi: float, ticks: int = 4) -> tuple[float, float, float]:
    """Round axis bounds and step (1, 2, 2.5 or 5 × 10^k)."""
    import math
    raw = (hi - lo) / ticks or 1.0
    mag = 10 ** math.floor(math.log10(raw))
    step = next(m * mag for m in (1, 2, 2.5, 5, 10) if m * mag >= raw)
    return math.floor(lo / step) * step, math.ceil(hi / step) * step, step


def chart_svg(c: dict, w: int = 1120, h: int = 470) -> str:
    """A small, dependency-free SVG chart (bar, hbar or line) in the slide theme."""
    e = html.escape
    labels, series, unit = [str(x) for x in c["labels"]], c["series"], c.get("unit", "")
    kind = c.get("type", "bar")
    values = [float(v) for s in series for v in s["values"]]
    lo, hi, step = _nice_range(min(0.0, min(values)), max(0.0, max(values)) or 1.0)
    legend = ""
    if len(series) > 1:
        legend = "".join(f'<tspan fill="{PALETTE[i % 6]}">■ </tspan><tspan fill="#c4cde3">{e(str(s.get("name", "")))}   </tspan>'
                         for i, s in enumerate(series))
        legend = f'<text x="{w}" y="18" text-anchor="end" font-size="20">{legend}</text>'
    parts = [f'<svg viewBox="0 0 {w} {h}" width="{w}" height="{h}" font-family="Inter, DejaVu Sans, sans-serif">', legend]
    if kind == "hbar":
        left, top, right = 260, 40 if len(series) > 1 else 10, w - 120
        row = (h - top) / len(labels)
        bh = row * 0.7 / len(series)
        x0 = left + (0 - lo) / (hi - lo) * (right - left)
        for i, lab in enumerate(labels):
            y = top + i * row
            parts.append(f'<text x="{left - 16}" y="{y + row * 0.5 + 7}" text-anchor="end" font-size="22" fill="#dbe2f2">{e(lab)}</text>')
            for j, s in enumerate(series):
                v = float(s["values"][i])
                x1 = left + (v - lo) / (hi - lo) * (right - left)
                yy = y + row * 0.15 + j * bh
                parts.append(f'<rect x="{min(x0, x1):.1f}" y="{yy:.1f}" width="{abs(x1 - x0):.1f}" height="{bh - 4:.1f}" rx="5" fill="{PALETTE[j % 6]}"/>')
                parts.append(f'<text x="{max(x0, x1) + 10:.1f}" y="{yy + bh / 2 + 6:.1f}" font-size="20" fill="#e8ecf6">{e(_fmt(v, unit))}</text>')
    else:
        left, top, bottom, right = 90, 60 if len(series) > 1 else 30, h - 50, w - 20
        y = lambda v: bottom - (v - lo) / (hi - lo) * (bottom - top)  # noqa: E731
        for k in range(round((hi - lo) / step) + 1):  # grid
            gv = lo + step * k
            parts.append(f'<line x1="{left}" x2="{right}" y1="{y(gv):.1f}" y2="{y(gv):.1f}" stroke="#2b3a63" stroke-width="1"/>')
            parts.append(f'<text x="{left - 12}" y="{y(gv) + 6:.1f}" text-anchor="end" font-size="18" fill="#8fa3d6">{e(_fmt(gv, unit))}</text>')
        col = (right - left) / len(labels)
        for i, lab in enumerate(labels):
            parts.append(f'<text x="{left + col * (i + .5):.1f}" y="{bottom + 32}" text-anchor="middle" font-size="20" fill="#dbe2f2">{e(lab)}</text>')
        for j, s in enumerate(series):
            vals = [float(v) for v in s["values"]]
            if kind == "line":
                pts = " ".join(f"{left + col * (i + .5):.1f},{y(v):.1f}" for i, v in enumerate(vals))
                parts.append(f'<polyline points="{pts}" fill="none" stroke="{PALETTE[j % 6]}" stroke-width="4" stroke-linejoin="round"/>')
                for i, v in enumerate(vals):
                    parts.append(f'<circle cx="{left + col * (i + .5):.1f}" cy="{y(v):.1f}" r="6" fill="{PALETTE[j % 6]}"/>')
                if len(series) == 1 and len(vals) <= 12:
                    parts += [f'<text x="{left + col * (i + .5):.1f}" y="{y(v) - 14:.1f}" text-anchor="middle" font-size="18" fill="#e8ecf6">{e(_fmt(v, unit))}</text>'
                              for i, v in enumerate(vals)]
            else:
                bw = col * 0.72 / len(series)
                for i, v in enumerate(vals):
                    x = left + col * i + col * 0.14 + j * bw
                    parts.append(f'<rect x="{x:.1f}" y="{min(y(v), y(0)):.1f}" width="{bw - 4:.1f}" height="{abs(y(0) - y(v)):.1f}" rx="5" fill="{PALETTE[j % 6]}"/>')
                    if len(labels) * len(series) <= 16:
                        parts.append(f'<text x="{x + bw / 2 - 2:.1f}" y="{min(y(v), y(0)) - 10:.1f}" text-anchor="middle" font-size="18" fill="#e8ecf6">{e(_fmt(v, unit))}</text>')
    parts.append("</svg>")
    return "".join(parts)


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
