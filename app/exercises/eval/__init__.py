"""Exercise: evaluation harness.

Runs single-model baselines and every exercise with an `Exercise.run` hook
over a question set, scoring each answer (exact match + LLM grader) and
measuring latency and cost. See harness.py for the method and its caveats.
"""

from pathlib import Path

from app.core.exercise import Exercise

from .routes import router

EXERCISE = Exercise(
    slug="eval",
    title="Eval Harness",
    description=(
        "Score single models vs. orchestration patterns on the same questions — "
        "accuracy, LLM-graded quality, latency and cost side by side."
    ),
    router=router,
    static_dir=Path(__file__).parent / "static",
    tags=("evaluation", "LLM-as-judge", "baselines", "cost vs quality"),
    order=90,
)
