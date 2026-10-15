"""FastAPI routes for the supervisor exercise (mounted at /supervisor/).

POST api/run (goal) → job id; GET api/stream/{job_id} relays the run's
events as SSE (run_started / supervisor_thinking / supervisor_note /
delegated / tool_call / specialist_finished / guard / done / error).
"""

import asyncio
from pathlib import Path

from fastapi import APIRouter, Form, HTTPException
from fastapi.responses import FileResponse, StreamingResponse

from app.core.config import MOCK_MODELS, load_research_models
from app.core.jobs import JobRegistry, run_to_completion

from .orchestrator import MAX_SUPERVISOR_TURNS, load_supervisor_model, run_supervisor
from .specialists import SPECIALISTS, model_for

router = APIRouter()
_jobs = JobRegistry()

STATIC_DIR = Path(__file__).parent / "static"


async def run_headless(goal: str) -> str:
    """Exercise.run: one supervised run, returning the final answer."""
    events = await run_to_completion(lambda q: run_supervisor(goal, q))
    last = events[-1]
    if last["type"] != "done":
        raise RuntimeError(last.get("message", "supervisor run failed"))
    return last["final_answer"]


@router.get("/")
async def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


@router.get("/api/info")
async def info() -> dict:
    """Team roster so the page can explain itself before any run."""
    models = load_research_models()
    return {
        "supervisor": load_supervisor_model().label,
        "max_turns": MAX_SUPERVISOR_TURNS,
        "mock": MOCK_MODELS,
        "specialists": [
            {"name": s.name, "model": model_for(s, models).label, "role": s.role,
             "tools": s.uses_tools}
            for s in SPECIALISTS
        ],
    }


@router.post("/api/run")
async def start_run(goal: str = Form(...)) -> dict:
    if not goal.strip():
        raise HTTPException(400, "Goal must not be empty")

    async def pipeline(events: asyncio.Queue) -> None:
        await run_supervisor(goal.strip(), events)

    return {"job_id": _jobs.start(pipeline)}


@router.get("/api/stream/{job_id}")
async def stream(job_id: str) -> StreamingResponse:
    return _jobs.stream(job_id)
