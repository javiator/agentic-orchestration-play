"""Narration: text → WAV, through a pluggable TTS backend, cached per line.

Backends (pick with --tts / DEMO_VIDEO_TTS / `tts:` in the YAML; voice with
--voice / DEMO_VIDEO_VOICE / `voice:`):

    kokoro   Kokoro-82M (ONNX), offline, the most natural. Model (~350 MB)
             downloaded once into the cache. Voices: af_heart (default),
             af_bella, am_michael, am_fenrir, bm_george, … (see kokoro-onnx).
    piper    Piper neural TTS, offline, light and fast. Voice downloaded once.
             Default en_US-lessac-medium; any rhasspy/piper-voices name works.
    espeak   espeak-ng command line: robotic, but works anywhere it's installed.

"auto" (default) = the first of kokoro, piper, espeak that can be loaded.

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

PIPER_VOICES_URL = "https://huggingface.co/rhasspy/piper-voices/resolve/main/"
KOKORO_URL = "https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0/"
KOKORO_FILES = ("kokoro-v1.0.onnx", "voices-v1.0.bin")

# Written forms TTS engines misread → what to say. Subtitles keep the original.
SPOKEN = [
    (r"(^|[\s(])-(\d)", r"\1minus \2"),            # "-43%" (Piper drops the sign)
    (r"(\d)/(\d)", r"\1 out of \2"),               # "6/6"
    (r"\$(\d+(?:\.\d+)?)", r"\1 dollars"),         # "$1.10"
    (r"\be\.g\.,?", "for example,"), (r"\bi\.e\.,?", "that is,"),
    (r"\bvs\.?(?=\s)", "versus"), (r"\betc\.", "and so on."),
    (r"(\d)\s*×", r"\1 times"), ("×", " times "), ("→", " to "), ("->", " to "),
    (r"~\s*(\d)", r"about \1"), (" & ", " and "), (r"https?://", ""),
    (r"\s+([,.;:!?])", r"\1"), (r"\s{2,}", " "),
]


def speech_text(text: str) -> str:
    """Rewrite what TTS engines misread. Subtitles keep the original text."""
    for pattern, repl in SPOKEN:
        text = re.sub(pattern, repl, text)
    return text.strip()


def _download(url: str, target: Path) -> None:
    if target.exists():
        return
    target.parent.mkdir(parents=True, exist_ok=True)
    print(f"[demo-video] downloading {target.name} (one time)…", flush=True)
    tmp = target.with_name(target.name + ".part")
    urllib.request.urlretrieve(url, tmp)
    tmp.rename(target)


def _write_wav(path: Path, samples, rate: int) -> None:
    import numpy as np
    pcm = (np.clip(samples, -1, 1) * 32767).astype(np.int16)
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(rate)
        wf.writeframes(pcm.tobytes())


class Backend:
    id = "base"

    def synth(self, text: str, wav: Path) -> None:  # pragma: no cover - interface
        raise NotImplementedError


class Kokoro(Backend):
    def __init__(self, voice: str | None = None, speed: float = 1.0):
        from kokoro_onnx import Kokoro as Model  # ImportError → caller falls back

        folder = CACHE / "voices" / "kokoro"
        for name in KOKORO_FILES:
            _download(KOKORO_URL + name, folder / name)
        self.model = Model(str(folder / KOKORO_FILES[0]), str(folder / KOKORO_FILES[1]))
        self.voice = voice or "af_heart"
        if self.voice not in self.model.get_voices():
            raise SetupError(f"unknown Kokoro voice {self.voice!r}; try one of {', '.join(self.model.get_voices()[:12])}…")
        self.lang = "en-gb" if self.voice[:1] == "b" else "en-us"
        self.speed = speed
        self.id = f"kokoro:{self.voice}:{speed}"

    def synth(self, text: str, wav: Path) -> None:
        samples, rate = self.model.create(text, voice=self.voice, speed=self.speed, lang=self.lang)
        _write_wav(wav, samples, rate)


class Piper(Backend):
    def __init__(self, voice: str | None = None, length_scale: float = 1.05):
        from piper import PiperVoice, SynthesisConfig  # ImportError → caller falls back

        voice = voice or "en_US-lessac-medium"
        lang, name, quality = voice.split("-")[0], voice.split("-")[1], voice.split("-")[-1]
        folder = CACHE / "voices"
        for suffix in (".onnx", ".onnx.json"):
            _download(f"{PIPER_VOICES_URL}{lang.split('_')[0]}/{lang}/{name}/{quality}/{voice}{suffix}",
                      folder / f"{voice}{suffix}")
        self.voice = PiperVoice.load(str(folder / f"{voice}.onnx"))
        self.config = SynthesisConfig(length_scale=length_scale)
        self.id = f"piper:{voice}:{length_scale}"

    def synth(self, text: str, wav: Path) -> None:
        with wave.open(str(wav), "wb") as wf:
            self.voice.synthesize_wav(text, wf, syn_config=self.config)


class Espeak(Backend):
    def __init__(self, voice: str | None = None, speed: int = 165):
        self.exe = shutil.which("espeak-ng") or shutil.which("espeak")
        if not self.exe:
            raise SetupError("No TTS available: kokoro-onnx / piper-tts aren't installed and espeak-ng isn't on "
                             "PATH (apt install espeak-ng | brew install espeak-ng).")
        voice = voice or "en-us"
        self.args = ["-v", voice, "-s", str(speed)]
        self.id = f"espeak:{voice}:{speed}"

    def synth(self, text: str, wav: Path) -> None:
        subprocess.run([self.exe, *self.args, "-w", str(wav), text], check=True, capture_output=True)


BACKENDS = {"kokoro": Kokoro, "piper": Piper, "espeak": Espeak}
AUTO_ORDER = ("kokoro", "piper", "espeak")


def _voice_kind(voice: str) -> str:
    """Which backend a voice name belongs to: en_US-lessac-medium → piper,
    af_heart → kokoro, en-us → espeak."""
    if re.fullmatch(r"[a-z]{2}_[A-Z]{2}-[\w]+-(x_low|low|medium|high)", voice):
        return "piper"
    if re.fullmatch(r"[a-z]{2}_[a-z]+", voice):
        return "kokoro"
    return "espeak"


def make_backend(name: str | None = "auto", voice: str | None = None) -> Backend:
    name = name or "auto"
    voice = voice or os.getenv("DEMO_VIDEO_VOICE") or None
    if name != "auto":
        if name not in BACKENDS:
            raise SetupError(f"unknown TTS backend {name!r} (choose from {', '.join(BACKENDS)} or auto)")
        return BACKENDS[name](voice)
    for candidate in AUTO_ORDER:
        try:
            return BACKENDS[candidate](voice if voice and _voice_kind(voice) == candidate else None)
        except ImportError:
            print(f"[demo-video] {candidate} TTS not installed; trying the next backend", flush=True)
    raise SetupError("no TTS backend available")  # Espeak raises its own SetupError first


class Narrator:
    def __init__(self, backend: str | Backend = "auto", voice: str | None = None):
        self.backend = backend if isinstance(backend, Backend) else make_backend(backend, voice)
        self.cache = CACHE / "tts"
        self.cache.mkdir(parents=True, exist_ok=True)

    def say(self, text: str) -> tuple[Path, float]:
        """Text → (wav path, seconds). Cached by backend + content."""
        spoken = speech_text(text)
        key = hashlib.sha1(f"{self.backend.id}|{spoken}".encode()).hexdigest()[:16]
        path = self.cache / f"{key}.wav"
        if not path.exists():
            tmp = path.with_name(path.stem + ".part.wav")
            self.backend.synth(spoken, tmp)
            tmp.rename(path)
        with wave.open(str(path)) as wf:
            return path, wf.getnframes() / wf.getframerate()
