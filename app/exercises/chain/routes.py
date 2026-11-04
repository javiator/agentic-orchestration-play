"""FastAPI routes for the prompt-chain exercise (mounted at /chain/).

POST api/run (topic) → job id; GET api/stream/{job_id} relays SSE events
(run_started / step / variant_finished / done / error).
"""

from pathlib import Path

from fastapi import APIRouter, Form, HTTPException
from fastapi.responses import FileResponse, StreamingResponse

from app.core.config import MOCK_MODELS
from app.core.jobs import JobRegistry

from .orchestrator import CHAIN_FIX_ROUNDS, chain_model, run_compare
from .spec import SPEC_TEXT

router = APIRouter()
_jobs = JobRegistry()
STATIC_DIR = Path(__file__).parent / "static"

SAMPLE_TOPICS = [
    "How vaccines train the immune system",
    "Why interest rates affect house prices",
    "How a search engine decides which pages to show",
    "What makes a password strong",
    "How noise-cancelling headphones work",
]


@router.get("/")
async def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


@router.get("/api/info")
async def info() -> dict:
    return {"model": chain_model().label, "spec": SPEC_TEXT, "fix_rounds": CHAIN_FIX_ROUNDS,
            "mock": MOCK_MODELS, "samples": SAMPLE_TOPICS}


@router.post("/api/run")
async def start_run(topic: str = Form(...)) -> dict:
    if not topic.strip():
        raise HTTPException(400, "Topic must not be empty")
    t = topic.strip()[:200]
    return {"job_id": _jobs.start(lambda events: run_compare(t, events))}


@router.get("/api/stream/{job_id}")
async def stream(job_id: str) -> StreamingResponse:
    return _jobs.stream(job_id)
