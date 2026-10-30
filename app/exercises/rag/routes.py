"""FastAPI routes for the RAG exercise (mounted at /rag/).

POST api/run (question) → job id; GET api/stream/{job_id} relays events as
SSE (run_started / retrieval / agent_step / grounding / variant_finished /
done / error). GET api/doc/{doc_id} serves a corpus document for viewing.
"""

import asyncio
import re
from pathlib import Path

from fastapi import APIRouter, Form, HTTPException
from fastapi.responses import FileResponse, StreamingResponse

from app.core.config import MOCK_MODELS
from app.core.jobs import JobRegistry, run_to_completion

from .pipelines import RAG_MAX_STEPS, RAG_VERIFY_ROUNDS, load_rag_model, run_compare
from .retrieval import TOP_K, doc_title, get_index, load_docs

router = APIRouter()
_jobs = JobRegistry()

STATIC_DIR = Path(__file__).parent / "static"

# Picked to show where each variant wins: one-fact lookups (both fine),
# multi-hop (agent should win), and one the documents can't answer.
SAMPLE_QUESTIONS = [
    "What is Kestrel?",
    "Who owns the postmortem for the April 2025 Beacon outage? Give the person's name.",
    "What is the maximum nightly hotel rate for a business trip to Tokyo?",
    "Who has to approve a $12,000 expense? Name the person.",
    "What does a 10-seat Beacon Team plan cost per month after the 2025 price change?",
    "How many desks does the Toronto office have?",
]


def _headless(variant: str):
    async def run(question: str) -> str:
        events = await run_to_completion(lambda q: run_compare(question, q, (variant,)))
        last = events[-1]
        if last["type"] != "done":
            raise RuntimeError(last.get("message", "RAG run failed"))
        result = last["results"][variant]
        if result["error"]:
            raise RuntimeError(result["error"])
        return result["answer"]

    return run


run_plain_headless = _headless("plain")
run_agentic_headless = _headless("agentic")


@router.get("/")
async def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


@router.get("/api/info")
async def info() -> dict:
    index = get_index()
    docs = load_docs()
    return {
        "model": load_rag_model().label,
        "backend": index.backend,
        "top_k": TOP_K,
        "max_steps": RAG_MAX_STEPS,
        "verify_rounds": RAG_VERIFY_ROUNDS,
        "mock": MOCK_MODELS,
        "chunks": len(index.chunks),
        "docs": [{"id": d, "title": doc_title(t)} for d, t in docs.items()],
        "samples": SAMPLE_QUESTIONS,
    }


@router.get("/api/doc/{doc_id}")
async def doc(doc_id: str) -> dict:
    docs = load_docs()
    if not re.fullmatch(r"[\w-]+", doc_id) or doc_id not in docs:
        raise HTTPException(404, "Unknown document")
    return {"id": doc_id, "text": docs[doc_id]}


@router.post("/api/run")
async def start_run(question: str = Form(...)) -> dict:
    if not question.strip():
        raise HTTPException(400, "Question must not be empty")

    async def pipeline(events: asyncio.Queue) -> None:
        await run_compare(question.strip(), events)

    return {"job_id": _jobs.start(pipeline)}


@router.get("/api/stream/{job_id}")
async def stream(job_id: str) -> StreamingResponse:
    return _jobs.stream(job_id)
