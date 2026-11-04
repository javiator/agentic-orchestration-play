"""Exercise discovery.

Every sub-package of app/exercises/ whose name does not start with "_" is an
exercise and must export `EXERCISE` (see app/core/exercise.py). Packages
starting with "_" (e.g. _template) are skipped — copy _template to a new
folder name to start an exercise.
"""

import importlib
import logging
import pkgutil

from app.core.exercise import Exercise

log = logging.getLogger(__name__)


def discover_exercises() -> list[Exercise]:
    found: list[Exercise] = []
    for mod in pkgutil.iter_modules(__path__):
        if not mod.ispkg or mod.name.startswith("_"):
            continue
        # One broken exercise shouldn't take the others down: log and skip.
        try:
            module = importlib.import_module(f"{__name__}.{mod.name}")
            exercise = getattr(module, "EXERCISE")
        except Exception:
            log.exception("Skipping exercise %r: failed to load", mod.name)
            continue
        if not isinstance(exercise, Exercise):
            log.error("Skipping exercise %r: EXERCISE is not an Exercise", mod.name)
            continue
        found.append(exercise)
    slugs = [e.slug for e in found]
    dupes = {s for s in slugs if slugs.count(s) > 1}
    if dupes:
        raise RuntimeError(f"Duplicate exercise slugs: {sorted(dupes)}")
    return sorted(found, key=lambda e: (e.order, e.title))
