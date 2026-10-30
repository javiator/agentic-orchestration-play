"""Root web app: a landing page plus every discovered exercise.

Each exercise package under app/exercises/ is mounted at /<slug>/ (its
router) and /<slug>/static/ (its UI files). The landing page at / lists them
via /api/exercises; the shared nav bar (static/nav.js) lets every exercise
page switch to another one. One process, one port, one ./start.sh.
"""

from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from .exercises import discover_exercises

app = FastAPI(title="Agentic Orchestration Playground")

STATIC_DIR = Path(__file__).parent / "static"
EXERCISES = discover_exercises()

for ex in EXERCISES:
    app.include_router(ex.router, prefix=f"/{ex.slug}")
    if ex.static_dir:
        app.mount(
            f"/{ex.slug}/static", StaticFiles(directory=ex.static_dir), name=f"{ex.slug}-static"
        )


@app.get("/")
async def home() -> FileResponse:
    return FileResponse(STATIC_DIR / "home.html")


@app.get("/api/exercises")
async def exercises() -> dict:
    """Landing-page cards and the nav bar's exercise switcher."""
    return {
        "exercises": [
            {
                "slug": ex.slug,
                "title": ex.title,
                "description": ex.description,
                "tags": list(ex.tags),
                "url": f"/{ex.slug}/",
            }
            for ex in EXERCISES
        ]
    }


# Shared assets (base.css, nav.js, landing page) for all exercises.
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
