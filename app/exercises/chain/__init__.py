"""Exercise: prompt chain vs single prompt (workflows vs one-shot).

See orchestrator.py for the chain and spec.py for the code-checked spec.
"""

from pathlib import Path

from app.core.exercise import Exercise

from .routes import router

EXERCISE = Exercise(
    slug="chain",
    title="Prompt Chain vs Single Prompt",
    description=(
        "The same writing task with a strict spec, done two ways: one big prompt, or a "
        "fixed chain of small steps (outline → parallel sections → assemble) with code "
        "checks as gates between them."
    ),
    router=router,
    static_dir=Path(__file__).parent / "static",
    tags=("workflows", "gates", "parallel steps", "structured output"),
    order=25,
)
