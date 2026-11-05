"""Live demo recording: Playwright drives the real page while recording video.

Each step: caption bar + highlighted element + narration, with actions that
happen WHILE the narration plays, and an optional wait for the app after it.
Long waits are marked and FAST-FORWARDED in the final cut (no narration plays
during them). Narration can read the page (templating, see render_say), so it
describes what really happened on this run.

Step fields (YAML):
    say        narration, templated                       (required)
    goto       open this page first (before the narration)
    caption    short caption-bar text (≤ ~6 words)
    tag        caption-bar tag (default: the script's `label`, e.g. DEMO)
    highlight  CSS selector; "first:"/"last:" prefix picks one match.
               Retried after the actions, since results often appear later.
    actions    list, run in order during the narration:
                 goto: /path            fill: [sel, text]       click: sel
                 check: sel             uncheck: sel            select: [sel, value]
                 press: [sel, key]      wheel: 200              pause: 0.5 (seconds)
                 js: "code"             click: {selector, nth, text, optional}
               Selectors take "first:" (default), "last:" or "all:" prefixes
               (all: = every match, for check/uncheck/click).
    wait       selector (visible) or "js:<expr>", or {until, timeout, caption}
    hold       seconds to linger after the step (default 0.8)

Templating in `say` (evaluated when the step starts, before its actions):
    {text:SEL}            visible text of the match (whitespace collapsed; line breaks → ". ")
    {count:SEL}           number of matches
    {plural:SEL|word}     "3 words" / "1 word" from the count
    {js:EXPR}             any JS expression, as text
    {if:SEL}..{else}..{end}    SEL has a visible match (or {if:js:EXPR}); nests
  filters for text/js:  |before:X  |after:X  |max:N (whole sentences)  |num  |default:X  |replace:A=>B  |strip
    e.g. "Scores: {text:#score|num}"   "{text:#savings|before:(}"
"""

from __future__ import annotations

import json
import os
import re
import time
from dataclasses import dataclass, field
from pathlib import Path

from common import H, W, media_duration

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

VISIBLE_JS = """(sel) => [...document.querySelectorAll(sel)].some(e => e.getClientRects().length > 0
  && getComputedStyle(e).visibility !== 'hidden')"""

ACTION_TIMEOUT_MS = 10_000
# First "|" (not part of "||") that starts a filter: |before:X |max:N ...
FILTER_RE = re.compile(r"(?<!\|)\|(?!\|)(?=(?:before|after|max|num|default|replace|strip)\b)")


def _split_prefix(sel: str) -> tuple[str, str]:
    m = re.match(r"^(first|last|all):", sel)
    return (m.group(1), sel[m.end():]) if m else ("first", sel)


@dataclass
class Demo:
    """Drives one recorded page. Times are seconds since recording start."""
    page: object
    narrator: object | None   # None in dry runs: durations are estimated, nothing is synthesized
    base: str
    label: str = "DEMO"
    wait_caption: str = "waiting for the app…"
    t0: float = field(default_factory=time.monotonic)
    narration: list[dict] = field(default_factory=list)
    fast: list[tuple[float, float]] = field(default_factory=list)
    log: list[str] = field(default_factory=list)
    shots: Path | None = None

    def now(self) -> float:
        return time.monotonic() - self.t0

    # ── page helpers ─────────────────────────────────────────────────────────

    def goto(self, path: str) -> None:
        self.page.goto(path if re.match(r"^https?://", path) else self.base + path)
        self.page.wait_for_load_state("networkidle")
        self.page.evaluate(OVERLAY_JS)
        self.caption("", "")

    def caption(self, tag: str, text: str) -> None:
        self.page.evaluate(SET_CAPTION_JS, [tag, text])

    def highlight(self, sel: str | None, log: bool = True) -> bool:
        found = bool(self.page.evaluate(HIGHLIGHT_JS, sel))
        if not found and sel and log:
            self.log.append(f"highlight not found: {sel}")
        return found

    def locs(self, sel: str) -> list:
        which, css = _split_prefix(sel)
        loc = self.page.locator(css)
        if which == "all":
            return loc.all()
        if not loc.count():
            return []
        return [loc.last if which == "last" else loc.first]

    def text(self, sel: str, default: str = "") -> str:
        """Visible text, whitespace collapsed; a line break after text with no
        punctuation becomes ". " so headings and labels read as sentences."""
        found = self.locs(sel)
        if not found:
            return default
        lines = [" ".join(ln.split()) for ln in found[0].inner_text().splitlines()]
        out = ""
        for ln in filter(None, lines):
            out += ("" if not out else " " if re.search(r"[.!?:;,·—–-]$", out) else ". ") + ln
        return out

    def visible(self, sel: str) -> bool:
        if sel.startswith("js:"):
            return bool(self.page.evaluate(sel[3:]))
        return bool(self.page.evaluate(VISIBLE_JS, _split_prefix(sel)[1]))

    # ── narration templating ─────────────────────────────────────────────────

    def render_say(self, template: str) -> str:
        tokens = _tokenize(template)
        out, _ = self._render(tokens, 0, stop=())
        return re.sub(r"\s+", " ", out).strip()

    def _render(self, tokens: list, i: int, stop: tuple) -> tuple[str, int]:
        parts = []
        while i < len(tokens):
            kind, val = tokens[i]
            if kind == "tag" and val in stop:
                return "".join(parts), i
            if kind == "text":
                parts.append(val)
                i += 1
            elif val.startswith("if:"):
                cond = self.visible(val[3:])
                yes, i = self._render(tokens, i + 1, stop=("else", "end"))
                no = ""
                if i < len(tokens) and tokens[i][1] == "else":
                    no, i = self._render(tokens, i + 1, stop=("end",))
                i += 1  # skip {end}
                parts.append(yes if cond else no)
            else:
                parts.append(self._value(val))
                i += 1
        return "".join(parts), i

    def _value(self, expr: str) -> str:
        kind, _, rest = expr.partition(":")
        if kind == "plural":
            sel, _, word = rest.rpartition("|")
            n = len(self.locs("all:" + _split_prefix(sel)[1]))
            return f"{n} {word or 'item'}{'' if n == 1 else 's'}"
        m = FILTER_RE.search(rest)
        body, filters = (rest[:m.start()], rest[m.end():].split("|")) if m else (rest, [])
        if kind == "js":
            try:
                val = self.page.evaluate(body)
            except Exception as exc:  # noqa: BLE001
                self.log.append(f"template js failed: {body[:80]} ({str(exc).splitlines()[0][:120]})")
                val = ""
            return _apply(str("" if val is None else val), filters)
        sel = body
        if kind == "count":
            return str(len(self.locs("all:" + _split_prefix(sel)[1])))
        if kind == "text":
            val = self.text(sel)
            if not val and not any(f.startswith("default") for f in filters):
                self.log.append(f"template text empty: {sel}")
            return _apply(val, filters)
        raise ValueError(f"unknown template field {{{expr}}}")

    # ── actions ──────────────────────────────────────────────────────────────

    def act(self, action: dict) -> None:
        (verb, arg), = action.items()
        try:
            self._act(verb, arg)
        except Exception as exc:  # noqa: BLE001 — log and carry on; the timeline shows it
            self.log.append(f"action failed: {verb} {json.dumps(arg)[:100]} ({str(exc).splitlines()[0][:160]})")

    def _act(self, verb: str, arg) -> None:
        p = self.page
        if verb == "goto":
            self.goto(arg)
        elif verb == "fill":
            sel, value = arg
            (loc,) = self._need(sel)
            loc.fill("", timeout=ACTION_TIMEOUT_MS)
            loc.type(str(value), delay=12 if len(str(value)) < 140 else 0)
        elif verb in ("check", "uncheck"):
            for loc in self._need(arg):
                getattr(loc, verb)(timeout=ACTION_TIMEOUT_MS)
        elif verb == "click":
            spec = arg if isinstance(arg, dict) else {"selector": arg}
            which, css = _split_prefix(spec["selector"])
            loc = p.locator(css)
            if spec.get("text"):
                loc = loc.filter(has_text=spec["text"])
            if not loc.count():
                if spec.get("optional"):
                    return
                raise RuntimeError(f"no element matches {spec['selector']}")
            targets = (loc.all() if which == "all" else [loc.nth(spec["nth"]) if "nth" in spec
                                                         else loc.last if which == "last" else loc.first])
            for t in targets:
                t.click(timeout=ACTION_TIMEOUT_MS)
        elif verb == "select":
            sel, value = arg
            (loc,) = self._need(sel)
            loc.select_option(value, timeout=ACTION_TIMEOUT_MS)
        elif verb == "press":
            sel, key = arg
            (loc,) = self._need(sel)
            loc.press(key, timeout=ACTION_TIMEOUT_MS)
        elif verb == "wheel":
            p.mouse.wheel(0, float(arg))
        elif verb == "pause":
            p.wait_for_timeout(float(arg) * 1000)
        elif verb == "js":
            p.evaluate(arg)
        else:
            raise ValueError(f"unknown action: {verb}")

    def _need(self, sel: str) -> list:
        found = self.locs(sel)
        if not found:
            raise RuntimeError(f"no element matches {sel}")
        return found

    # ── waits and steps ──────────────────────────────────────────────────────

    def wait(self, cond: str, timeout: float = 420, cap: str | None = None) -> None:
        """Wait for a selector (visible) or a JS expression to become true.
        Anything longer than a few seconds is fast-forwarded in the final cut."""
        start = self.now()
        self.caption("⏩", cap or self.wait_caption)
        timeout = min(timeout, float(os.getenv("DEMO_MAX_WAIT", timeout)))
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                if cond.startswith("js:"):
                    ok = self.page.evaluate(cond[3:])
                else:
                    loc = self.page.locator(_split_prefix(cond)[1])
                    ok = loc.count() > 0 and loc.first.is_visible()
            except Exception:  # noqa: BLE001 — page mid-navigation; try again
                ok = False
            if ok:
                break
            self.page.wait_for_timeout(250)
        else:
            self.log.append(f"wait timed out ({timeout:.0f}s): {cond}")
        end = self.now()
        if end - start > 4:
            self.fast.append((start + 1.0, end - 0.6))
        self.page.wait_for_timeout(600)

    def step(self, spec: dict, index: int = 0) -> None:
        """Caption + highlight + narration, with actions during the narration
        and an optional wait after it."""
        if spec.get("goto"):
            self.goto(spec["goto"])
        text = self.render_say(spec["say"])
        if self.narrator:
            synth_start = self.now()
            wav, dur = self.narrator.say(text)
            if self.now() - synth_start > 1.2:  # uncached TTS: cut the dead air
                self.fast.append((synth_start + 0.2, self.now(), 0.2))  # ≈ cut out
        else:  # dry run: no TTS, ~2.6 words/s estimate, don't wait it out
            wav, dur = None, len(text.split()) / 2.6
        tag = spec.get("tag") or self.label
        cap = spec.get("caption", "")
        hl = spec.get("highlight")
        self.caption(tag, cap)
        found = self.highlight(hl, log=False)
        start = self.now()
        self.narration.append({"t": start, "wav": str(wav) if wav else None, "dur": dur, "text": text,
                               "step": index, "caption": cap})
        self.page.wait_for_timeout(500)
        for action in spec.get("actions") or []:
            self.act(action)
        if hl and not found:  # the element may only appear after the action
            self.page.wait_for_timeout(400)
            self.highlight(hl)
        remaining = start + dur + 0.35 - self.now()
        if remaining > 0 and self.narrator:
            self.page.wait_for_timeout(remaining * 1000)
        wait = spec.get("wait")
        if wait:
            w = wait if isinstance(wait, dict) else {"until": wait}
            self.wait(w["until"], timeout=float(w.get("timeout", 420)), cap=w.get("caption"))
            self.caption(tag, cap)
        self.page.wait_for_timeout(float(spec.get("hold", 0.8)) * 1000)
        if self.shots:
            self.shots.mkdir(parents=True, exist_ok=True)
            self.page.screenshot(path=str(self.shots / f"step{index:02d}.png"))


def _tokenize(template: str) -> list[tuple[str, str]]:
    """Split into ("text", str) and ("tag", inner) tokens; braces nest inside a tag
    (so JS like {js:[..].some(x => {..})} works). Literal braces: {{ and }}."""
    tokens, buf, i = [], "", 0
    while i < len(template):
        ch = template[i]
        if template.startswith("{{", i) or template.startswith("}}", i):
            buf += ch
            i += 2
        elif ch == "{":
            depth, j = 1, i + 1
            while j < len(template) and depth:
                depth += {"{": 1, "}": -1}.get(template[j], 0)
                j += 1
            if depth:
                raise ValueError(f"unbalanced '{{' in: {template}")
            if buf:
                tokens.append(("text", buf))
                buf = ""
            tokens.append(("tag", template[i + 1:j - 1].strip()))
            i = j
        else:
            buf += ch
            i += 1
    if buf:
        tokens.append(("text", buf))
    return tokens


def _apply(val: str, filters: list[str]) -> str:
    for f in filters:
        name, _, arg = f.partition(":")
        name = name.strip()
        if name == "before":
            val = val.split(arg)[0]
        elif name == "after":
            val = val.split(arg, 1)[1] if arg in val else val
        elif name == "max":  # cut at the last sentence end that fits, else at a word
            n = int(arg)
            if len(val) > n:
                cut = val[:n]
                ends = [m.end() for m in re.finditer(r"[.!?](?=\s)", cut + " ")]
                val = cut[:ends[-1]] if ends and ends[-1] >= n // 3 else cut.rsplit(" ", 1)[0] + "…"
        elif name == "num":
            m = re.search(r"-?\d+(\.\d+)?", val)
            val = m.group(0) if m else (arg or "")
        elif name == "default":
            val = val or arg
        elif name == "replace":
            old, _, new = arg.partition("=>")
            val = val.replace(old, new)
        elif name == "strip":
            val = val.strip(arg or None)
    return val.strip()


def record_demo(browser, narrator, base: str, script: dict, outdir: Path, shots: Path | None = None) -> dict:
    """Run a YAML demo script while recording. Returns the timeline (also saved as JSON)."""
    outdir.mkdir(parents=True, exist_ok=True)
    for old in outdir.glob("*.webm"):
        old.unlink()
    if narrator:  # synthesize fixed lines now, so the recording doesn't wait for TTS
        for step in script.get("steps") or []:
            if "{" not in step["say"]:
                narrator.say(re.sub(r"\s+", " ", step["say"]).strip())
    ctx = browser.new_context(viewport={"width": W, "height": H}, record_video_dir=str(outdir),
                              record_video_size={"width": W, "height": H}, device_scale_factor=1)
    page = ctx.new_page()
    defaults = script.get("defaults") or {}
    demo = Demo(page=page, narrator=narrator, base=base.rstrip("/"), shots=shots,
                label=defaults.get("tag", "DEMO"), wait_caption=defaults.get("wait_caption", "waiting for the app…"))
    try:
        if script.get("start"):
            demo.goto(script["start"])
        for i, step in enumerate(script.get("steps") or [], 1):
            demo.step({**{k: v for k, v in defaults.items() if k == "hold"}, **step}, i)
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
                "fast": [(f[0] + offset, f[1] + offset, *f[2:]) for f in demo.fast]}
    (outdir / "recording.json").write_text(json.dumps(timeline, indent=1, ensure_ascii=False))
    return timeline
