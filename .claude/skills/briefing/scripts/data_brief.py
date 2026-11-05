"""Numbers for a briefing, computed from a CSV — so slides never carry hand-typed figures.

    python3 data_brief.py profile data.csv
    python3 data_brief.py agg data.csv --value "units*unit_price" --by "year(month)"
    python3 data_brief.py agg data.csv --value "units*unit_price" --by region --split "year(month)"
    python3 data_brief.py agg data.csv --value "units*unit_price" --by region --split "year(month)" \
                                       --pct-change 2024 2025 --sort desc
    python3 data_brief.py agg data.csv --value units --func mean --by product --where "region=North"

profile  rows, columns with their type (number / date / text), numeric stats, top
         values, date ranges — read it first to see what's worth saying.
agg      group and aggregate; prints JSON shaped like a demo-video `chart:` block
         ({type, labels, series: [{name, values}], unit}) plus `table` rows and a
         `stats` list (total, top group, …) ready for a `stats` slide.

  --value EXPR      a column or arithmetic over columns: units*unit_price, (a-b)/b
  --func F          sum (default) | mean | count | min | max
  --by KEY          group key: a column, or year(col) / quarter(col) / month(col) for
                    date-like columns ("2024-01", "2024-01-31", ...)
  --split KEY       a second key → one series per value (e.g. one per year)
  --where COL=VAL   filter (repeatable; also COL!=VAL, COL>N, COL<N)
  --pct-change A B  with --split: % change from split value A to B per group (→ hbar)
  --sort asc|desc   order groups by value (default: by key)   --top N   keep N groups
  --round N         decimals (default 2)

Standard library only.
"""

from __future__ import annotations

import argparse
import ast
import csv
import json
import operator
import re
import statistics
import sys
from collections import Counter, defaultdict
from pathlib import Path

DATE = re.compile(r"^(\d{4})-(\d{1,2})(?:-(\d{1,2}))?")


def read(path: Path) -> list[dict]:
    with path.open(newline="", encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


def num(v) -> float | None:
    if v is None:
        return None
    s = str(v).strip().replace(",", "").replace("$", "").replace("€", "").replace("£", "").rstrip("%")
    try:
        return float(s)
    except ValueError:
        return None


def col_type(values: list[str]) -> str:
    vals = [v for v in values if str(v).strip()]
    if not vals:
        return "empty"
    if all(num(v) is not None for v in vals):
        return "number"
    if all(DATE.match(str(v).strip()) for v in vals):
        return "date"
    return "text"


def profile(rows: list[dict]) -> str:
    cols = list(rows[0].keys()) if rows else []
    out = [f"{len(rows)} rows × {len(cols)} columns", ""]
    for c in cols:
        vals = [r[c] for r in rows]
        t = col_type(vals)
        line = f"- **{c}** ({t})"
        if t == "number":
            nums = [num(v) for v in vals if num(v) is not None]
            line += (f": min {min(nums):g}, max {max(nums):g}, mean {statistics.fmean(nums):.4g}, "
                     f"sum {sum(nums):.6g}")
        elif t == "date":
            ds = sorted(v.strip() for v in vals if v.strip())
            years = sorted({DATE.match(d).group(1) for d in ds})
            line += f": {ds[0]} → {ds[-1]} ({len(set(ds))} distinct; years {', '.join(years)})"
        else:
            top = Counter(v.strip() for v in vals if v.strip()).most_common(8)
            distinct = len({v.strip() for v in vals})
            line += f": {distinct} distinct — " + ", ".join(f"{k} ({n})" for k, n in top)
        out.append(line)
    out += ["", "Next: `agg` with --by a text/date column and --value a number column (or an expression)."]
    return "\n".join(out)


OPS = {ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul, ast.Div: operator.truediv,
       ast.USub: operator.neg, ast.Pow: operator.pow}


def compile_expr(expr: str, columns: list[str]):
    tree = ast.parse(expr, mode="eval").body
    names = {c: c for c in columns} | {re.sub(r"\W", "_", c): c for c in columns}

    def ev(node, row):
        if isinstance(node, ast.Constant):
            return float(node.value)
        if isinstance(node, ast.Name):
            if node.id not in names:
                raise SystemExit(f"unknown column {node.id!r}; columns: {', '.join(columns)}")
            return num(row[names[node.id]]) or 0.0
        if isinstance(node, ast.BinOp) and type(node.op) in OPS:
            return OPS[type(node.op)](ev(node.left, row), ev(node.right, row))
        if isinstance(node, ast.UnaryOp) and type(node.op) in OPS:
            return OPS[type(node.op)](ev(node.operand, row))
        raise SystemExit(f"unsupported expression: {expr!r} (columns, numbers, + - * / **)")
    return lambda row: ev(tree, row)


def key_fn(spec: str, columns: list[str]):
    m = re.fullmatch(r"(year|quarter|month)\((.+)\)", spec.strip())
    col = m.group(2) if m else spec.strip()
    if col not in columns:
        raise SystemExit(f"unknown column {col!r}; columns: {', '.join(columns)}")
    if not m:
        return lambda r: r[col].strip()
    part = m.group(1)

    def f(r):
        d = DATE.match(r[col].strip())
        if not d:
            return "?"
        y, mo = d.group(1), int(d.group(2))
        return y if part == "year" else f"{y}-Q{(mo - 1) // 3 + 1}" if part == "quarter" else f"{y}-{mo:02d}"
    return f


def where_fn(conds: list[str]):
    tests = []
    for c in conds:
        m = re.fullmatch(r"\s*(.+?)\s*(!=|=|>|<)\s*(.*?)\s*", c)
        if not m:
            raise SystemExit(f"bad --where {c!r} (COL=VAL, COL!=VAL, COL>N, COL<N)")
        col, op, val = m.groups()
        tests.append((col, op, val))

    def ok(r):
        for col, op, val in tests:
            v = r.get(col, "").strip()
            if op == "=" and v != val or op == "!=" and v == val:
                return False
            if op in "<>" and not ((num(v) or 0) > float(val) if op == ">" else (num(v) or 0) < float(val)):
                return False
        return True
    return ok


def human(v: float, unit: str = "") -> str:
    """760250 → 760k, 1234567 → 1.23M, 12.5 → 12.5 (for stat tiles)."""
    a = abs(v)
    text = (f"{v / 1e9:.3g}B" if a >= 1e9 else f"{v / 1e6:.3g}M" if a >= 1e6 else f"{v / 1e3:.3g}k" if a >= 1e4
            else f"{v:,.0f}" if float(v).is_integer() or a >= 100 else f"{v:.3g}")
    return text + unit


AGG = {"sum": sum, "mean": statistics.fmean, "count": len, "min": min, "max": max}


def agg(rows: list[dict], a) -> dict:
    cols = list(rows[0].keys())
    value = compile_expr(a.value, cols) if a.value else (lambda r: 1.0)
    by, split = key_fn(a.by, cols), key_fn(a.split, cols) if a.split else (lambda r: "")
    keep = where_fn(a.where)
    cells: dict[tuple, list[float]] = defaultdict(list)
    for r in rows:
        if keep(r):
            cells[(by(r), split(r))].append(value(r))
    func = AGG[a.func]
    groups = sorted({g for g, _ in cells})
    splits = sorted({s for _, s in cells})
    table = {g: {s: round(func(cells[(g, s)]), a.round) if cells.get((g, s)) else 0 for s in splits} for g in groups}
    unit = a.unit
    if a.pct_change:
        x, y = a.pct_change
        if x not in splits or y not in splits:
            raise SystemExit(f"--pct-change values must be split values: {splits}")
        table = {g: {f"{x}→{y}": round((v[y] - v[x]) / v[x] * 100, 1) if v[x] else 0} for g, v in table.items()}
        splits, unit = [f"{x}→{y}"], "%"
    if a.sort:
        tot = {g: sum(table[g].values()) for g in groups}
        groups.sort(key=lambda g: tot[g], reverse=a.sort == "desc")
    if a.top:
        groups = groups[:a.top]
    chart_type = "hbar" if a.pct_change or len(groups) > 8 else ("line" if re.match(r"(month|quarter)\(", a.by) else "bar")
    chart = {"type": chart_type, "labels": groups, "unit": unit,
             "series": [{"name": s or a.value or "count", "values": [table[g][s] for g in groups]} for s in splits]}
    totals = {g: round(sum(table[g].values()), a.round) for g in groups}
    stats = []
    if not a.pct_change:
        grand = round(sum(totals.values()), a.round)
        best = max(totals, key=totals.get)
        stats = [{"value": human(grand, unit), "label": f"total {a.func} of {a.value or 'rows'}"},
                 {"value": best, "label": f"top {a.by} ({human(totals[best], unit)})"}]
        if len(splits) == 2:
            x, y = splits
            sx, sy = sum(table[g][x] for g in groups), sum(table[g][y] for g in groups)
            if sx:
                pct = (sy - sx) / sx * 100
                stats.append({"value": f"{pct:+.1f}%", "label": f"{x} → {y}", "good": pct >= 0})
    else:
        best = max(groups, key=lambda g: table[g][splits[0]])
        worst = min(groups, key=lambda g: table[g][splits[0]])
        stats = [{"value": f"{table[best][splits[0]]:+.1f}%", "label": f"best: {best}", "good": True},
                 {"value": f"{table[worst][splits[0]]:+.1f}%", "label": f"worst: {worst}",
                  "good": table[worst][splits[0]] >= 0}]
    return {"chart": chart, "table": [{"group": g, **table[g]} for g in groups], "stats": stats,
            "query": {k: v for k, v in vars(a).items() if k not in ("file", "cmd")}}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("profile")
    p.add_argument("file", type=Path)
    g = sub.add_parser("agg")
    g.add_argument("file", type=Path)
    g.add_argument("--value")
    g.add_argument("--func", default="sum", choices=list(AGG))
    g.add_argument("--by", required=True)
    g.add_argument("--split")
    g.add_argument("--where", action="append", default=[])
    g.add_argument("--pct-change", nargs=2, metavar=("FROM", "TO"))
    g.add_argument("--sort", choices=["asc", "desc"])
    g.add_argument("--top", type=int)
    g.add_argument("--round", type=int, default=2)
    g.add_argument("--unit", default="")
    a = ap.parse_args()
    rows = read(a.file)
    if not rows:
        sys.exit("no rows")
    if a.cmd == "profile":
        print(profile(rows))
    else:
        if a.func == "count":
            a.value = a.value or None
        print(json.dumps(agg(rows, a), indent=1, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
