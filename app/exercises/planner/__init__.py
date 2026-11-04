"""Exercise: planner–executor — plan a task graph, run it in parallel waves,
replan on failure. See orchestrator.py.
"""

from pathlib import Path

from app.core.exercise import Exercise

from .routes import router, run_headless

EXERCISE = Exercise(
    slug="planner",
    title="Planner–Executor",
    description=(
        "A planner turns the goal into a graph of steps up front; workers run independent "
        "steps in parallel; a failed step triggers a replan of only the remaining work."
    ),
    router=router,
    static_dir=Path(__file__).parent / "static",
    tags=("planning", "task graph (DAG)", "parallel execution", "replanning"),
    order=22,
    run=run_headless,
)
