"""The specialist team the supervisor can delegate to.

Each specialist is just a system prompt + (optionally) the research tools +
a model slot. Specialists never talk to each other directly: they see the
user's goal, the shared scratchpad of finished work, and the one task the
supervisor gave them. To add a specialist, append to SPECIALISTS — the
supervisor's `delegate` tool and prompt are generated from this list.
"""

from dataclasses import dataclass

from app.core.config import ModelSpec


@dataclass(frozen=True)
class Specialist:
    name: str
    role: str          # one line the supervisor reads when choosing who acts
    system: str        # the specialist's own instructions
    uses_tools: bool   # True → runs as a ReAct loop with web search / fetch
    model_slot: int    # which MODEL_N it runs on (0 = MODEL_1), wrapping around


SPECIALISTS = [
    Specialist(
        name="researcher",
        role="Finds facts and sources on the web (search + read pages). Use for anything "
        "factual, recent, or that needs evidence.",
        system=(
            "You are the researcher on a team. Do the research task you are given using "
            "your web tools, then report concise findings as bullet points, each with its "
            "source URL. Report only what you found; say clearly what you could not verify. "
            "Do not write the final answer for the user — another specialist does that."
        ),
        uses_tools=True,
        model_slot=0,
    ),
    Specialist(
        name="analyst",
        role="Reasons over material already on the scratchpad: compares options, checks "
        "consistency, quantifies, spots gaps or contradictions. No web access.",
        system=(
            "You are the analyst on a team. Work only from the goal and the scratchpad of "
            "your teammates' findings. Do the analysis task you are given: compare, weigh, "
            "calculate, and flag contradictions or gaps explicitly. Be structured and "
            "concise. Do not invent facts that are not on the scratchpad."
        ),
        uses_tools=False,
        model_slot=1,
    ),
    Specialist(
        name="writer",
        role="Turns the scratchpad into a clear, well-structured final answer for the user.",
        system=(
            "You are the writer on a team. Using only the goal and the scratchpad, write "
            "the deliverable you are asked for: clear, well organized, directly answering "
            "the user's goal, citing sources from the scratchpad where relevant. Output "
            "only the deliverable itself."
        ),
        uses_tools=False,
        model_slot=2,
    ),
]

BY_NAME = {s.name: s for s in SPECIALISTS}


def model_for(specialist: Specialist, models: list[ModelSpec]) -> ModelSpec:
    """Map a specialist to MODEL_N. With 3+ models each specialist gets its
    own provider — handoffs then cross model boundaries, which is the point."""
    return models[specialist.model_slot % len(models)]
