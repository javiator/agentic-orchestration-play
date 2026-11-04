"""Exercise: guardrails — layered input/output checks around a support bot.

See guards.py for the checks and orchestrator.py for the guarded pipeline.
"""

from pathlib import Path

from app.core.exercise import Exercise

from .routes import _headless, router

EXERCISE = Exercise(
    slug="guardrails",
    title="Guardrails",
    description=(
        "The same support bot (with a secret in its prompt) with and without layered "
        "guardrails: PII redaction, injection patterns, an input classifier, secret-leak "
        "and output-policy checks — against a set of attacks."
    ),
    router=router,
    static_dir=Path(__file__).parent / "static",
    tags=("prompt injection", "PII", "defence in depth", "false positives"),
    order=50,
    variants=(("guarded", _headless("guarded")), ("unguarded", _headless("unguarded"))),
)
