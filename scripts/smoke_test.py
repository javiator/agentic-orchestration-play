"""End-to-end smoke test: run every exercise once against a RUNNING server.

    ./start.sh                         # real keys in .env (or MOCK_MODELS=true ./start.sh)
    .venv/bin/python scripts/smoke_test.py                 # all exercises
    .venv/bin/python scripts/smoke_test.py rag hitl        # just these
    BASE=http://localhost:9000 .venv/bin/python scripts/smoke_test.py

For each exercise it submits one representative request, follows the SSE
stream to the end (auto-approving Human-in-the-Loop pauses), and prints
PASS/FAIL with time, model calls and the start of the answer. Every final
event is saved to smoke_report.json (gitignored) so you can read the full
answers afterwards. Exit code 1 if anything failed.

This checks that the plumbing works with your providers (tool calling,
JSON replies, timeouts) — not that answers are good. For quality, use the
Eval Harness.
"""

import json
import os
import sys
import time
from pathlib import Path

import httpx

BASE = os.getenv("BASE", "http://localhost:8000").rstrip("/")
TIMEOUT = float(os.getenv("SMOKE_TIMEOUT", "600"))
REPORT = Path(__file__).resolve().parents[1] / "smoke_report.json"


def stream_job(client: httpx.Client, slug: str, start_path: str, data: dict, stream_query: str = "",
               on_event=None) -> dict:
    r = client.post(f"{BASE}/{slug}/{start_path}", data=data)
    r.raise_for_status()
    job = r.json()["job_id"]
    last = None
    with client.stream("GET", f"{BASE}/{slug}/api/stream/{job}{stream_query}", timeout=TIMEOUT) as resp:
        for line in resp.iter_lines():
            if not line.startswith("data: "):
                continue
            ev = json.loads(line[6:])
            if on_event:
                on_event(client, slug, job, ev)
            last = ev
            if ev["type"] in ("done", "error"):
                break
    if last is None:
        raise RuntimeError("stream ended without events")
    if last["type"] == "error":
        raise RuntimeError(last.get("message", "error event"))
    return last


def approve_all(client: httpx.Client, slug: str, job: str, ev: dict) -> None:
    if ev["type"] == "approval_required":
        client.post(f"{BASE}/{slug}/api/decide/{job}", data={"action_id": ev["action_id"], "decision": "approve"})


def info(client: httpx.Client, slug: str) -> dict:
    return client.get(f"{BASE}/{slug}/api/info").json()


def first_answer(done: dict) -> str:
    for key in ("answer", "final_answer"):
        if isinstance(done.get(key), str):
            return done[key]
    if "verdict" in done:
        return done["verdict"].get("merged_answer", "")
    if "best" in done:
        return done["best"]["text"]
    if "results" in done and isinstance(done["results"], dict):
        parts = []
        for name, r in done["results"].items():
            if isinstance(r, dict):
                text = r.get("answer") or r.get("text") or (f"score {r['score']}/{r['probes']}" if "score" in r else "")
                parts.append(f"[{name}] {r.get('error') or text}")
        return " | ".join(parts)
    if "routed" in done:
        return done["routed"].get("answer") or done["routed"].get("error", "")
    if "summaries" in done:
        return "; ".join(f"{s['label']}: grade {s['grade_avg']}" for s in done["summaries"])
    return json.dumps(done)[:300]


def variant_errors(done: dict) -> list[str]:
    """Side-by-side exercises contain failures inside 'done' — surface them."""
    errs = []
    for key in ("results",):
        if isinstance(done.get(key), dict):
            errs += [f"{k}: {v['error']}" for k, v in done[key].items() if isinstance(v, dict) and v.get("error")]
    for key in ("routed", "baseline"):
        if isinstance(done.get(key), dict) and done[key].get("error"):
            errs.append(f"{key}: {done[key]['error']}")
    return errs


# slug → function(client) → final 'done' event
CASES = {
    "consensus": lambda c: stream_job(
        c, "consensus", "api/query",
        {"query": "In two sentences: why is the sky blue?", "use_tools": "true", "rebuttal": "true"},
        stream_query="?session_id=&query=smoke"),
    "supervisor": lambda c: stream_job(c, "supervisor", "api/run",
                                       {"goal": "Briefly: should a 5-person startup use microservices? Recommend."}),
    "router": lambda c: stream_job(c, "router", "api/run", {"question": info(c, "router")["samples"][3], "compare": "true"}),
    "router-classify": lambda c: stream_job(c, "router", "api/classify", {}),
    "chain": lambda c: stream_job(c, "chain", "api/run", {"topic": info(c, "chain")["samples"][0]}),
    "planner": lambda c: stream_job(c, "planner", "api/run",
                                    {"goal": info(c, "planner")["samples"][0], "inject_failure": "true"}),
    "rag": lambda c: stream_job(c, "rag", "api/run", {"question": info(c, "rag")["samples"][1]}),
    "reflection": lambda c: stream_job(c, "reflection", "api/run", {"task": info(c, "reflection")["samples"][0]}),
    "code-agent": lambda c: stream_job(c, "code-agent", "api/run", {"question": info(c, "code-agent")["samples"][0]}),
    "hitl": lambda c: stream_job(c, "hitl", "api/run", {"request": info(c, "hitl")["samples"][0]}, on_event=approve_all),
    "guardrails": lambda c: stream_job(c, "guardrails", "api/run", {"message": info(c, "guardrails")["samples"][2]}),
    "context": lambda c: stream_job(c, "context", "api/run", {}),
    "eval": lambda c: stream_job(c, "eval", "api/run",
                                 {"dataset": "smoke", "targets": ["model:1", "exercise:router:routed"]}),
}


def main() -> int:
    wanted = sys.argv[1:] or list(CASES)
    unknown = [w for w in wanted if w not in CASES]
    if unknown:
        print(f"Unknown: {unknown}. Choose from: {', '.join(CASES)}")
        return 2
    report, failed = {}, 0
    with httpx.Client(timeout=60) as client:
        try:
            client.get(f"{BASE}/api/exercises").raise_for_status()
        except Exception as exc:
            print(f"Server not reachable at {BASE} ({exc}). Start it with ./start.sh")
            return 2
        for name in wanted:
            started = time.perf_counter()
            try:
                done = CASES[name](client)
                errs = variant_errors(done)
                status = "FAIL" if errs else "PASS"
                usage = done.get("usage") or (done if "calls" in done else {})
                detail = "; ".join(errs) if errs else first_answer(done)
                report[name] = {"status": status, "done": done}
            except Exception as exc:
                status, usage, detail = "FAIL", {}, f"{type(exc).__name__}: {exc}"
                report[name] = {"status": status, "error": detail}
            failed += status == "FAIL"
            secs = time.perf_counter() - started
            calls = f"{usage['calls']} calls" if "calls" in usage else ""
            print(f"{status}  {name:16} {secs:6.1f}s {calls:>9}  {' '.join(str(detail).split())[:110]}")
    REPORT.write_text(json.dumps(report, indent=2, default=str))
    print(f"\n{len(wanted) - failed}/{len(wanted)} passed. Full results: {REPORT}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
