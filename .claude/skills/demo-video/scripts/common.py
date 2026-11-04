"""Shared paths, process helpers and preflight checks.

Everything the skill writes outside its output folder lives in one cache
directory, OUTSIDE the skill folder, so the skill can be copied anywhere:

    $DEMO_VIDEO_CACHE (default ~/.cache/demo-video)/
        venv/      the skill's own Python environment (setup.py)
        voices/    downloaded TTS voices
        tts/       synthesized narration, cached per sentence
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

SKILL = Path(__file__).resolve().parents[1]          # the demo-video/ folder
CACHE = Path(os.getenv("DEMO_VIDEO_CACHE", Path.home() / ".cache" / "demo-video")).expanduser()

W, H = 1280, 800          # capture size (slides + browser viewport)
OUT_W, OUT_H = 1152, 720  # final video size (same 16:10 aspect)
FPS = 25


class SetupError(RuntimeError):
    """A missing system dependency, with a message that says how to fix it."""


def run(cmd: list[str]) -> None:
    res = subprocess.run(cmd, capture_output=True, text=True)
    if res.returncode:
        raise RuntimeError(f"{cmd[0]} failed:\n{res.stderr[-2000:]}")


def media_duration(path: Path) -> float:
    out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)],
                         capture_output=True, text=True).stdout
    return float(out.strip())


def check_ffmpeg() -> None:
    """ffmpeg + ffprobe on PATH, and ffmpeg built with libass (burned-in subtitles)."""
    for tool in ("ffmpeg", "ffprobe"):
        if not shutil.which(tool):
            raise SetupError(f"{tool} not found on PATH. Install ffmpeg (with libass): "
                             "apt install ffmpeg  |  brew install ffmpeg  |  https://ffmpeg.org/download.html")
    filters = subprocess.run(["ffmpeg", "-hide_banner", "-filters"], capture_output=True, text=True).stdout
    if " subtitles " not in filters:
        raise SetupError("ffmpeg was built without libass (no 'subtitles' filter), so subtitles can't be burned in. "
                         "Install a full build: apt install ffmpeg  |  brew install ffmpeg (or brew install "
                         "homebrew-ffmpeg/ffmpeg/ffmpeg --with-libass).")


# ── browser ──────────────────────────────────────────────────────────────────

def _system_chromium() -> str | None:
    """A Chromium that is already installed, so we never download another one."""
    for env in ("DEMO_VIDEO_CHROMIUM", "CHROME_PATH"):
        if os.getenv(env) and Path(os.environ[env]).exists():
            return os.environ[env]
    for cand in ("/opt/pw-browsers/chromium", "/usr/bin/chromium", "/usr/bin/chromium-browser",
                 "/usr/bin/google-chrome"):
        if Path(cand).exists():
            return cand
    return None


def launch_browser(p):
    """Playwright's bundled Chromium if it matches this Playwright version;
    else a system Chromium; else install Playwright's (once)."""
    try:
        return p.chromium.launch()
    except Exception as first:  # noqa: BLE001 — browser missing or mismatched
        exe = _system_chromium()
        if exe:
            try:
                return p.chromium.launch(executable_path=exe, args=["--no-sandbox"])
            except Exception:  # noqa: BLE001
                pass
        print("  no usable Chromium found; installing Playwright's (one time)…", flush=True)
        import sys
        res = subprocess.run([sys.executable, "-m", "playwright", "install", "chromium"])
        if res.returncode:
            raise SetupError(f"Could not launch or install Chromium: {first}") from first
        return p.chromium.launch()
