"""FastAPI routes for the consensus exercise (mounted at /consensus/).

Flow: the browser POSTs the query + context + files + feature toggles to
api/query (multipart), gets back a job id, then opens an SSE stream at
api/stream/{job_id}. The orchestrator pushes events (agent_started /
tool_call / agent_finished / fanout_complete / memory_recalled /
judge_started / rebuttal_started / done / error) onto a queue that this
layer relays as SSE, so the UI can show progress while agents are running.

SESSIONS (session memory): passing the session_id returned by a previous
query makes the new query a follow-up — prior turns of that session are
injected into the shared context for every agent. This is the conversation-
level multi-turn layer; the tool loop inside each agent is the inner one.
"""

import asyncio
import json
import uuid
from pathlib import Path

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, StreamingResponse

from app.core.config import load_judge_model, load_research_models
from app.core.files import parse_upload

from .orchestrator import RunOptions, run_consensus

router = APIRouter()

# In-memory stores — fine for a single-user tool:
#   _jobs:     job_id -> live event queue for the SSE relay
#   _sessions: session_id -> [{"query", "merged_answer"}, ...] (session memory)
_jobs: dict[str, asyncio.Queue] = {}
_sessions: dict[str, list[dict[str, str]]] = {}

STATIC_DIR = Path(__file__).parent / "static"


@router.get("/")
async def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


@router.get("/api/models")
async def models() -> dict:
    """Let the UI render one panel per configured model before any query runs."""
    return {
        "models": [m.label for m in load_research_models()],
        "judge": load_judge_model().label,
    }


@router.post("/api/query")
async def submit_query(
    query: str = Form(...),
    context_text: str = Form(""),
    files: list[UploadFile] = File(default=[]),
    use_tools: bool = Form(False),
    rebuttal: bool = Form(False),
    use_memory: bool = Form(False),
    session_id: str = Form(""),
) -> dict:
    if not query.strip():
        raise HTTPException(400, "Query must not be empty")

    parsed_files = []
    for f in files:
        if not f.filename:
            continue
        try:
            parsed_files.append(parse_upload(f.filename, await f.read()))
        except ValueError as exc:
            raise HTTPException(400, str(exc))

    research = load_research_models()
    judge = load_judge_model()

    # Resolve the session: reuse if the client sent a known id (follow-up),
    # otherwise start a fresh one. History is copied so the running pipeline
    # sees a stable snapshot even if another turn starts later.
    if session_id and session_id in _sessions:
        history = list(_sessions[session_id])
    else:
        session_id = uuid.uuid4().hex
        _sessions[session_id] = []
        history = []

    options = RunOptions(
        use_tools=use_tools, rebuttal=rebuttal, use_memory=use_memory, history=history
    )

    job_id = uuid.uuid4().hex
    events: asyncio.Queue = asyncio.Queue()
    _jobs[job_id] = events

    async def pipeline() -> None:
        await run_consensus(query, context_text, parsed_files, research, judge, events, options)

    # Fire the pipeline in the background; the SSE endpoint drains the queue.
    # Session history is appended by the stream relay when it sees 'done', so
    # a follow-up asked after the answer renders always has this turn.
    task = asyncio.create_task(pipeline())
    task.add_done_callback(lambda t: t.exception())  # surface, don't swallow

    return {
        "job_id": job_id,
        "session_id": session_id,
        "turn": len(history) + 1,
        "models": [m.label for m in research],
        "judge": judge.label,
        "options": {"use_tools": use_tools, "rebuttal": rebuttal, "use_memory": use_memory},
        "attached_files": [{"name": f["name"], "chars": f["chars"]} for f in parsed_files],
    }


@router.get("/api/stream/{job_id}")
async def stream(job_id: str, session_id: str = "", query: str = "") -> StreamingResponse:
    events = _jobs.get(job_id)
    if events is None:
        raise HTTPException(404, "Unknown job")

    async def event_source():
        try:
            while True:
                event = await events.get()
                # Record the finished turn into session memory so the next
                # query in this session can reference it.
                if event["type"] == "done" and session_id in _sessions and query:
                    _sessions[session_id].append(
                        {
                            "query": query,
                            "merged_answer": event["verdict"].get("merged_answer", ""),
                        }
                    )
                yield f"data: {json.dumps(event)}\n\n"
                if event["type"] in ("done", "error"):
                    break
        finally:
            _jobs.pop(job_id, None)

    return StreamingResponse(event_source(), media_type="text/event-stream")
