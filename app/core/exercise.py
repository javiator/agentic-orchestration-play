"""The exercise contract: what a folder under app/exercises/ must expose.

Each exercise is a self-contained learning module (its own routes, pipeline
and UI) that shares the core building blocks in app/core/ — model config,
the ReAct tool loop, tools, file parsing. The root app (app/main.py) finds
every exercise package automatically and mounts it at /<slug>/, so adding
an exercise never requires touching the root app or start.sh.
"""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path

from fastapi import APIRouter


@dataclass(frozen=True)
class Exercise:
    """Exported as `EXERCISE` from an exercise package's __init__.py."""

    slug: str              # URL segment: the exercise is served at /<slug>/
    title: str             # card heading on the landing page + switcher label
    description: str       # one or two sentences shown on the landing card
    router: APIRouter      # routes relative to the exercise root ("/" = its page)
    static_dir: Path | None = None  # served at /<slug>/static/ when set
    tags: tuple[str, ...] = ()      # concepts covered, shown as chips
    order: int = 100       # landing-page position (lower first, then title)
    # Optional headless entry point: question in, final answer text out, no
    # UI. Lets other code (e.g. an eval harness) run any exercise uniformly.
    run: Callable[[str], Awaitable[str]] | None = None
    # More headless entry points, as (name, run) pairs, for exercises that
    # compare variants of one pattern (e.g. plain vs agentic RAG). Each is
    # listed as its own eval target, labelled "<title> (<name>)".
    variants: tuple[tuple[str, Callable[[str], Awaitable[str]]], ...] = ()
