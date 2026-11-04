"""Narration: text → WAV, through a pluggable TTS backend, cached per line.

Backends (pick with --tts / DEMO_VIDEO_TTS; default "auto" = piper, else espeak):

    piper    Piper neural TTS, offline. Voice downloaded once into the cache.
    espeak   espeak-ng command line: robotic, but works anywhere it's installed.

Adding a backend (e.g. ElevenLabs, or "bring your own audio" for sandboxes
with no outbound network): subclass Backend, implement synth(text, wav_path),
give it a unique `id` (it's part of the cache key), register it in BACKENDS.
Nothing else in the pipeline changes — everything downstream only sees WAVs.
"""

from __future__ import annotations

import hashlib
import os
import re
import shutil
import subprocess
import urllib.request
import wave
from pathlib import Path

from common import CACHE, SetupError

PIPER_VOICE = os.getenv("DEMO_VIDEO_VOICE", "en_US-lessac-medium")
PIPER_VOICES_URL = "https://huggingface.co/rhasspy/piper-voices/resolve/main/"


def speech_text(text: str) -> str:
    """Rewrite what TTS engines misread. Subtitles keep the original text."""
    text = re.sub(r"(^|\s)-(\d)", r"\1minus \2", text)  # Piper reads "-43%" as "43%"
    text = re.sub(r"(\d)/(\d)", r"\1 out of \2", text)
    return text.replace("×", " times").replace("→", " to ")


class Backend:
    id = "base"

    def synth(self, text: str, wav: Path) -> None:  # pragma: no cover - interface
        raise NotImplementedError


class Piper(Backend):
    def __init__(self, voice: str = PIPER_VOICE, length_scale: float = 1.05):
        from piper import PiperVoice, SynthesisConfig  # ImportError → caller falls back

        path = self._download(voice)
        self.voice = PiperVoice.load(str(path))
        self.config = SynthesisConfig(length_scale=length_scale)
        self.id = f"piper:{voice}:{length_scale}"

    @staticmethod
    def _download(voice: str) -> Path:
        lang, name, quality = voice.split("-")[0], voice.split("-")[1], voice.split("-")[-1]
        folder = CACHE / "voices"
        folder.mkdir(parents=True, exist_ok=True)
        for suffix in (".onnx", ".onnx.json"):
            target = folder / f"{voice}{suffix}"
            if not target.exists():
                url = f"{PIPER_VOICES_URL}{lang.split('_')[0]}/{lang}/{name}/{quality}/{target.name}"
                print(f"[demo-video] downloading voice {target.name}…", flush=True)
                tmp = target.with_suffix(target.suffix + ".part")
                urllib.request.urlretrieve(url, tmp)
                tmp.rename(target)
        return folder / f"{voice}.onnx"

    def synth(self, text: str, wav: Path) -> None:
        with wave.open(str(wav), "wb") as wf:
            self.voice.synthesize_wav(text, wf, syn_config=self.config)


class Espeak(Backend):
    def __init__(self, voice: str = "en-us", speed: int = 165):
        self.exe = shutil.which("espeak-ng") or shutil.which("espeak")
        if not self.exe:
            raise SetupError("No TTS available: piper-tts isn't installed and espeak-ng isn't on PATH "
                             "(apt install espeak-ng | brew install espeak-ng).")
        self.args = ["-v", voice, "-s", str(speed)]
        self.id = f"espeak:{voice}:{speed}"

    def synth(self, text: str, wav: Path) -> None:
        subprocess.run([self.exe, *self.args, "-w", str(wav), text], check=True, capture_output=True)


BACKENDS = {"piper": Piper, "espeak": Espeak}


def make_backend(name: str = "auto") -> Backend:
    name = name or "auto"
    if name != "auto":
        return BACKENDS[name]()
    try:
        return Piper()
    except ImportError:
        print("[demo-video] piper-tts not available; narrating with espeak-ng", flush=True)
        return Espeak()


class Narrator:
    def __init__(self, backend: str | Backend = "auto"):
        self.backend = backend if isinstance(backend, Backend) else make_backend(backend)
        self.cache = CACHE / "tts"
        self.cache.mkdir(parents=True, exist_ok=True)

    def say(self, text: str) -> tuple[Path, float]:
        """Text → (wav path, seconds). Cached by backend + content."""
        spoken = speech_text(text)
        key = hashlib.sha1(f"{self.backend.id}|{spoken}".encode()).hexdigest()[:16]
        path = self.cache / f"{key}.wav"
        if not path.exists():
            tmp = path.with_suffix(".part.wav")
            self.backend.synth(spoken, tmp)
            tmp.rename(path)
        with wave.open(str(path)) as wf:
            return path, wf.getnframes() / wf.getframerate()
