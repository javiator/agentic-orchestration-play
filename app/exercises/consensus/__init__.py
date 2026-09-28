"""Exercise: multi-model research consensus.

One query fans out to N models in parallel, a judge model merges the answers
and flags disagreements. Toggleable layers: tool use (ReAct loop), a rebuttal
round, and memory (session follow-ups + long-term SQLite recall).
"""

from pathlib import Path

from app.core.exercise import Exercise

from .routes import router

EXERCISE = Exercise(
    slug="consensus",
    title="Multi-Model Research Consensus",
    description=(
        "One question → independent answers from several models → a judge merges "
        "them and flags disagreements. Optional tool use, rebuttal round, and memory."
    ),
    router=router,
    static_dir=Path(__file__).parent / "static",
    tags=("fan-out", "LLM judge", "ReAct tools", "rebuttal", "memory"),
    order=10,
)
