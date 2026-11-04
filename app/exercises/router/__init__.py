"""Exercise: router — a cheap model picks the path (and price) per request.

See orchestrator.py for the routes, the escalation rule and the trade-offs.
"""

from pathlib import Path

from app.core.exercise import Exercise

from .routes import router, run_routed_headless, run_strong_headless

EXERCISE = Exercise(
    slug="router",
    title="Router",
    description=(
        "A small model classifies each request and sends it down the cheapest path that "
        "can handle it — small model, strong model, strong model + tools, or decline — "
        "compared against always using the strongest setup."
    ),
    router=router,
    static_dir=Path(__file__).parent / "static",
    tags=("routing", "cost vs quality", "classification", "escalation"),
    order=15,
    variants=(("routed", run_routed_headless), ("always strong", run_strong_headless)),
)
