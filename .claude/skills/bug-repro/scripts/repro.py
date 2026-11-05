"""Replay a bug reproduction script and report whether the bug happens.

    python3 repro.py REPRO.yaml --base http://localhost:8000 [--out DIR] [--video]
                     [--also http://localhost:8001]   # e.g. the fix branch, for before/after
                     [--runs 3]                       # replay N times: same verdict every time?

REPRO.yaml is a demo-video script whose `expect:` checks describe the CORRECT
behaviour, plus a `bug:` block (title, issue, expected, actual). It runs headless
through the demo-video engine's dry run (screenshots per step, console errors and
failed requests captured, no TTS) and decides:

    REPRODUCED      an expect: check failed               exit 1
    NOT REPRODUCED  every check passed (bug fixed / absent)  exit 0
    INCONCLUSIVE    the script itself broke (a selector or action failed, a wait
                    timed out) — fix the script before trusting either answer   exit 2

Writes DIR/<slug>/report.md (verdict, failed checks, console errors, per-step
screenshots). --video also builds the narrated MP4 evidence with the engine.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent


def engine() -> Path:
    for cand in (os.getenv("DEMO_VIDEO_SKILL"), HERE.parents[1] / "demo-video",
                 Path.home() / ".claude" / "skills" / "demo-video"):
        if cand and (Path(cand) / "scripts" / "build.py").exists():
            return Path(cand)
    sys.exit("demo-video skill not found: install it next to bug-repro or set DEMO_VIDEO_SKILL")


def load_yaml(path: Path) -> dict:
    """Read the YAML with the engine's venv (PyYAML lives there, not necessarily here)."""
    try:
        import yaml
        return yaml.safe_load(path.read_text())
    except ImportError:
        venv = Path(os.getenv("DEMO_VIDEO_CACHE", Path.home() / ".cache" / "demo-video")) / "venv" / "bin" / "python"
        if not venv.exists():
            subprocess.run([sys.executable, str(engine() / "scripts" / "setup.py")], check=True, capture_output=True)
        out = subprocess.run([str(venv), "-c", "import json,sys,yaml;print(json.dumps(yaml.safe_load(open(sys.argv[1]))))",
                              str(path)], capture_output=True, text=True, check=True).stdout
        return json.loads(out)


def run_once(yaml_path: Path, spec: dict, base: str, out: Path) -> dict:
    env = {**os.environ, "DEMO_MAX_WAIT": str((spec.get("bug") or {}).get("max_wait", 30))}
    res = subprocess.run([sys.executable, str(engine() / "scripts" / "build.py"), str(yaml_path), "--base", base,
                          "--out", str(out), "--dry-run"], capture_output=True, text=True, env=env)
    steps_json = out / spec.get("slug", yaml_path.stem) / "shots" / "steps.json"
    if not steps_json.exists():
        print(res.stdout[-2000:], res.stderr[-2000:], file=sys.stderr)
        return {"verdict": "INCONCLUSIVE", "failed": [], "broken": ["the engine failed to run the script"],
                "console": [], "steps": [], "base": base}
    data = json.loads(steps_json.read_text())
    failed = [p for p in data["problems"] if p.startswith("expect failed")]
    broken = [p for p in data["problems"] if not p.startswith("expect failed")
              and not p.startswith("template text empty")]
    verdict = "INCONCLUSIVE" if broken else "REPRODUCED" if failed else "NOT REPRODUCED"
    return {"verdict": verdict, "failed": failed, "broken": broken, "console": data["console"],
            "steps": data["steps"], "base": base}


def report(spec: dict, runs: list[dict], path: Path) -> None:
    bug = spec.get("bug") or {}
    md = [f"# Bug reproduction: {bug.get('title', spec.get('slug', ''))}", ""]
    for key in ("issue", "expected", "actual", "environment"):
        if bug.get(key):
            md.append(f"**{key.title()}:** {bug[key]}  ")
    for r in runs:
        md += ["", f"## {r['verdict']} — {r['base']}", ""]
        if r["failed"]:
            md += ["Failed checks (the bug):", ""] + [f"- {p}" for p in r["failed"]] + [""]
        if r["broken"]:
            md += ["Script problems (fix these first):", ""] + [f"- {p}" for p in r["broken"]] + [""]
        if r["console"]:
            md += ["Console errors and failed requests:", "", "```"] + r["console"][:40] + ["```", ""]
        md += ["Steps:", ""] + [f"{s['step']}. {s['text']}  \n   ![step {s['step']}]({s['png']})" for s in r["steps"]]
    md += ["", "Replay: `python3 repro.py <this yaml> --base <url>` — exit 1 = reproduced, 0 = not reproduced, "
           "2 = inconclusive."]
    path.write_text("\n".join(md) + "\n")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("repro", type=Path)
    ap.add_argument("--base", required=True, help="URL of the app with the bug (or the build under test)")
    ap.add_argument("--also", action="append", default=[], help="another URL to replay against (e.g. the fix)")
    ap.add_argument("--out", type=Path, default=Path("repro-out"))
    ap.add_argument("--video", action="store_true", help="also record the narrated evidence video")
    ap.add_argument("--runs", type=int, default=1, help="replay N times against --base to detect flakiness")
    args = ap.parse_args()
    spec = load_yaml(args.repro)
    slug = spec.get("slug", args.repro.stem)
    runs = []
    targets = [args.base] * max(1, args.runs) + args.also
    for i, base in enumerate(targets):
        out = args.out / (f"run{i}" if len(targets) > 1 else "")
        r = run_once(args.repro, spec, base, out)
        runs.append(r)
        print(f"{r['verdict']:15} {base}")
        for p in r["failed"] + r["broken"]:
            print(f"    {p}")
    first = {r["verdict"] for r in runs[:max(1, args.runs)]}
    if len(first) > 1:
        print(f"FLAKY: {args.runs} replays against {args.base} gave {sorted(first)} — stabilise the script "
              "(waits, data) before relying on it")
        runs[0]["verdict"] = "INCONCLUSIVE"
        runs[0]["broken"].append(f"flaky: verdicts {sorted(first)} over {args.runs} runs")
    (args.out / slug).mkdir(parents=True, exist_ok=True)
    report(spec, runs, args.out / slug / "report.md")
    print(f"report: {args.out / slug / 'report.md'}")
    if args.video:
        subprocess.run([sys.executable, str(engine() / "scripts" / "build.py"), str(args.repro), "--base", args.base,
                        "--out", str(args.out)])
        print(f"video: {args.out / (slug + '.mp4')}")
    return {"REPRODUCED": 1, "NOT REPRODUCED": 0}.get(runs[0]["verdict"], 2)


if __name__ == "__main__":
    sys.exit(main())
