"""Starter for a new exercise — copy this folder, don't edit it in place.

    cp -r app/exercises/_template app/exercises/my_exercise

then set a unique `slug`, a title and a description below. Folders starting
with "_" are skipped by discovery, so this template never shows up itself.
Restart the server and the new exercise appears on the landing page.
"""

from pathlib import Path

from app.core.exercise import Exercise

from .routes import ask_model, router


async def run(question: str) -> str:
    """Headless entry point (optional): question in, answer out."""
    return (await ask_model(question))[1]


EXERCISE = Exercise(
    slug="template",
    title="Single model call (template)",
    description="Minimal starter: send a prompt to MODEL_1 and show the reply.",
    router=router,
    static_dir=Path(__file__).parent / "static",
    tags=("starter",),
    run=run,
)
