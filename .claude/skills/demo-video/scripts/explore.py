"""Explore a running web app so a demo script can be drafted from what's really there.

    python3 explore.py http://localhost:8000/router/ --out ./explore
        [--do 'click:#run' --do 'wait:#result' --do 'fill:#q=hello' --do 'shot:after-run' ...]
        [--max-wait 120]

For the page (and again after each `shot:`), writes into --out:
    NN-<name>.png      full-page screenshot (view it to see the layout)
    NN-<name>.md       an inventory: headings, inputs (with options), buttons,
                       checkboxes, sample/chip lists, every element with an id,
                       and a text outline — with ready-to-use CSS selectors.

--do steps run in order: click:SEL  fill:SEL=TEXT  check:SEL  select:SEL=VALUE
wait:SEL (or wait:js:EXPR)  goto:URL  pause:SECONDS  shot:NAME. Use them to reach
result states (run something, wait, inventory again) before writing `steps:`.
"""

from __future__ import annotations

import argparse
import re
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from setup import ensure_env  # noqa: E402

ensure_env()

from playwright.sync_api import sync_playwright  # noqa: E402

from common import H, W, launch_browser  # noqa: E402

INVENTORY_JS = r"""() => {
  const vis = e => e.getClientRects().length > 0 && getComputedStyle(e).visibility !== 'hidden';
  const txt = (e, n = 90) => (e.innerText || e.value || '').replace(/\s+/g, ' ').trim().slice(0, n);
  const sel = e => {
    if (e.id) return '#' + CSS.escape(e.id);
    if (e.name) return `${e.tagName.toLowerCase()}[name="${e.name}"]`;
    const cls = [...e.classList].filter(c => !/^(vid-|hidden$)/.test(c)).slice(0, 2);
    let s = e.tagName.toLowerCase() + cls.map(c => '.' + CSS.escape(c)).join('');
    const p = e.parentElement && e.parentElement.closest('[id]');
    if (p && p !== e) s = '#' + CSS.escape(p.id) + ' ' + s;
    const all = [...document.querySelectorAll(s)];
    return all.length > 1 ? `${s}  (nth ${all.indexOf(e)} of ${all.length})` : s;
  };
  const pick = q => [...document.querySelectorAll(q)];
  return {
    title: document.title, url: location.href,
    headings: pick('h1,h2,h3').filter(vis).map(e => ({tag: e.tagName, text: txt(e), sel: sel(e)})),
    inputs: pick('input:not([type=checkbox]):not([type=radio]):not([type=hidden]),textarea,select').map(e => ({
      sel: sel(e), type: e.type || e.tagName.toLowerCase(), visible: vis(e),
      label: (e.labels && e.labels[0] ? txt(e.labels[0], 60) : '') || e.placeholder || '',
      value: (e.value || '').slice(0, 80),
      options: e.tagName === 'SELECT' ? [...e.options].map(o => o.value + (o.selected ? ' (selected)' : '')) : undefined })),
    toggles: pick('input[type=checkbox],input[type=radio]').map(e => ({
      sel: sel(e), checked: e.checked, value: e.value, visible: vis(e),
      label: e.labels && e.labels[0] ? txt(e.labels[0], 60) : '' })),
    buttons: pick('button,[role=button],a.button,input[type=submit]').filter(vis).map(e => ({
      sel: sel(e), text: txt(e, 60), disabled: !!e.disabled })),
    lists: pick('[id]').filter(e => vis(e) && e.children.length >= 3 &&
        [...e.children].every(c => c.tagName === e.children[0].tagName) && e.children.length <= 40)
      .map(e => ({sel: '#' + CSS.escape(e.id) + ' > *', count: e.children.length,
                  items: [...e.children].slice(0, 12).map(c => txt(c, 70))})),
    ids: pick('[id]').map(e => ({sel: '#' + CSS.escape(e.id), tag: e.tagName.toLowerCase(), visible: vis(e),
                                classes: [...e.classList].join(' '), text: txt(e, 70)})),
  };
}"""


def to_md(inv: dict) -> str:
    out = [f"# {inv['title']}", f"URL: {inv['url']}", ""]

    def section(name: str, rows: list[str]) -> None:
        if rows:
            out.extend([f"## {name}", *rows, ""])

    section("Headings", [f"- {h['tag']} `{h['sel']}` — {h['text']}" for h in inv["headings"]])
    section("Inputs", [f"- `{i['sel']}` {i['type']}{'' if i['visible'] else ' (hidden)'} — {i['label']!r}"
                       + (f" value={i['value']!r}" if i["value"] else "")
                       + (f" options={i['options']}" if i.get("options") else "") for i in inv["inputs"]])
    section("Checkboxes / radios", [f"- `{t['sel']}` value={t['value']!r} checked={t['checked']}"
                                    f"{'' if t['visible'] else ' (hidden)'} — {t['label']}" for t in inv["toggles"]])
    section("Buttons", [f"- `{b['sel']}` — {b['text']!r}{' (disabled)' if b['disabled'] else ''}" for b in inv["buttons"]])
    section("Repeated items (samples, chips, rows) — click with {click: {selector, nth}}",
            [f"- `{l['sel']}` ×{l['count']}: " + " | ".join(l["items"]) for l in inv["lists"]])
    section("Elements with ids", [f"- `{e['sel']}` <{e['tag']}>{' .' + e['classes'] if e['classes'] else ''}"
                                  f"{'' if e['visible'] else ' (hidden)'} — {e['text']}" for e in inv["ids"]])
    return "\n".join(out)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("url")
    ap.add_argument("--out", type=Path, default=Path("explore"))
    ap.add_argument("--do", action="append", default=[], help="step, e.g. click:#run (repeatable)")
    ap.add_argument("--max-wait", type=float, default=120)
    args = ap.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    n = 0

    with sync_playwright() as p:
        browser = launch_browser(p)
        page = browser.new_page(viewport={"width": W, "height": H})

        def snapshot(name: str) -> None:
            nonlocal n
            n += 1
            stem = args.out / f"{n:02d}-{re.sub(r'[^\w-]+', '-', name).strip('-') or 'page'}"
            page.screenshot(path=f"{stem}.png", full_page=True)
            Path(f"{stem}.md").write_text(to_md(page.evaluate(INVENTORY_JS)))
            print(f"  {stem}.png  {stem}.md")

        page.goto(args.url)
        page.wait_for_load_state("networkidle")
        snapshot("start")
        for step in args.do:
            verb, _, arg = step.partition(":")
            try:
                if verb == "click":
                    page.locator(arg).first.click(timeout=10_000)
                elif verb == "fill":
                    sel, _, text = arg.partition("=")
                    page.locator(sel).first.fill(text, timeout=10_000)
                elif verb == "check":
                    page.locator(arg).first.check(timeout=10_000)
                elif verb == "select":
                    sel, _, value = arg.partition("=")
                    page.locator(sel).first.select_option(value, timeout=10_000)
                elif verb == "goto":
                    page.goto(arg)
                    page.wait_for_load_state("networkidle")
                elif verb == "pause":
                    page.wait_for_timeout(float(arg) * 1000)
                elif verb == "wait":
                    deadline = time.monotonic() + args.max_wait
                    while time.monotonic() < deadline:
                        ok = (page.evaluate(arg[3:]) if arg.startswith("js:")
                              else page.locator(arg).count() > 0 and page.locator(arg).first.is_visible())
                        if ok:
                            break
                        page.wait_for_timeout(300)
                    else:
                        print(f"  ! wait timed out: {arg}")
                elif verb == "shot":
                    snapshot(arg)
                else:
                    print(f"  ! unknown step {step!r}")
            except Exception as exc:  # noqa: BLE001
                print(f"  ! {step}: {str(exc).splitlines()[0]}")
        if args.do and not args.do[-1].startswith("shot:"):
            snapshot("end")
        browser.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
