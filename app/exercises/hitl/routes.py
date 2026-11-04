"""FastAPI routes for the human-in-the-loop exercise (mounted at /hitl/).

POST api/run (request) → job id; GET api/stream/{job_id} relays SSE events
(run_started / agent_note / action / approval_required / done / error).
POST api/decide/{job_id} (action_id, decision=approve|edit|reject, args, feedback)
answers a pending approval — the stream then continues.
"""

import json
from pathlib import Path

from fastapi import APIRouter, Form, HTTPException
from fastapi.responses import FileResponse, StreamingResponse

from app.core.config import MOCK_MODELS
from app.core.jobs import TERMINAL_EVENTS, JobRegistry

from .agent import APPROVAL_TIMEOUT, MAX_STEPS, Run, agent_model, run_hitl
from .world import AUTO_APPROVE_LIMIT, POLICY, RISK, SEED

router = APIRouter()
_jobs = JobRegistry()
_runs: dict[str, Run] = {}  # job_id → live run (pending approvals, its world)
STATIC_DIR = Path(__file__).parent / "static"

# Each one exercises a different branch of the policy.
SAMPLE_REQUESTS = [
    "Customer jane@example.com says her standing desk (order A1002) arrived damaged. Refund it in full and let her know.",
    "li@example.com says the espresso machine from order A1004 leaks a little. Give her a $40 goodwill refund and email her.",
    "raj@example.com wants a refund for order A1003 because it's late.",
    "jane@example.com asks for a refund on order A1003.",
]


@router.get("/")
async def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


@router.get("/api/info")
async def info() -> dict:
    return {"model": agent_model().label, "auto_approve_limit": AUTO_APPROVE_LIMIT,
            "approval_timeout": APPROVAL_TIMEOUT, "max_steps": MAX_STEPS, "policy": POLICY,
            "risks": RISK, "world": SEED, "mock": MOCK_MODELS, "samples": SAMPLE_REQUESTS}


@router.post("/api/run")
async def start_run(request: str = Form(...)) -> dict:
    if not request.strip():
        raise HTTPException(400, "Request must not be empty")
    run, text = Run(), request.strip()
    job_id = _jobs.start(lambda events: run_hitl(text, run, events))
    _runs[job_id] = run
    while len(_runs) > 50:  # don't grow without bound if streams are abandoned
        _runs.pop(next(iter(_runs)))
    return {"job_id": job_id}


@router.post("/api/decide/{job_id}")
async def decide(job_id: str, action_id: str = Form(...), decision: str = Form(...),
                 args: str = Form(""), feedback: str = Form("")) -> dict:
    run = _runs.get(job_id)
    if run is None:
        raise HTTPException(404, "Unknown or finished run")
    if decision not in ("approve", "edit", "reject"):
        raise HTTPException(400, "decision must be approve, edit or reject")
    payload: dict = {"decision": decision, "feedback": feedback.strip()}
    if decision == "edit":
        try:
            payload["args"] = json.loads(args)
        except json.JSONDecodeError as exc:
            raise HTTPException(400, f"Edited arguments are not valid JSON: {exc}")
        if not isinstance(payload["args"], dict):
            raise HTTPException(400, "Edited arguments must be a JSON object")
    if not run.decide(action_id, payload):
        raise HTTPException(409, "No pending approval with that id")
    return {"ok": True}


@router.get("/api/stream/{job_id}")
async def stream(job_id: str) -> StreamingResponse:
    def forget(event: dict) -> None:
        if event["type"] in TERMINAL_EVENTS:
            _runs.pop(job_id, None)
    return _jobs.stream(job_id, on_event=forget)
