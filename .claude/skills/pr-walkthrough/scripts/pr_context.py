"""Summarize a branch's changes into a brief for writing a walkthrough video.

    python3 pr_context.py [--base origin/main] [--head HEAD] [--repo .] [--out brief.md]
                          [--pr-body body.md] [--max-hunks 40]

Standard library + git only. Writes a markdown brief (and brief.json next to it):

  - commits (subject + body), and the PR description if given
  - files changed, grouped by area (ui, api, backend, tests, docs, config, other)
    with +/- counts and status (added / modified / deleted / renamed)
  - every hunk with its line range IN THE NEW FILE — ready for a `code` slide:
        {kind: code, file: <path>, lines: "a-b", highlight: [...]}
  - UI hooks introduced by the change: new element ids / data-testids / classes,
    new routes or endpoints, new CLI flags — the things a live demo can show
  - a suggested outline (what to show live vs on slides)

The brief is input for Claude to write the demo YAML; nothing here calls a model.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from collections import defaultdict
from pathlib import Path

AREAS = [  # first match wins
    ("config", r"(^|/)requirements[^/]*\.txt$"),
    ("tests", r"(^|/)(tests?|__tests__|spec)/|(_test|\.test|\.spec)\.\w+$|(^|/)test_[^/]+$"),
    ("docs", r"\.(md|rst|txt|adoc)$|(^|/)docs?/"),
    ("ui", r"\.(html?|css|scss|less|jsx|tsx|vue|svelte)$|(^|/)(static|templates|components|pages|public|ui|frontend)/"
           r"|\.js$"),
    ("api", r"(^|/)(routes?|api|endpoints?|controllers?|handlers?|views)(/|\.py$|\.ts$)"),
    ("config", r"(^|/)(\.github|docker|deploy|infra)/|\.(ya?ml|toml|ini|cfg|json|env\.example)$"
               r"|(^|/)(Dockerfile|Makefile|requirements[^/]*\.txt|package\.json|pyproject\.toml)$"),
    ("backend", r"\.(py|go|rb|java|kt|rs|ts|cs|php|scala|ex|exs)$"),
]

HOOK_PATTERNS = {
    "element ids": r"""\bid=["']([\w:-]+)["']""",
    "test ids": r"""data-test(?:id)?=["']([\w:-]+)["']""",
    "routes": r"""@(?:\w+\.)?(?:get|post|put|patch|delete|route|api_route)\(\s*["']([^"']+)["']"""
              r"""|\b(?:path|re_path|url)\(\s*r?["']([^"']+)["']"""
              r"""|\b(?:app|router)\.(?:get|post|put|patch|delete|use)\(\s*["'`]([^"'`]+)["'`]""",
    "cli flags": r"""add_argument\(\s*["'](--[\w-]+)["']""",
    "env vars": r"""(?:getenv|environ(?:\.get)?)\(?\[?\s*["']([A-Z][A-Z0-9_]{2,})["']""",
}


def git(repo: Path, *args: str) -> str:
    res = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True)
    if res.returncode:
        sys.exit(f"git {' '.join(args)} failed: {res.stderr.strip()}")
    return res.stdout


def area_of(path: str) -> str:
    return next((name for name, pattern in AREAS if re.search(pattern, path)), "other")


def parse_diff(diff: str) -> dict[str, list[dict]]:
    """path → hunks: {start, end (new file), added: [(line_no, text)], removed: int, header}."""
    files: dict[str, list[dict]] = defaultdict(list)
    path, hunk, new_line = None, None, 0
    for line in diff.splitlines():
        if line.startswith(("diff --git", "--- ", "index ", "new file", "deleted file", "similarity", "rename ")):
            hunk = None
            continue
        if line.startswith("+++ "):
            path = None if line[4:] == "/dev/null" else line[6:]
        elif line.startswith("@@") and path:
            m = re.match(r"@@ -\d+(?:,\d+)? \+(\d+)(?:,(\d+))? @@ ?(.*)", line)
            start, count = int(m.group(1)), int(m.group(2) if m.group(2) is not None else 1)
            hunk = {"start": start, "end": max(start, start + count - 1), "added": [], "removed": 0,
                    "header": m.group(3).strip()}
            files[path].append(hunk)
            new_line = start
        elif hunk is not None and path:
            if line.startswith("+"):
                hunk["added"].append((new_line, line[1:]))
                new_line += 1
            elif line.startswith("-"):
                hunk["removed"] += 1
            elif not line.startswith("\\"):
                new_line += 1
    return files


SYMBOL = re.compile(r"^(\s*)(?:async\s+def|def|class|function|export\s+(?:default\s+)?(?:async\s+)?(?:function|class|const)"
                    r"|func|fn|pub\s+fn|interface|type)\s+([A-Za-z_$][\w$]*)")


def symbols(added: list[tuple[int, str]]) -> list[tuple[int, int, str]]:
    """Top-level-ish definitions among added lines: (first line, last line, name)."""
    found, in_class = [], False
    for n, text in added:
        m = SYMBOL.match(text)
        if not m:
            continue
        indent = len(m.group(1).expandtabs(4))
        if indent == 0:
            in_class = text.lstrip().startswith(("class", "export class", "interface"))
            found.append((n, m.group(2)))
        elif indent <= 4 and in_class:  # methods of a top-level class
            found.append((n, m.group(2)))
    last = added[-1][0] if added else 0
    return [(n, (found[i + 1][0] - 1) if i + 1 < len(found) else last, name) for i, (n, name) in enumerate(found)]


def ranges(nums: list[int]) -> list[str]:
    out, run = [], []
    for n in sorted(nums):
        if run and n == run[-1] + 1:
            run.append(n)
        else:
            if run:
                out.append(f"{run[0]}-{run[-1]}" if len(run) > 1 else str(run[0]))
            run = [n]
    if run:
        out.append(f"{run[0]}-{run[-1]}" if len(run) > 1 else str(run[0]))
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--base", default="origin/main")
    ap.add_argument("--head", default="HEAD")
    ap.add_argument("--repo", type=Path, default=Path("."))
    ap.add_argument("--out", type=Path, default=Path("walkthrough-brief.md"))
    ap.add_argument("--pr-body", type=Path, help="PR description (markdown) to include")
    ap.add_argument("--max-hunks", type=int, default=40, help="hunks listed in full (largest first)")
    args = ap.parse_args()

    repo = args.repo
    merge_base = git(repo, "merge-base", args.base, args.head).strip()
    span = f"{merge_base}..{args.head}"
    commits = [c for c in git(repo, "log", "--reverse", "--format=%h%x1f%s%x1f%b%x1e", span).split("\x1e") if c.strip()]
    commits = [dict(zip(("sha", "subject", "body"), c.strip().split("\x1f"))) for c in commits]
    trailer = re.compile(r"^(Co-Authored-By|Signed-off-by|Claude-Session|Reviewed-by|Change-Id):", re.I)
    for c in commits:
        lines = [ln for ln in c.get("body", "").splitlines() if not trailer.match(ln.strip())]
        c["body"] = "\n".join(lines).strip()
    status = {}
    for line in git(repo, "diff", "--name-status", "-M", span).splitlines():
        parts = line.split("\t")
        code = parts[0][0]
        status[parts[-1]] = {"A": "added", "M": "modified", "D": "deleted", "R": f"renamed from {parts[1]}"}.get(code, code)
    numstat = {}
    for line in git(repo, "diff", "--numstat", "-M", span).splitlines():
        add, rem, path = line.split("\t", 2)
        path = re.sub(r"\{[^}]* => ([^}]*)\}", r"\1", path).replace("//", "/").split(" => ")[-1]
        numstat[path] = (int(add) if add != "-" else 0, int(rem) if rem != "-" else 0)
    hunks = parse_diff(git(repo, "diff", "-U2", "-M", span))

    files = []
    for path, st in status.items():
        add, rem = numstat.get(path, (0, 0))
        files.append({"path": path, "status": st, "area": area_of(path), "added": add, "removed": rem,
                      "hunks": [{"lines": f'{h["start"]}-{h["end"]}', "highlight": ranges([n for n, _ in h["added"]]),
                                 "added": len(h["added"]), "removed": h["removed"], "context": h["header"],
                                 "symbols": [{"name": nm, "lines": f"{a}-{b}"} for a, b, nm in symbols(h["added"])]}
                                for h in hunks.get(path, [])]})

    hooks: dict[str, set[str]] = defaultdict(set)
    for path, hs in hunks.items():
        for h in hs:
            for _, text in h["added"]:
                for name, pattern in HOOK_PATTERNS.items():
                    for m in re.finditer(pattern, text):
                        value = next((g for g in m.groups() if g), None)
                        if value:
                            hooks[name].add(f"{value}  ({path})")

    by_area: dict[str, list[dict]] = defaultdict(list)
    for f in files:
        by_area[f["area"]].append(f)
    total_add = sum(f["added"] for f in files)
    total_rem = sum(f["removed"] for f in files)

    md = [f"# Walkthrough brief: {args.base}..{args.head}", "",
          f"{len(commits)} commits · {len(files)} files · +{total_add} −{total_rem}", ""]
    if args.pr_body and args.pr_body.exists():
        md += ["## PR description", "", args.pr_body.read_text().strip(), ""]
    md += ["## Commits", ""]
    for c in commits:
        md.append(f"- `{c['sha']}` {c['subject']}")
        if c["body"]:
            md += [f"  {ln}" for ln in c["body"][:600].splitlines()]
    md.append("")
    md += ["## Files by area", ""]
    for area in ("ui", "api", "backend", "config", "tests", "docs", "other"):
        if by_area.get(area):
            md.append(f"### {area} ({len(by_area[area])})")
            md += [f"- `{f['path']}` {f['status']} +{f['added']} −{f['removed']}" for f in
                   sorted(by_area[area], key=lambda f: -(f["added"] + f["removed"]))]
            md.append("")
    if hooks:
        md += ["## UI and interface hooks added (things a live demo can show)", ""]
        for name, values in hooks.items():
            md.append(f"**{name}**: " + ", ".join(sorted(values)[:40]))
        md.append("")
    all_hunks = sorted(((f["path"], h) for f in files for h in f["hunks"]), key=lambda x: -x[1]["added"])
    md += ["## Largest hunks (for `code` slides: file + lines + highlight)", ""]
    for path, h in all_hunks[:args.max_hunks]:
        a, b = map(int, h["lines"].split("-"))
        if b - a >= 40 and not h["symbols"]:
            md.append(f"- `{path}` lines {h['lines']} (+{h['added']} −{h['removed']}) — too long for one slide; "
                      "pick a ≤ 30-line excerpt")
        elif b - a < 40:
            md.append(f"- `{path}` lines {h['lines']} (+{h['added']} −{h['removed']})"
                      + (f" in `{h['context'][:60]}`" if h["context"] else "")
                      + f" → `{{kind: code, file: {path}, lines: \"{h['lines']}\", highlight: {json.dumps(h['highlight'][:6])}}}`")
        else:  # a big block (often a new file): offer its definitions as slide-sized excerpts
            defs = ", ".join(f"`{sym['name']}` {sym['lines']}" for sym in h["symbols"][:14])
            md.append(f"- `{path}` lines {h['lines']} (+{h['added']} −{h['removed']}) — too long for one slide; "
                      f"definitions: {defs}")
    md += ["", "## Suggested outline", "",
           "1. Title slide: what the change does for the user (not the file list); chips = areas.",
           "2. `bullets` slide: the 3–5 changes that matter, in the reviewer's words.",
           "3. Live steps for each user-visible change (" +
           (", ".join(sorted({v.split('  ')[0] for v in hooks.get('element ids', set())})[:8]) or "see ui files") + ").",
           "4. One `code` slide per key hunk (≤ 30 lines each), highlight the added lines.",
           "5. `takeaways`: how to review/test it, risks, what's next."]
    args.out.write_text("\n".join(md) + "\n")
    args.out.with_suffix(".json").write_text(json.dumps(
        {"base": args.base, "head": args.head, "merge_base": merge_base, "commits": commits, "files": files,
         "hooks": {k: sorted(v) for k, v in hooks.items()}}, indent=1))
    print(f"{args.out}  ({len(commits)} commits, {len(files)} files, +{total_add} −{total_rem})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
