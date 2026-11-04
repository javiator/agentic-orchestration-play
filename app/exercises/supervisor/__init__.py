"""Exercise: supervisor + specialists (dynamic handoff).

A supervisor model decides, turn by turn, which specialist (researcher,
analyst, writer) acts next and with what task, until it calls `finish`.
See orchestrator.py for the pattern and specialists.py for the team.
"""

from pathlib import Path

from app.core.exercise import Exercise

from .routes import router, run_headless

EXERCISE = Exercise(
    slug="supervisor",
    title="Supervisor & Specialists",
    description=(
        "A supervisor agent decides at runtime which specialist acts next — researcher, "
        "analyst or writer — passing work through a shared scratchpad until it finishes."
    ),
    router=router,
    static_dir=Path(__file__).parent / "static",
    tags=("agents as tools", "dynamic routing", "shared state", "loop guards"),
    order=20,
    run=run_headless,
)
