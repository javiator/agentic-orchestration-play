"""Exercise: plain RAG vs agentic RAG, side by side.

The same question, model, documents and search run through two strategies:
a fixed retrieve-then-answer pipeline, and an agent that uses search as a
tool (decomposes, rephrases, reads documents, checks its own grounding).
See pipelines.py for the pattern and retrieval.py for the search layer.
"""

from pathlib import Path

from app.core.exercise import Exercise

from .routes import router, run_agentic_headless, run_plain_headless

EXERCISE = Exercise(
    slug="rag",
    title="Plain RAG vs Agentic RAG",
    description=(
        "One question, two retrieval strategies over the same documents: a single "
        "search-then-answer pipeline vs an agent that plans searches, follows leads "
        "and checks its answer is grounded."
    ),
    router=router,
    static_dir=Path(__file__).parent / "static",
    tags=("retrieval", "multi-hop", "query rewriting", "grounding check"),
    order=30,
    variants=(("plain", run_plain_headless), ("agentic", run_agentic_headless)),
)
