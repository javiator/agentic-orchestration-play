"""Building blocks for the learning videos: narration, slides, demo recording
and assembly. build.py wires them together per exercise.

    narration   Piper neural TTS (voice downloaded once into .cache/voices),
                cached per sentence so re-builds don't re-synthesize.
    slides      HTML rendered by Playwright and screenshotted (1280×800).
    demo        Playwright screen recording of the real page. Each step shows
                a caption bar, highlights an element, plays its narration, and
                optionally waits for the app; long waits are FAST-FORWARDED in
                the final cut (no narration plays during them).
    assembly    ffmpeg: slide clips + demo clip → one MP4 at 1152×720 with the
                narration as burned-in subtitles.
"""

from __future__ import annotations

import hashlib
import html
import json
import os
import re
import subprocess
import time
import urllib.request
import wave
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

HERE = Path(__file__).resolve().parent
CACHE = HERE / ".cache"
OUT = HERE / "out"
W, H = 1280, 800          # capture size (slides + browser viewport)
OUT_W, OUT_H = 1152, 720  # final video size (same 16:10 aspect)
FPS = 25

VOICE = "en_US-lessac-medium"
VOICE_URL = "https://huggingface.co/rhasspy/piper-voices/resolve/main/en/en_US/lessac/medium/"


def run(cmd: list[str]) -> None:
    res = subprocess.run(cmd, capture_output=True, text=True)
    if res.returncode:
        raise RuntimeError(f"{cmd[0]} failed:\n{res.stderr[-2000:]}")


def media_duration(path: Path) -> float:
    out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)],
                         capture_output=True, text=True).stdout
    return float(out.strip())


# ── narration ────────────────────────────────────────────────────────────────

class Narrator:
    def __init__(self, length_scale: float = 1.05):
        from piper import PiperVoice, SynthesisConfig

        voices = CACHE / "voices"
        voices.mkdir(parents=True, exist_ok=True)
        for suffix in (".onnx", ".onnx.json"):
            target = voices / f"{VOICE}{suffix}"
            if not target.exists():
                urllib.request.urlretrieve(VOICE_URL + target.name, target)
        self.voice = PiperVoice.load(str(voices / f"{VOICE}.onnx"))
        self.config = SynthesisConfig(length_scale=length_scale)
        (CACHE / "tts").mkdir(parents=True, exist_ok=True)

    def say(self, text: str) -> tuple[Path, float]:
        """Text → (wav path, seconds). Cached by content."""
        text = re.sub(r"(^|\s)-(\d)", r"\1minus \2", text)  # Piper reads "-43%" as "43%"
        key = hashlib.sha1(f"{VOICE}|{self.config.length_scale}|{text}".encode()).hexdigest()[:16]
        path = CACHE / "tts" / f"{key}.wav"
        if not path.exists():
            with wave.open(str(path), "wb") as wf:
                self.voice.synthesize_wav(text, wf, syn_config=self.config)
        with wave.open(str(path)) as wf:
            return path, wf.getnframes() / wf.getframerate()


# ── slides ───────────────────────────────────────────────────────────────────

SLIDE_CSS = """
* { box-sizing: border-box; margin: 0; }
body { width: 1280px; height: 800px; overflow: hidden; font-family: Inter, 'DejaVu Sans', sans-serif;
       background: radial-gradient(circle at 15% 10%, #1e2a4a 0, #0d1324 55%); color: #e8ecf6;
       padding: 64px 80px; display: flex; flex-direction: column; }
.kicker { color: #8fa3d6; font-size: 22px; letter-spacing: .08em; text-transform: uppercase; font-weight: 600; }
h1 { font-size: 64px; line-height: 1.1; font-weight: 800; margin-top: 18px; }
h2 { font-size: 44px; font-weight: 750; margin: 10px 0 34px; }
.lead { font-size: 30px; line-height: 1.45; color: #c4cde3; margin-top: 30px; max-width: 1000px; }
.cols { display: grid; align-items: start; grid-template-columns: repeat(var(--n), 1fr); gap: 28px; flex: 1; }
.box { background: #16203a; border: 1px solid #2b3a63; border-radius: 18px; padding: 28px 30px; }
.box h3 { font-size: 22px; text-transform: uppercase; letter-spacing: .06em; color: #f5b94a; margin-bottom: 16px; }
.box ul { padding-left: 24px; } .box li { font-size: 25px; line-height: 1.4; margin-bottom: 12px; color: #dbe2f2; }
pre { font-family: 'DejaVu Sans Mono', monospace; color: #dbe7ff; background: #0a1020; border: 1px solid #2b3a63;
      border-radius: 16px; padding: 26px 30px; line-height: 1.32; flex: 1; overflow: hidden; white-space: pre; }
.take { display: flex; gap: 26px; align-items: flex-start; background: #16203a; border: 1px solid #2b3a63;
        border-radius: 18px; padding: 26px 30px; margin-bottom: 22px; }
.take .n { flex: none; width: 54px; height: 54px; border-radius: 50%; background: #f5b94a; color: #0d1324;
           font-size: 28px; font-weight: 800; display: grid; place-items: center; }
.take p { font-size: 30px; line-height: 1.35; padding-top: 6px; }
.foot { margin-top: auto; color: #6f7fa8; font-size: 20px; }
.chips { display: flex; flex-wrap: wrap; gap: 12px; margin-top: 34px; }
.chip { border: 1px solid #3a4c80; border-radius: 999px; padding: 8px 18px; font-size: 20px; color: #b9c6ea; }
"""


def slide_html(slide: dict, kicker: str) -> str:
    e = html.escape
    kind = slide["kind"]
    body = f'<div class="kicker">{e(kicker)}</div>'
    if kind == "title":
        body += f'<h1>{e(slide["title"])}</h1><p class="lead">{e(slide.get("lead", ""))}</p>'
        if slide.get("chips"):
            body += '<div class="chips">' + "".join(f'<span class="chip">{e(c)}</span>' for c in slide["chips"]) + "</div>"
    elif kind == "boxes":
        boxes = slide["boxes"]
        body += f'<h2>{e(slide["title"])}</h2><div class="cols" style="--n:{len(boxes)}">'
        for head, items in boxes:
            body += f'<div class="box"><h3>{e(head)}</h3><ul>' + "".join(f"<li>{e(i)}</li>" for i in items) + "</ul></div>"
        body += "</div>"
    elif kind == "diagram":
        body += f'<h2>{e(slide["title"])}</h2><pre id="d">{e(slide["diagram"])}</pre>'
    elif kind == "takeaways":
        body += f'<h2>{e(slide.get("title", "Takeaways"))}</h2>'
        body += "".join(f'<div class="take"><div class="n">{i}</div><p>{e(t)}</p></div>'
                        for i, t in enumerate(slide["items"], 1))
    if slide.get("foot"):
        body += f'<div class="foot">{e(slide["foot"])}</div>'
    return f"<!doctype html><html><head><meta charset='utf-8'><style>{SLIDE_CSS}</style></head><body>{body}</body></html>"


# Shrink the diagram font until the block fits its box (diagrams vary a lot in size).
FIT_DIAGRAM = """() => { const p = document.getElementById('d'); if (!p) return;
  let s = 26; p.style.fontSize = s + 'px';
  while (s > 11 && (p.scrollWidth > p.clientWidth || p.scrollHeight > p.clientHeight)) { s -= 0.5; p.style.fontSize = s + 'px'; } }"""


def render_slides(browser, slides: list[dict], kicker: str, outdir: Path) -> list[Path]:
    outdir.mkdir(parents=True, exist_ok=True)
    page = browser.new_page(viewport={"width": W, "height": H})
    paths = []
    for i, slide in enumerate(slides):
        page.set_content(slide_html(slide, kicker))
        page.evaluate(FIT_DIAGRAM)
        path = outdir / f"slide{i:02d}.png"
        page.screenshot(path=str(path))
        paths.append(path)
    page.close()
    return paths


# ── demo recording ───────────────────────────────────────────────────────────

OVERLAY_JS = """() => {
  if (document.getElementById('vid-cap')) return;
  const st = document.createElement('style');
  st.textContent = `
    #vid-cap { position: fixed; top: 0; left: 0; right: 0; z-index: 99999; display: flex; align-items: center; gap: 14px;
      padding: 10px 22px; background: rgba(13,19,36,.94); color: #fff; font: 600 20px Inter, sans-serif;
      border-bottom: 3px solid #f5b94a; transition: opacity .3s; }
    #vid-cap .tag { background: #f5b94a; color: #0d1324; border-radius: 6px; padding: 2px 10px; font-size: 15px; font-weight: 800; }
    body { padding-top: 52px !important; }
    .vid-hl { outline: 4px solid #f5b94a !important; outline-offset: 4px; border-radius: 8px;
      animation: vidpulse 1.4s ease-in-out infinite; position: relative; z-index: 5; }
    @keyframes vidpulse { 0%,100% { box-shadow: 0 0 0 0 rgba(245,185,74,.55); } 50% { box-shadow: 0 0 0 14px rgba(245,185,74,0); } }`;
  document.head.appendChild(st);
  const bar = document.createElement('div'); bar.id = 'vid-cap';
  bar.innerHTML = '<span class="tag"></span><span class="txt"></span>';
  document.body.appendChild(bar);
}"""

SET_CAPTION_JS = """([tag, txt]) => { const b = document.getElementById('vid-cap'); if (!b) return;
  b.querySelector('.tag').textContent = tag; b.querySelector('.txt').textContent = txt; }"""

HIGHLIGHT_JS = """(sel) => { document.querySelectorAll('.vid-hl').forEach(e => e.classList.remove('vid-hl'));
  if (!sel) return false; const m = sel.match(/^(first|last):/); if (m) sel = sel.slice(m[0].length);
  let els = [...document.querySelectorAll(sel)].filter(e => e.offsetParent !== null);
  if (m) els = m[1] === 'last' ? els.slice(-1) : els.slice(0, 1);
  if (!els.length) return false; els.forEach(e => e.classList.add('vid-hl'));
  els[0].scrollIntoView({behavior: 'smooth', block: 'center'}); return true; }"""


@dataclass
class Demo:
    """Drives one recorded page. Times are seconds since recording start."""
    page: object
    narrator: Narrator
    base: str
    label: str = "DEMO"
    t0: float = field(default_factory=time.monotonic)
    narration: list[dict] = field(default_factory=list)
    fast: list[tuple[float, float]] = field(default_factory=list)
    log: list[str] = field(default_factory=list)
    shots: Path | None = None  # debug: a screenshot after every step

    def now(self) -> float:
        return time.monotonic() - self.t0

    def goto(self, path: str) -> None:
        self.page.goto(self.base + path)
        self.page.wait_for_load_state("networkidle")
        self.page.evaluate(OVERLAY_JS)
        self.caption("", "")

    def caption(self, tag: str, text: str) -> None:
        self.page.evaluate(SET_CAPTION_JS, [tag, text])

    def highlight(self, sel: str | None) -> None:
        if not self.page.evaluate(HIGHLIGHT_JS, sel) and sel:
            self.log.append(f"highlight not found: {sel}")

    def text(self, sel: str, default: str = "") -> str:
        """Visible text of the first match ("last:<sel>" for the last one)."""
        last = sel.startswith("last:")
        loc = self.page.locator(sel[5:] if last else sel)
        if not loc.count():
            return default
        return " ".join((loc.last if last else loc.first).inner_text().split())

    def wait(self, cond: str | Callable[[], bool], timeout: float = 420, cap: str = "waiting for the models…") -> None:
        """Wait for a selector (visible) or a JS expression to become true.
        Anything longer than a few seconds is fast-forwarded in the final cut."""
        start = self.now()
        self.caption("⏩", cap)
        timeout = min(timeout, float(os.getenv("DEMO_MAX_WAIT", timeout)))
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if callable(cond):
                ok = cond()
            elif cond.startswith("js:"):
                ok = self.page.evaluate(cond[3:])
            else:
                loc = self.page.locator(cond)
                ok = loc.count() > 0 and loc.first.is_visible()
            if ok:
                break
            self.page.wait_for_timeout(250)
        else:
            self.log.append(f"wait timed out: {cond}")
        end = self.now()
        if end - start > 4:
            self.fast.append((start + 1.0, end - 0.6))
        self.page.wait_for_timeout(600)

    def step(self, say: str | Callable[[], str], cap: str = "", hl: str | None = None, *,
             tag: str | None = None, fill: tuple[str, str] | None = None, click: str | None = None,
             check: str | None = None, uncheck: str | None = None, action: Callable[[], None] | None = None,
             wait: str | Callable[[], bool] | None = None, wait_cap: str = "waiting for the models…",
             hold: float = 0.8, timeout: float = 420) -> None:
        """Caption + highlight + narration, with optional actions during the
        narration and an optional wait after it."""
        text = say() if callable(say) else say
        wav, dur = self.narrator.say(text)
        self.caption(tag or self.label, cap)
        self.highlight(hl)
        start = self.now()
        self.narration.append({"t": start, "wav": str(wav), "dur": dur, "text": text})
        self.page.wait_for_timeout(500)
        if fill:
            sel, value = fill
            self.page.locator(sel).fill("")
            self.page.locator(sel).type(value, delay=12 if len(value) < 140 else 0)
        if check:
            self.page.locator(check).check()
        if uncheck:
            self.page.locator(uncheck).uncheck()
        if click:
            self.page.locator(click).first.click()
        if action:
            action()
        remaining = start + dur + 0.35 - self.now()
        if remaining > 0:
            self.page.wait_for_timeout(remaining * 1000)
        if wait:
            self.wait(wait, timeout=timeout, cap=wait_cap)
            self.caption(tag or self.label, cap)
        self.page.wait_for_timeout(hold * 1000)
        if self.shots:
            self.shots.mkdir(parents=True, exist_ok=True)
            self.page.screenshot(path=str(self.shots / f"{len(self.narration):02d}.png"))


def record_demo(browser, narrator: Narrator, base: str, script: Callable[[Demo], None], outdir: Path) -> dict:
    """Run a demo script while recording. Returns the timeline (also saved as JSON)."""
    outdir.mkdir(parents=True, exist_ok=True)
    for old in outdir.glob("*.webm"):
        old.unlink()
    ctx = browser.new_context(viewport={"width": W, "height": H}, record_video_dir=str(outdir),
                              record_video_size={"width": W, "height": H}, device_scale_factor=1)
    page = ctx.new_page()
    shots = os.getenv("DEMO_SHOTS")
    demo = Demo(page=page, narrator=narrator, base=base, shots=Path(shots) / outdir.parent.name if shots else None)
    try:
        script(demo)
    finally:
        elapsed = demo.now()
        video_path = Path(page.video.path())
        ctx.close()
    webm = outdir / "demo.webm"
    video_path.rename(webm)
    # Recording starts slightly before our clock; shift everything by the gap.
    offset = max(0.0, media_duration(webm) - elapsed)
    timeline = {"webm": str(webm), "duration": media_duration(webm), "log": demo.log,
                "narration": [{**n, "t": n["t"] + offset} for n in demo.narration],
                "fast": [(a + offset, b + offset) for a, b in demo.fast]}
    (outdir / "timeline.json").write_text(json.dumps(timeline, indent=1))
    return timeline


# ── assembly ─────────────────────────────────────────────────────────────────

ENC_FAST = ["-c:v", "libx264", "-preset", "veryfast", "-crf", "16", "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-b:a", "128k", "-ar", "48000", "-ac", "2"]


def slide_clip(png: Path, wav: Path | None, seconds: float, out: Path) -> None:
    cmd = ["ffmpeg", "-y", "-loop", "1", "-framerate", str(FPS), "-t", f"{seconds:.3f}", "-i", str(png)]
    if wav:
        cmd += ["-i", str(wav), "-filter_complex", f"[1:a]adelay=400|400,apad[a]", "-map", "0:v", "-map", "[a]"]
    else:
        cmd += ["-f", "lavfi", "-t", f"{seconds:.3f}", "-i", "anullsrc=r=48000:cl=stereo"]
    cmd += ["-vf", f"scale={OUT_W}:{OUT_H}:flags=lanczos,fps={FPS}", "-t", f"{seconds:.3f}", *ENC_FAST, str(out)]
    run(cmd)


def demo_clip(timeline: dict, out: Path, max_ff_seconds: float = 2.5) -> tuple[float, list[dict]]:
    """Cut the recording into normal/fast segments, lay narration on top.
    Returns (clip duration, narration with times mapped into the clip)."""
    dur = timeline["duration"]
    segs, cursor = [], 0.0
    for a, b in sorted(timeline["fast"]):
        if a <= cursor or b - a < 1:
            continue
        segs.append((cursor, a, 1.0))
        segs.append((a, b, max(1.0, (b - a) / max_ff_seconds)))
        cursor = b
    segs.append((cursor, dur, 1.0))

    def mapped(t: float) -> float:
        acc = 0.0
        for a, b, speed in segs:
            if t <= b:
                return acc + (max(t, a) - a) / speed
            acc += (b - a) / speed
        return acc

    total = mapped(dur)
    parts, labels = [], []
    for i, (a, b, speed) in enumerate(segs):
        parts.append(f"[0:v]trim={a:.3f}:{b:.3f},setpts=(PTS-STARTPTS)/{speed:.4f}[v{i}]")
        labels.append(f"[v{i}]")
    graph = ";".join(parts) + f";{''.join(labels)}concat=n={len(segs)}:v=1:a=0,scale={OUT_W}:{OUT_H}:flags=lanczos,fps={FPS}[v]"
    narr = [{**n, "t": mapped(n["t"])} for n in timeline["narration"]]
    inputs = ["-i", timeline["webm"]]
    for n in narr:
        inputs += ["-i", n["wav"]]
    if narr:
        mix = "".join(f"[{i + 1}:a]adelay={int(n['t'] * 1000)}|{int(n['t'] * 1000)}[a{i}];" for i, n in enumerate(narr))
        mix += "".join(f"[a{i}]" for i in range(len(narr))) + f"amix=inputs={len(narr)}:normalize=0,apad[a]"
        graph += ";" + mix
    else:
        inputs += ["-f", "lavfi", "-i", "anullsrc=r=48000:cl=stereo"]
        graph += f";[1:a]anull[a]"
    run(["ffmpeg", "-y", *inputs, "-filter_complex", graph, "-map", "[v]", "-map", "[a]",
         "-t", f"{total:.3f}", *ENC_FAST, str(out)])
    return total, narr


def _chunks(text: str, limit: int = 84) -> list[str]:
    words, lines, cur = text.split(), [], ""
    for w in words:
        if cur and len(cur) + 1 + len(w) > limit:
            lines.append(cur)
            cur = w
        else:
            cur = f"{cur} {w}".strip()
        if re.search(r"[.!?:;]$", w) and len(cur) > limit * 0.55:
            lines.append(cur)
            cur = ""
    if cur:
        lines.append(cur)
    return lines


def _ts(t: float) -> str:
    ms = int(round(t * 1000))
    return f"{ms // 3600000:02d}:{ms // 60000 % 60:02d}:{ms // 1000 % 60:02d},{ms % 1000:03d}"


def write_srt(cues: list[dict], path: Path) -> None:
    """cues: {t, dur, text} — each split into short lines, timed by length."""
    out, n = [], 1
    for cue in cues:
        lines = _chunks(cue["text"])
        total_chars = sum(len(x) for x in lines) or 1
        t = cue["t"]
        for line in lines:
            d = cue["dur"] * len(line) / total_chars
            out.append(f"{n}\n{_ts(t)} --> {_ts(t + d)}\n{line}\n")
            n += 1
            t += d
    path.write_text("\n".join(out))


def concat_final(parts: list[Path], srt: Path, out: Path, crf: int = 28) -> None:
    listing = out.with_suffix(".txt")
    listing.write_text("".join(f"file '{p.resolve()}'\n" for p in parts))
    style = ("FontName=Inter,FontSize=11,PrimaryColour=&H00FFFFFF,BackColour=&H99000000,"
             "BorderStyle=4,Outline=0,Shadow=0,MarginV=14,MarginL=40,MarginR=40")
    run(["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(listing),
         "-vf", f"subtitles={srt}:force_style='{style}'",
         "-c:v", "libx264", "-preset", "slow", "-crf", str(crf), "-pix_fmt", "yuv420p", "-movflags", "+faststart",
         "-c:a", "aac", "-b:a", "96k", "-ac", "1", str(out)])
    listing.unlink()
