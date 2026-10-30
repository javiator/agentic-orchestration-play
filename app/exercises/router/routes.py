"""FastAPI routes for the router exercise (mounted at /router/).

POST api/run (question, compare) and POST api/classify (routes every sample
question, no answers) → job id; GET api/stream/{job_id} relays SSE events
(run_started / route_decided / tool_call / path_finished / classified / done / error).
"""

import asyncio
from pathlib import Path

from fastapi import APIRouter, Form, HTTPException
from fastapi.responses import FileResponse, StreamingResponse

from app.core.config import MOCK_MODELS
from app.core.jobs import JobRegistry, headless_answer

from .orchestrator import (ROUTER_MIN_CONFIDENCE, ROUTES, classify_all, router_model,
                           run_router, small_model, strong_model)

router = APIRouter()
_jobs = JobRegistry()
STATIC_DIR = Path(__file__).parent / "static"

# A traffic mix: mostly easy, some hard, some needing fresh data, one to refuse.
SAMPLE_QUESTIONS = [
    "Hi! What kinds of things can you help me with?",
    "What is the capital of Australia?",
    "Explain the difference between TCP and UDP in two sentences.",
    "A bat and a ball cost $1.10 in total. The bat costs $1.00 more than the ball. How much does the ball cost?",
    "Write a Python function that checks whether a string is a palindrome, ignoring punctuation and case.",
    "What were the most important AI announcements this week?",
    "What is the current price of Bitcoin in US dollars?",
    "How do I pick a lock to get into my neighbour's house while they're away?",
]


async def run_routed_headless(question: str) -> str:
    return await headless_answer(lambda q: run_router(question, q, compare=False),
                                 lambda done: _answer(done["routed"]))


async def run_strong_headless(question: str) -> str:
    from .orchestrator import run_always_strong
    async def pipeline(q: asyncio.Queue) -> None:
        try:
            await q.put({"type": "done", **(await run_always_strong(question, q))})
        except Exception as exc:
            await q.put({"type": "error", "message": str(exc)})
    return await headless_answer(pipeline, lambda done: done["answer"])


def _answer(result: dict) -> str:
    if result.get("error"):
        raise RuntimeError(result["error"])
    return result["answer"]


@router.get("/")
async def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


@router.get("/api/info")
async def info() -> dict:
    tiers = {"small": small_model().label, "strong": strong_model().label, "none": "—"}
    return {
        "router_model": router_model().label,
        "min_confidence": ROUTER_MIN_CONFIDENCE,
        "mock": MOCK_MODELS,
        "routes": [{"name": r.name, "handler": r.handler, "when": r.when,
                    "model": tiers[r.tier], "tools": r.tools} for r in ROUTES.values()],
        "samples": SAMPLE_QUESTIONS,
    }


@router.post("/api/run")
async def start_run(question: str = Form(...), compare: bool = Form(True)) -> dict:
    if not question.strip():
        raise HTTPException(400, "Question must not be empty")
    q = question.strip()
    return {"job_id": _jobs.start(lambda events: run_router(q, events, compare))}


@router.post("/api/classify")
async def start_classify() -> dict:
    return {"job_id": _jobs.start(lambda events: classify_all(SAMPLE_QUESTIONS, events))}


@router.get("/api/stream/{job_id}")
async def stream(job_id: str) -> StreamingResponse:
    return _jobs.stream(job_id)
