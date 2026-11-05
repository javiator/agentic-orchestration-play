"""Map a codebase for a narrated onboarding tour.

    python3 tour_context.py [REPO] [--paths app/core app/exercises] [--out tour-brief.md]
                            [--max-files 60]

Standard library only. Writes a markdown brief (+ .json) with:

  - the project's own docs: headings of AGENTS.md / CLAUDE.md / README.md / HANDOFF.md
  - a tree of folders with file counts and lines of code (vendor/build dirs skipped)
  - per source file: the module docstring's first paragraph, its definitions with line
    ranges (→ `code` slides), and whether it's an entry point (main, CLI, routes)
  - ASCII diagrams found in docstrings and comments, with file:line (→ `diagram` slides)
  - web routes (FastAPI/Flask/Express/Django patterns) → pages a live demo can open
  - suggested tour areas: the biggest/most central folders, in reading order
"""

from __future__ import annotations

import argparse
import ast
import json
import os
import re
import sys
import textwrap
from collections import defaultdict
from pathlib import Path

SKIP_DIRS = {".git", "node_modules", "__pycache__", ".venv", "venv", "dist", "build", ".mypy_cache", ".pytest_cache",
             ".tox", ".next", "target", "out", "videos", ".idea", ".vscode", "coverage"}
SOURCE = {".py", ".js", ".ts", ".tsx", ".jsx", ".go", ".rs", ".java", ".kt", ".rb", ".php", ".cs", ".swift", ".scala"}
DOCS = ("AGENTS.md", "CLAUDE.md", "README.md", "HANDOFF.md", "ARCHITECTURE.md", "CONTRIBUTING.md")
DRAWING = set("─│▶►→┌┐└┘├┤┬┴┼═║╔╗╚╝←↓↑▼▲")
BOX = set("─│┌┐└┘├┤┬┴┼═║╔╗╚╝")
QUOTES = {chr(34) * 3, chr(39) * 3}
ROUTE = re.compile(r"""@(?:\w+\.)+(get|post|put|patch|delete|route|websocket|api_route)\(\s*["']([^"']+)["']"""
                   r"""|\b(?:app|router)\.(get|post|put|patch|delete|use)\(\s*["'`]([^"'`]+)["'`]"""
                   r"""|\bpath\(\s*r?["']([^"']+)["']""")
DEF = re.compile(r"^(\s*)(?:async\s+def|def|class|function|export\s+(?:default\s+)?(?:async\s+)?(?:function|class|const)"
                 r"|func|fn|pub\s+fn|interface|type)\s+([A-Za-z_$][\w$]*)")


def walk(root: Path, paths: list[str]) -> list[Path]:
    starts = [root / p for p in paths] if paths else [root]
    files = []
    for start in starts:
        if start.is_file():
            files.append(start)
            continue
        for dirpath, dirnames, filenames in os.walk(start):
            rel = Path(dirpath).relative_to(root)
            dirnames[:] = sorted(d for d in dirnames if d not in SKIP_DIRS
                                 and not (d.startswith(".") and not paths and d != ".claude"))
            files += [Path(dirpath) / f for f in sorted(filenames) if not rel.parts or rel.parts[0] not in SKIP_DIRS]
    return files


def loc(path: Path) -> int:
    """Non-blank lines of a text file; 0 for binaries (images, videos, databases)."""
    try:
        data = path.read_bytes()
    except OSError:
        return 0
    if b"\0" in data[:4096]:
        return 0
    return sum(1 for line in data.decode(errors="replace").splitlines() if line.strip())


def defs(text: str) -> list[dict]:
    lines = text.splitlines()
    found, in_class = [], False
    for i, line in enumerate(lines, 1):
        m = DEF.match(line)
        if not m:
            continue
        indent = len(m.group(1).expandtabs(4))
        if indent == 0:
            in_class = line.lstrip().startswith(("class", "export class", "interface"))
            found.append([i, m.group(2), 0])
        elif indent <= 4 and in_class:
            found.append([i, m.group(2), 4])
    out = []
    for k, (start, name, ind) in enumerate(found):
        end = found[k + 1][0] - 1 if k + 1 < len(found) else len(lines)
        while end > start and not lines[end - 1].strip():
            end -= 1
        out.append({"name": name, "lines": f"{start}-{end}", "size": end - start + 1, "method": bool(ind)})
    return out


def module_doc(path: Path, text: str) -> str:
    if path.suffix == ".py":
        try:
            return ast.get_docstring(ast.parse(text)) or ""
        except SyntaxError:
            return ""
    m = re.match(r"\s*/\*\*?(.*?)\*/", text, re.S)
    return re.sub(r"^\s*\* ?", "", m.group(1), flags=re.M).strip() if m else ""


def diagrams(path: Path, text: str) -> list[dict]:
    """Blocks of 3+ consecutive lines containing box-drawing / arrow characters."""
    out, block, start = [], [], 0
    lines = text.splitlines()
    for i, line in enumerate(lines + [""], 1):
        drawn = sum(ch in DRAWING for ch in line) >= 1 and len(line.strip()) > 3
        if drawn or (block and line.strip() and len(block) < 60 and any(ch in DRAWING for ch in "".join(block[-2:]))):
            if not block:
                start = i
            block.append(line)
        else:
            literal = any(re.search(r"[\"'][─│┌└▶→]", b) for b in block)  # box chars inside a string = code
            if sum(any(ch in BOX for ch in b) for b in block) >= 2 and not literal:  # drawings, not prose
                clean = [re.sub(r"^\s*(#|//|\*|--)\s?", "", b) for b in block if b.strip() not in QUOTES]
                out.append({"file": str(path), "lines": f"{start}-{start + len(block) - 1}",
                            "diagram": textwrap.dedent("\n".join(clean)).strip("\n")})
            block = []
    return out


def outline(path: Path) -> list[str]:
    return [line.strip() for line in path.read_text(errors="replace").splitlines() if re.match(r"#{1,3} ", line)]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("repo", nargs="?", type=Path, default=Path("."))
    ap.add_argument("--paths", nargs="*", default=[], help="limit to these folders/files (relative to REPO)")
    ap.add_argument("--out", type=Path, default=Path("tour-brief.md"))
    ap.add_argument("--max-files", type=int, default=60, help="source files described in detail (largest first)")
    args = ap.parse_args()
    root = args.repo.resolve()
    files = walk(root, args.paths)

    folders: dict[str, list[int]] = defaultdict(lambda: [0, 0])
    sources, all_diagrams, routes = [], [], []
    for f in files:
        rel = f.relative_to(root)
        n = loc(f)
        for depth in range(1, min(3, len(rel.parts)) + 0):
            key = "/".join(rel.parts[:depth])
            folders[key][0] += 1
            folders[key][1] += n
        if f.suffix not in SOURCE and f.suffix not in {".md"}:
            continue
        text = f.read_text(errors="replace")
        all_diagrams += [{**d, "file": str(rel)} for d in diagrams(rel, text)]
        if f.suffix == ".md":
            continue
        for m in ROUTE.finditer(text):
            route = next(g for g in m.groups()[1::2] + (m.group(5),) if g) if any(m.groups()) else None
            if route:
                line_no = text[:m.start()].count("\n") + 1
                routes.append({"route": route, "file": str(rel), "line": line_no})
        doc = module_doc(f, text)
        sources.append({"file": str(rel), "loc": n, "doc": doc.split("\n\n")[0].strip()[:400],
                        "entry": bool(re.search(r"__main__|argparse|FastAPI\(|Flask\(|express\(\)|func main\(", text)),
                        "defs": defs(text)})

    md = [f"# Tour brief: {root.name}" + (f" ({', '.join(args.paths)})" if args.paths else ""), ""]
    for name in DOCS:
        p = root / name
        if p.exists():
            md += [f"## {name} — outline", ""] + [f"- {h}" for h in outline(p)[:40]] + [""]
    md += ["## Folders (files · lines of code)", ""]
    for key, (count, lines) in sorted(folders.items(), key=lambda kv: (kv[0].count("/"), -kv[1][1])):
        if count >= 2 or key.count("/") == 0:
            md.append(f"{'  ' * key.count('/')}- `{key}/` {count} files · {lines} lines")
    md += ["", "## Entry points", ""] + [f"- `{s['file']}` — {s['doc'].splitlines()[0][:120] if s['doc'] else ''}"
                                       for s in sources if s["entry"]] + [""]
    if routes:
        md += ["## Routes (pages/endpoints a live demo can open)", ""]
        md += [f"- `{r['route']}` — {r['file']}:{r['line']}" for r in routes[:60]] + [""]
    md += ["## Diagrams already in the code (→ `diagram` slides; copy the text)", ""]
    for d in all_diagrams[:25]:
        md += [f"### {d['file']}:{d['lines']}", "", "```", d["diagram"], "```", ""]
    md += ["## Source files (largest first): docstring + definitions (→ `code` slides)", ""]
    for s in sorted(sources, key=lambda s: -s["loc"])[:args.max_files]:
        md.append(f"### `{s['file']}` — {s['loc']} lines{' · entry point' if s['entry'] else ''}")
        if s["doc"]:
            md += ["", "> " + s["doc"].replace("\n", "\n> "), ""]
        top = [d for d in s["defs"] if not d["method"]]
        if top:
            md.append("Definitions: " + ", ".join(f"`{d['name']}` {d['lines']}" for d in top[:20]))
        md.append("")
    top_folders = [k for k, _ in sorted(((k, v) for k, v in folders.items() if k.count("/") <= 1 and v[1] > 50),
                                        key=lambda kv: -kv[1][1])][:8]
    md += ["## Suggested tour", "",
           "1. Overview video: what the project does (README), the architecture diagram, the folder map, "
           "how to run it.",
           *[f"{i}. `{k}/` — its docstring diagram (if any), 1–3 key definitions as code slides, "
             "and the page it serves (if any) recorded live." for i, k in enumerate(top_folders, 2)],
           "", "Keep each video 2–4 minutes; one area per video; finish with 'where to start reading'."]
    args.out.write_text("\n".join(md) + "\n")
    args.out.with_suffix(".json").write_text(json.dumps({"folders": folders, "sources": sources,
                                                         "diagrams": all_diagrams, "routes": routes}, indent=1))
    print(f"{args.out}  ({len(sources)} source files, {len(all_diagrams)} diagrams, {len(routes)} routes)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
