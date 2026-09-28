"""Model configuration loaded entirely from .env.

The whole point of the provider-agnostic layer: models are *data*, not code.
MODEL_1..MODEL_N and JUDGE_MODEL are LiteLLM model strings; LiteLLM routes
each one to the right provider SDK based on its prefix (openai/, anthropic/,
gemini/, ollama_chat/, ...). Swapping a provider = editing .env, nothing else.
"""

import os
from dataclasses import dataclass

from dotenv import load_dotenv

load_dotenv()


@dataclass(frozen=True)
class ModelSpec:
    """One agent's model: the LiteLLM model string plus optional endpoint
    overrides so OpenAI-compatible gateways (Ollama cloud, OpenRouter, vLLM)
    can be used per-model without global env juggling."""

    name: str          # LiteLLM model string, e.g. "anthropic/claude-sonnet-5"
    api_base: str | None = None
    api_key: str | None = None

    @property
    def label(self) -> str:
        # Human-readable label for the UI: strip the LiteLLM routing prefix.
        return self.name.split("/", 1)[-1]


def _spec(prefix: str) -> ModelSpec | None:
    name = os.getenv(prefix)
    if not name:
        return None
    return ModelSpec(
        name=name.strip(),
        api_base=os.getenv(f"{prefix}_API_BASE") or None,
        api_key=os.getenv(f"{prefix}_API_KEY") or None,
    )


def load_research_models() -> list[ModelSpec]:
    """Collect MODEL_1, MODEL_2, ... until the first gap. Any count >= 1 works;
    3+ from different providers is the intended setup."""
    models: list[ModelSpec] = []
    i = 1
    while True:
        spec = _spec(f"MODEL_{i}")
        if spec is None:
            break
        models.append(spec)
        i += 1
    if not models:
        raise RuntimeError("No models configured. Set MODEL_1 (and MODEL_2, ...) in .env")
    return models


def load_judge_model() -> ModelSpec:
    spec = _spec("JUDGE_MODEL")
    if spec is None:
        raise RuntimeError("JUDGE_MODEL not set in .env")
    return spec


AGENT_TIMEOUT = float(os.getenv("AGENT_TIMEOUT", "120"))
MAX_TOKENS = int(os.getenv("MAX_TOKENS", "6000"))
# Cap on reason→act→observe cycles per agent when tool use is enabled; the
# final iteration withholds tools to force an answer.
MAX_TOOL_ITERATIONS = int(os.getenv("MAX_TOOL_ITERATIONS", "6"))
