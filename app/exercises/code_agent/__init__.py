"""Exercise: a code-executing agent.

The model answers by writing Python, running it in a limited subprocess and
reading the output (or the traceback) until it can answer. See agent.py for
the loop and sandbox.py for what the sandbox does — and doesn't — protect.
"""

from pathlib import Path

from app.core.exercise import Exercise

from .routes import router, run_headless

EXERCISE = Exercise(
    slug="code-agent",
    title="Code-Executing Agent",
    description=(
        "The model writes Python, runs it in a sandboxed process, reads the output or "
        "traceback and fixes its code — exact answers instead of plausible guesses."
    ),
    router=router,
    static_dir=Path(__file__).parent / "static",
    tags=("code execution", "self-correction", "sandboxing", "ReAct"),
    order=40,
    run=run_headless,
)
