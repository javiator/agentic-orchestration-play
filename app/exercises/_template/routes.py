"""Routes for the template exercise. Paths are relative to the exercise root:
"/" is its page, "/api/ask" is served at /<slug>/api/ask."""

from pathlib import Path

from fastapi import APIRouter, Form, HTTPException
from fastapi.responses import FileResponse

from app.core.config import load_research_models
from app.core.llm import complete

router = APIRouter()

STATIC_DIR = Path(__file__).parent / "static"


async def ask_model(prompt: str) -> tuple[str, str]:
    """One model call via the shared wrapper (mock mode works for free)."""
    spec = load_research_models()[0]
    reply = await complete(spec, [{"role": "user", "content": prompt}])
    return spec.label, reply.content


@router.get("/")
async def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


@router.post("/api/ask")
async def ask(prompt: str = Form(...)) -> dict:
    if not prompt.strip():
        raise HTTPException(400, "Prompt must not be empty")
    try:
        model, answer = await ask_model(prompt)
    except Exception as exc:
        raise HTTPException(502, f"Model call failed: {exc}")
    return {"model": model, "answer": answer}
