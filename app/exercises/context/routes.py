"""FastAPI routes for the context-management exercise (mounted at /context/).

POST api/run → job id (replays the scripted conversation under every
strategy, then asks the probes); GET api/stream/{job_id} relays SSE events
(run_started / compaction / probe_result / strategy_finished / done / error).
"""

from pathlib import Path

from fastapi import APIRouter
from fastapi.responses import FileResponse, StreamingResponse

from app.core.config import MOCK_MODELS
from app.core.jobs import JobRegistry

from .orchestrator import (COMPACT_EVERY, KEEP_RECENT, SCRIPT, STRATEGIES, WINDOW,
                           context_model, run_all)

router = APIRouter()
_jobs = JobRegistry()
STATIC_DIR = Path(__file__).parent / "static"


@router.get("/")
async def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


@router.get("/api/info")
async def info() -> dict:
    return {"model": context_model().label, "strategies": STRATEGIES, "window": WINDOW,
            "compact_every": COMPACT_EVERY, "keep_recent": KEEP_RECENT, "script": SCRIPT,
            "mock": MOCK_MODELS}


@router.post("/api/run")
async def start_run() -> dict:
    return {"job_id": _jobs.start(lambda events: run_all(events))}


@router.get("/api/stream/{job_id}")
async def stream(job_id: str) -> StreamingResponse:
    return _jobs.stream(job_id)
