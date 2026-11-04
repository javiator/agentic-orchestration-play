"""FastAPI routes for the eval exercise (mounted at /eval/).

POST api/run (dataset + one or more `targets`) → job id; GET api/stream/{id}
relays eval_started / item_started / item_finished / done / error as SSE.
"""

import asyncio
from pathlib import Path

from fastapi import APIRouter, Form, HTTPException
from fastapi.responses import FileResponse, StreamingResponse

from app.core.config import MOCK_MODELS
from app.core.jobs import JobRegistry

from . import store
from .harness import EVAL_CONCURRENCY, list_datasets, list_targets, load_grader_model, run_eval

router = APIRouter()
_jobs = JobRegistry()

STATIC_DIR = Path(__file__).parent / "static"


@router.get("/")
async def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


@router.get("/api/options")
async def options() -> dict:
    return {
        "datasets": list_datasets(),
        "targets": [{"id": t.id, "label": t.label} for t in list_targets()],
        "grader": load_grader_model().label,
        "concurrency": EVAL_CONCURRENCY,
        "mock": MOCK_MODELS,
    }


@router.get("/api/history")
async def history() -> dict:
    return {"runs": store.recent_runs()}


@router.post("/api/run")
async def start_eval(dataset: str = Form(...), targets: list[str] = Form(...)) -> dict:
    if not targets:
        raise HTTPException(400, "Select at least one target")

    async def pipeline(events: asyncio.Queue) -> None:
        await run_eval(dataset, targets, events)

    return {"job_id": _jobs.start(pipeline)}


@router.get("/api/stream/{job_id}")
async def stream(job_id: str) -> StreamingResponse:
    return _jobs.stream(job_id)
