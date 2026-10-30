"""Exercise: context management — full history vs window vs summary vs notes.

See orchestrator.py for the strategies and conversation.json for the script.
"""

from pathlib import Path

from app.core.exercise import Exercise

from .routes import router

EXERCISE = Exercise(
    slug="context",
    title="Context Management",
    description=(
        "One long conversation replayed four ways — full history, sliding window, running "
        "summary, fact notes — then quizzed on facts from early, mid and late in the chat: "
        "what each strategy remembers, and what it costs."
    ),
    router=router,
    static_dir=Path(__file__).parent / "static",
    tags=("memory", "summarization", "context window", "token cost"),
    order=55,
)
