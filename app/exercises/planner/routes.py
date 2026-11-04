"""FastAPI routes for the planner exercise (mounted at /planner/).

POST api/run (goal, inject_failure) → job id; GET api/stream/{job_id} relays
SSE events (run_started / plan_created / plan_rejected / wave_started /
step_started / tool_call / step_finished / replanning / replan_skipped /
synthesizing / done / error).
"""

from pathlib import Path

from fastapi import APIRouter, Form, HTTPException
from fastapi.responses import FileResponse, StreamingResponse

from app.core.config import MOCK_MODELS, load_research_models
from app.core.jobs import JobRegistry, headless_answer

from .orchestrator import MAX_REPLANS, MAX_STEPS, planner_model, run_planner

router = APIRouter()
_jobs = JobRegistry()
STATIC_DIR = Path(__file__).parent / "static"

# Goals that decompose into independent parts (parallel research) + synthesis.
SAMPLE_GOALS = [
    "Compare PostgreSQL and MongoDB for a small e-commerce startup and recommend one.",
    "Plan a 3-day trip to Lisbon for a family with two kids: what to see, where to stay, rough budget.",
    "Should a 10-person team adopt Kubernetes now or stay on a PaaS like Heroku? Weigh cost, effort and risk.",
    "What are the pros and cons of heat pumps versus gas boilers for a home in the UK?",
]


async def run_headless(goal: str) -> str:
    return await headless_answer(lambda q: run_planner(goal, q), lambda done: done["answer"])


@router.get("/")
async def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


@router.get("/api/info")
async def info() -> dict:
    return {"planner": planner_model().label, "workers": [m.label for m in load_research_models()],
            "max_steps": MAX_STEPS, "max_replans": MAX_REPLANS, "mock": MOCK_MODELS,
            "samples": SAMPLE_GOALS}


@router.post("/api/run")
async def start_run(goal: str = Form(...), inject_failure: bool = Form(False)) -> dict:
    if not goal.strip():
        raise HTTPException(400, "Goal must not be empty")
    g = goal.strip()
    return {"job_id": _jobs.start(lambda events: run_planner(g, events, inject_failure))}


@router.get("/api/stream/{job_id}")
async def stream(job_id: str) -> StreamingResponse:
    return _jobs.stream(job_id)
