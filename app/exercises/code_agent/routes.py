"""FastAPI routes for the code-agent exercise (mounted at /code-agent/).

POST api/run (question) → job id; GET api/stream/{job_id} relays events as
SSE (run_started / agent_note / code_run / done / error).
"""

import asyncio
from pathlib import Path

from fastapi import APIRouter, Form, HTTPException
from fastapi.responses import FileResponse, StreamingResponse

from app.core.config import MOCK_MODELS
from app.core.jobs import JobRegistry, run_to_completion

from .agent import CODE_MAX_STEPS, load_code_model, run_code_agent
from .sandbox import CODE_MEMORY_MB, CODE_TIMEOUT, DATA_DIR

router = APIRouter()
_jobs = JobRegistry()

STATIC_DIR = Path(__file__).parent / "static"

# Exact answers a model can't reliably produce without running code.
SAMPLE_QUESTIONS = [
    "Which region had the highest revenue growth from 2024 to 2025 in sales.csv, and by what percentage?",
    "Did the East region sell more or fewer units in 2025 than in 2024? How did its revenue change?",
    "Which month had the most units sold across all regions and products?",
    "How many prime numbers are there below 100,000?",
    "How many days are there between 3 February 2024 and 17 November 2025?",
]


async def run_headless(question: str) -> str:
    """Exercise.run: one agent run, returning the final answer."""
    events = await run_to_completion(lambda q: run_code_agent(question, q))
    last = events[-1]
    if last["type"] != "done":
        raise RuntimeError(last.get("message", "code agent failed"))
    return last["answer"]


@router.get("/")
async def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


@router.get("/api/info")
async def info() -> dict:
    files = []
    for f in sorted(DATA_DIR.iterdir()):
        if f.is_file():
            lines = f.read_text().splitlines()
            files.append({"name": f.name, "rows": len(lines), "preview": "\n".join(lines[:4])})
    return {
        "model": load_code_model().label,
        "max_steps": CODE_MAX_STEPS,
        "timeout": CODE_TIMEOUT,
        "memory_mb": CODE_MEMORY_MB,
        "mock": MOCK_MODELS,
        "files": files,
        "samples": SAMPLE_QUESTIONS,
    }


@router.post("/api/run")
async def start_run(question: str = Form(...)) -> dict:
    if not question.strip():
        raise HTTPException(400, "Question must not be empty")

    async def pipeline(events: asyncio.Queue) -> None:
        await run_code_agent(question.strip(), events)

    return {"job_id": _jobs.start(pipeline)}


@router.get("/api/stream/{job_id}")
async def stream(job_id: str) -> StreamingResponse:
    return _jobs.stream(job_id)
