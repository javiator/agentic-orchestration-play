"""Background jobs streamed to the browser as Server-Sent Events.

The pattern every exercise uses for live progress:

    POST api/<start>  ──▶ registry.start(pipeline) ──▶ {"job_id": ...}
                              │ runs pipeline(events) as a background task;
                              │ the pipeline pushes event dicts onto `events`
    GET api/stream/{job_id} ──▶ registry.stream(job_id) relays each event as
                              an SSE `data:` line until 'done' or 'error'

Contract for pipelines: ALWAYS finish with a {"type": "done"} or
{"type": "error"} event — that's what ends the stream (and the browser's
EventSource). A pipeline that can raise should catch and emit 'error'.
"""

import asyncio
import json
import uuid
from collections.abc import Awaitable, Callable
from typing import Any

from fastapi import HTTPException
from fastapi.responses import StreamingResponse

Event = dict[str, Any]
Pipeline = Callable[[asyncio.Queue], Awaitable[None]]

TERMINAL_EVENTS = ("done", "error")


class JobRegistry:
    """In-memory job_id → event queue. One per exercise; single-process by
    design, like the rest of the app."""

    def __init__(self) -> None:
        self._jobs: dict[str, asyncio.Queue] = {}

    def start(self, pipeline: Pipeline) -> str:
        job_id = uuid.uuid4().hex
        events: asyncio.Queue = asyncio.Queue()
        self._jobs[job_id] = events
        task = asyncio.create_task(pipeline(events))
        task.add_done_callback(lambda t: t.exception())  # surface, don't swallow
        return job_id

    def stream(
        self, job_id: str, on_event: Callable[[Event], None] | None = None
    ) -> StreamingResponse:
        """SSE relay. `on_event` sees every event before it is sent (e.g. to
        record a finished turn into session memory)."""
        events = self._jobs.get(job_id)
        if events is None:
            raise HTTPException(404, "Unknown job")

        async def event_source():
            try:
                while True:
                    event = await events.get()
                    if on_event:
                        on_event(event)
                    yield f"data: {json.dumps(event)}\n\n"
                    if event["type"] in TERMINAL_EVENTS:
                        break
            finally:
                self._jobs.pop(job_id, None)

        return StreamingResponse(event_source(), media_type="text/event-stream")


async def run_to_completion(pipeline: Pipeline) -> list[Event]:
    """Run a pipeline without a browser attached and return all its events —
    used by `Exercise.run` (e.g. for batch evaluation)."""
    events: asyncio.Queue = asyncio.Queue()
    await pipeline(events)
    out: list[Event] = []
    while not events.empty():
        out.append(events.get_nowait())
    return out
