"""FastAPI routes for the reflection exercise (mounted at /reflection/).

POST api/run (task, mode=independent|self) → job id; GET api/stream/{job_id}
relays SSE events (run_started / version / critique / revising / done / error).
"""

from pathlib import Path

from fastapi import APIRouter, Form, HTTPException
from fastapi.responses import FileResponse, StreamingResponse

from app.core.config import MOCK_MODELS
from app.core.jobs import JobRegistry, headless_answer

from .orchestrator import MAX_ROUNDS, TARGET_SCORE, critic_model, run_reflection, writer_model

router = APIRouter()
_jobs = JobRegistry()
STATIC_DIR = Path(__file__).parent / "static"

# Tasks with several explicit constraints — first drafts tend to miss one.
SAMPLE_TASKS = [
    "Explain how a blockchain works to a 12-year-old in under 100 words, using exactly one everyday analogy.",
    "Write a polite email declining a meeting invitation and proposing two alternative times next week. Maximum 90 words.",
    "Give 3 arguments for and 3 against a four-day work week — one sentence each, no two making the same point.",
    "Write a Python function median(nums) with a docstring. Raise ValueError on an empty list. Don't use the statistics module.",
    "Summarise the main causes of World War I in exactly 4 bullet points of at most 15 words each.",
]


async def run_headless(task: str) -> str:
    return await headless_answer(lambda q: run_reflection(task, "independent", q),
                                 lambda done: done["best"]["text"])


@router.get("/")
async def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


@router.get("/api/info")
async def info() -> dict:
    return {"writer": writer_model().label, "critic_independent": critic_model("independent").label,
            "max_rounds": MAX_ROUNDS, "target": TARGET_SCORE, "mock": MOCK_MODELS, "samples": SAMPLE_TASKS}


@router.post("/api/run")
async def start_run(task: str = Form(...), mode: str = Form("independent")) -> dict:
    if not task.strip():
        raise HTTPException(400, "Task must not be empty")
    if mode not in ("independent", "self"):
        raise HTTPException(400, "mode must be 'independent' or 'self'")
    t = task.strip()
    return {"job_id": _jobs.start(lambda events: run_reflection(t, mode, events))}


@router.get("/api/stream/{job_id}")
async def stream(job_id: str) -> StreamingResponse:
    return _jobs.stream(job_id)
