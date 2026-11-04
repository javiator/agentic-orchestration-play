"""Exercise: human-in-the-loop — risk-tiered approvals that pause the agent.

See agent.py for the loop and world.py for the fake shop and its policy.
"""

from pathlib import Path

from app.core.exercise import Exercise

from .routes import router

EXERCISE = Exercise(
    slug="hitl",
    title="Human-in-the-Loop",
    description=(
        "A support agent works on a fake shop on its own — until it wants to refund a lot "
        "or email a customer. Then it pauses for you to approve, edit or reject, and adapts."
    ),
    router=router,
    static_dir=Path(__file__).parent / "static",
    tags=("approvals", "risk tiers", "interrupt & resume", "feedback"),
    order=45,
)
