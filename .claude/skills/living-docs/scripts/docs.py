"""Regenerate how-to guides (markdown + screenshots) from guide scripts, and catch drift.

    python3 docs.py guides/*.yaml --base http://localhost:8000 --out docs/guides
                    [--fail-on-change] [--threshold 0.985] [--full-page-only]

A guide is a demo-video YAML (steps with actions, highlight, wait, expect) plus a
`guide:` block (title, intro, audience, prerequisites). Each step's `doc:` (or its
`say:`) becomes the instruction text. For every guide this:

  1. replays it headless through the demo-video engine (dry run: no TTS, no video;
     caption bar hidden so screenshots look like the product, highlight kept);
  2. writes OUT/<slug>.md — numbered steps, each with its text and a screenshot
     (the highlighted element with context, or the full page with `image: page`);
  3. compares every new screenshot with the previous one (SSIM via ffmpeg) and lists
     what changed in OUT/<slug>.changes.md — review those like a visual diff;
  4. writes OUT/README.md, an index of all guides.

Exit status: 1 if any guide is out of date (a selector no longer matches, an action
fails, a wait times out or an expect: check fails) — so it can run nightly in CI like
a test; with --fail-on-change also 1 when screenshots changed beyond --threshold.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent


def engine() -> Path:
    for cand in (os.getenv("DEMO_VIDEO_SKILL"), HERE.parents[1] / "demo-video",
                 Path.home() / ".claude" / "skills" / "demo-video"):
        if cand and (Path(cand) / "scripts" / "build.py").exists():
            return Path(cand)
    sys.exit("demo-video skill not found: install it next to living-docs or set DEMO_VIDEO_SKILL")


def yaml_load(path: Path) -> dict:
    venv = Path(os.getenv("DEMO_VIDEO_CACHE", Path.home() / ".cache" / "demo-video")) / "venv" / "bin" / "python"
    try:
        import yaml
        return yaml.safe_load(path.read_text())
    except ImportError:
        if not venv.exists():
            subprocess.run([sys.executable, str(engine() / "scripts" / "setup.py")], check=True, capture_output=True)
        out = subprocess.run([str(venv), "-c", "import json,sys,yaml;print(json.dumps(yaml.safe_load(open(sys.argv[1]))))",
                              str(path)], capture_output=True, text=True, check=True).stdout
        return json.loads(out)


def ssim(a: Path, b: Path) -> float:
    """Structural similarity of two PNGs (1.0 = identical); 0 if sizes differ or ffmpeg fails."""
    res = subprocess.run(["ffmpeg", "-v", "info", "-nostats", "-i", str(a), "-i", str(b), "-lavfi", "ssim", "-f", "null", "-"],
                         capture_output=True, text=True)
    m = re.search(r"All:([\d.]+)", res.stderr)
    return float(m.group(1)) if m else 0.0


def build_guide(path: Path, base: str, out: Path, threshold: float, page_only: bool) -> dict:
    spec = yaml_load(path)
    slug = spec.get("slug", path.stem)
    guide = spec.get("guide") or {}
    work = Path(tempfile.mkdtemp(prefix=f"docs-{slug}-"))
    env = {**os.environ, "DEMO_VIDEO_NO_CAPTION": "1", "DEMO_MAX_WAIT": str(guide.get("max_wait", 60))}
    res = subprocess.run([sys.executable, str(engine() / "scripts" / "build.py"), str(path), "--base", base,
                          "--out", str(work), "--dry-run"], capture_output=True, text=True, env=env)
    steps_json = work / slug / "shots" / "steps.json"
    if not steps_json.exists():
        print(res.stdout[-1500:], res.stderr[-1500:], file=sys.stderr)
        return {"slug": slug, "title": guide.get("title", slug), "problems": ["engine failed"], "changed": [], "steps": 0}
    data = json.loads(steps_json.read_text())

    img_dir = out / "img" / slug
    img_dir.mkdir(parents=True, exist_ok=True)
    changed, lines = [], []
    title = guide.get("title") or spec.get("concept", [{}])[0].get("title") or slug
    lines += [f"# {title}", ""]
    if guide.get("intro"):
        lines += [guide["intro"].strip(), ""]
    meta = []
    if guide.get("audience"):
        meta.append(f"**For:** {guide['audience']}")
    if guide.get("prerequisites"):
        meta.append("**Before you start:** " + "; ".join(guide["prerequisites"]))
    if meta:
        lines += ["  \n".join(meta), ""]
    for spec_step, st in zip(spec.get("steps") or [], data["steps"]):
        mode = spec_step.get("image", "element")
        src = Path(st["element_png"]) if mode == "element" and st.get("element_png") and not page_only else Path(st["png"])
        target = img_dir / f"step{st['step']:02d}.png"
        if mode != "none":
            if not target.exists():
                shutil.copy(src, target)
            else:
                score = ssim(src, target)
                if score < threshold:  # a real change: keep the old one next to it for review
                    changed.append({"step": st["step"], "caption": st["caption"], "ssim": round(score, 4),
                                    "old": str(target.with_suffix(".prev.png"))})
                    shutil.copy(target, target.with_suffix(".prev.png"))
                    shutil.copy(src, target)
                # else: visually the same — keep the committed file, so re-runs don't churn git
        text = (spec_step.get("doc") or st["text"]).strip()
        lines += [f"## {st['step']}. {st['caption'] or text.split('.')[0]}", "", text, ""]
        if mode != "none":
            lines += [f"![{st['caption']}](img/{slug}/{target.name})", ""]
    stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d")
    status = "✅ verified" if not data["problems"] else "⚠️ OUT OF DATE: " + "; ".join(data["problems"])
    lines += ["---", f"_{status} against {base} on {stamp} — generated by living-docs from `{path.name}`._", ""]
    (out / f"{slug}.md").write_text("\n".join(lines))
    if changed:
        md = [f"# Screenshot changes: {title}", "", f"SSIM below {threshold} (1.0 = identical):", ""]
        md += [f"- step {c['step']} ({c['caption']}): SSIM {c['ssim']} — new `img/{slug}/step{c['step']:02d}.png`, "
               f"previous `{Path(c['old']).name}`" for c in changed]
        (out / f"{slug}.changes.md").write_text("\n".join(md) + "\n")
    else:
        (out / f"{slug}.changes.md").unlink(missing_ok=True)
        for prev in img_dir.glob("*.prev.png"):
            prev.unlink()
    shutil.rmtree(work, ignore_errors=True)
    return {"slug": slug, "title": title, "problems": data["problems"], "changed": changed,
            "steps": len(data["steps"]), "console": data.get("console", [])}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("guides", nargs="+", type=Path)
    ap.add_argument("--base", required=True)
    ap.add_argument("--out", type=Path, default=Path("docs/guides"))
    ap.add_argument("--threshold", type=float, default=0.985)
    ap.add_argument("--fail-on-change", action="store_true")
    ap.add_argument("--full-page-only", action="store_true", help="always use full-page screenshots")
    args = ap.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    results = []
    for g in args.guides:
        r = build_guide(g, args.base.rstrip("/"), args.out, args.threshold, args.full_page_only)
        results.append(r)
        flag = "OUT OF DATE" if r["problems"] else "changed" if r["changed"] else "ok"
        print(f"{flag:12} {r['slug']}: {r['steps']} steps → {args.out / (r['slug'] + '.md')}")
        for p in r["problems"]:
            print(f"    ! {p}")
        for c in r["changed"]:
            print(f"    ~ step {c['step']} ({c['caption']}) SSIM {c['ssim']}")
    index = ["# Guides", "", "| Guide | Steps | Status |", "|---|---|---|"]
    index += [f"| [{r['title']}]({r['slug']}.md) | {r['steps']} | "
              f"{'⚠️ out of date' if r['problems'] else '🔄 screenshots changed' if r['changed'] else '✅'} |"
              for r in results]
    (args.out / "README.md").write_text("\n".join(index) + "\n")
    broken = any(r["problems"] for r in results)
    return 1 if broken or (args.fail_on_change and any(r["changed"] for r in results)) else 0


if __name__ == "__main__":
    sys.exit(main())
