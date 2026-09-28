"""Routes for the template exercise. Paths are relative to the exercise root:
"/" is its page, "/api/ask" is served at /<slug>/api/ask."""

import asyncio
from pathlib import Path

import litellm
from fastapi import APIRouter, Form, HTTPException
from fastapi.responses import FileResponse

from app.core.config import AGENT_TIMEOUT, MAX_TOKENS, load_research_models

router = APIRouter()

STATIC_DIR = Path(__file__).parent / "static"


@router.get("/")
async def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


@router.post("/api/ask")
async def ask(prompt: str = Form(...)) -> dict:
    if not prompt.strip():
        raise HTTPException(400, "Prompt must not be empty")
    spec = load_research_models()[0]
    try:
        response = await asyncio.wait_for(
            litellm.acompletion(
                model=spec.name,
                api_base=spec.api_base,
                api_key=spec.api_key,
                max_tokens=MAX_TOKENS,
                messages=[{"role": "user", "content": prompt}],
            ),
            timeout=AGENT_TIMEOUT,
        )
    except Exception as exc:
        raise HTTPException(502, f"{spec.label} failed: {exc}")
    return {"model": spec.label, "answer": response.choices[0].message.content or ""}
