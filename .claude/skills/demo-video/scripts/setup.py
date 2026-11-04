"""First-run setup: the skill's own venv (outside the skill folder).

    python3 scripts/setup.py          # create/refresh the venv, print its python

build.py, hub.py and explore.py call ensure_env() before importing anything
third-party: if the current interpreter lacks the requirements, they re-exec
themselves under the cache venv (creating it the first time). Only the
standard library may be imported above that call.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import CACHE, SKILL  # noqa: E402

VENV = CACHE / "venv"
VENV_PY = VENV / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
REQS = SKILL / "requirements.txt"


def _have_requirements() -> bool:
    try:
        import playwright  # noqa: F401
        import yaml  # noqa: F401
    except ImportError:
        return False
    return True


def _pip(args: list[str]) -> int:
    uv = shutil.which("uv")
    cmd = [uv, "pip", "install", "--python", str(VENV_PY), *args] if uv else [str(VENV_PY), "-m", "pip", "install", "-q", *args]
    return subprocess.run(cmd).returncode


def create_venv() -> Path:
    marker = VENV / ".requirements"
    wanted = REQS.read_text()
    if VENV_PY.exists() and marker.exists() and marker.read_text() == wanted:
        return VENV_PY
    print(f"[demo-video] setting up {VENV} (one time)…", flush=True)
    CACHE.mkdir(parents=True, exist_ok=True)
    if not VENV_PY.exists():
        uv = shutil.which("uv")
        subprocess.run([uv, "venv", "-q", str(VENV)] if uv else [sys.executable, "-m", "venv", str(VENV)], check=True)
    if _pip(["-r", str(REQS)]):
        # Piper has native wheels that may not exist for every platform: keep
        # going without it (narration falls back to espeak-ng).
        print("[demo-video] full install failed; installing without piper-tts (espeak-ng fallback)", flush=True)
        rest = [ln for ln in wanted.splitlines() if ln.strip() and not ln.startswith("piper")]
        if _pip(rest):
            raise SystemExit("[demo-video] could not install requirements into the venv")
    marker.write_text(wanted)
    return VENV_PY


def ensure_env() -> None:
    """Re-exec the calling script under the skill venv unless deps are already importable."""
    if _have_requirements() or os.getenv("DEMO_VIDEO_NO_REEXEC"):
        return
    py = create_venv()
    os.environ["DEMO_VIDEO_NO_REEXEC"] = "1"
    os.execv(str(py), [str(py), *sys.argv])


if __name__ == "__main__":
    print(create_venv())
