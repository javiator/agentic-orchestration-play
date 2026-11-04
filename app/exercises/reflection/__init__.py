"""Exercise: reflection — generate, critique, revise, keep the best.

See orchestrator.py for the loop, the stop rules and what to watch.
"""

from pathlib import Path

from app.core.exercise import Exercise

from .routes import router, run_headless

EXERCISE = Exercise(
    slug="reflection",
    title="Reflection Loop",
    description=(
        "A writer drafts, a critic scores it and lists concrete issues, the writer revises — "
        "round after round until it's good enough, stops improving, or runs out of budget."
    ),
    router=router,
    static_dir=Path(__file__).parent / "static",
    tags=("self-critique", "iterative refinement", "stop conditions", "diminishing returns"),
    order=35,
    run=run_headless,
)
