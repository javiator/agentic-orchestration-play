"""Assembly with ffmpeg: slide clips + the demo clip → one MP4 (1152×720,
H.264, CRF 28) with the narration burned in as subtitles, plus a soft .srt.

    slide_clip    still PNG + its narration WAV → short clip
    demo_clip     recording cut into normal / fast-forward segments; narration
                  laid on top at its (remapped) times
    write_srt     cues → SRT, long lines split at punctuation and timed by length
    concat_final  all clips → final MP4 with burned-in subtitles
"""

from __future__ import annotations

import re
from pathlib import Path

from common import FPS, OUT_H, OUT_W, run

# Final audio: cut rumble, even out levels gently, broadcast loudness (-16 LUFS), then a
# limiter so peaks stay below -1.5 dBFS after AAC encoding (single-pass loudnorm can overshoot).
AUDIO_POLISH = ("highpass=f=70,acompressor=threshold=-21dB:ratio=2:attack=5:release=120:makeup=1,"
                "loudnorm=I=-16:TP=-1.5:LRA=11,alimiter=limit=0.8:attack=2:release=50:level=false")

ENC_FAST = ["-c:v", "libx264", "-preset", "veryfast", "-crf", "16", "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-b:a", "128k", "-ar", "48000", "-ac", "2"]


def slide_clip(png: Path, wav: Path | None, seconds: float, out: Path) -> None:
    cmd = ["ffmpeg", "-y", "-loop", "1", "-framerate", str(FPS), "-t", f"{seconds:.3f}", "-i", str(png)]
    if wav:
        cmd += ["-i", str(wav), "-filter_complex", "[1:a]adelay=400|400,apad[a]", "-map", "0:v", "-map", "[a]"]
    else:
        cmd += ["-f", "lavfi", "-t", f"{seconds:.3f}", "-i", "anullsrc=r=48000:cl=stereo"]
    cmd += ["-vf", f"scale={OUT_W}:{OUT_H}:flags=lanczos,fps={FPS}", "-t", f"{seconds:.3f}", *ENC_FAST, str(out)]
    run(cmd)


def segments(duration: float, fast: list, max_ff_seconds: float = 2.5) -> list[tuple[float, float, float]]:
    """(start, end, speed) covering the recording; each fast span plays in ≤ max_ff_seconds
    (or its own third value, e.g. 0.2 s for TTS pauses that should vanish)."""
    segs, cursor = [], 0.0
    for span in sorted(fast):
        a, b = span[0], span[1]
        target = span[2] if len(span) > 2 else max_ff_seconds
        if a <= cursor or b - a < 1:
            continue
        segs.append((cursor, a, 1.0))
        segs.append((a, b, max(1.0, (b - a) / target)))
        cursor = b
    segs.append((cursor, duration, 1.0))
    return segs


def remap(t: float, segs: list[tuple[float, float, float]]) -> float:
    """Recording time → time in the fast-forwarded clip."""
    acc = 0.0
    for a, b, speed in segs:
        if t <= b:
            return acc + (max(t, a) - a) / speed
        acc += (b - a) / speed
    return acc


def demo_clip(timeline: dict, out: Path, max_ff_seconds: float = 2.5) -> tuple[float, list[dict]]:
    """Cut the recording into normal/fast segments, lay narration on top.
    Returns (clip duration, narration with times mapped into the clip)."""
    dur = timeline["duration"]
    segs = segments(dur, timeline["fast"], max_ff_seconds)
    total = remap(dur, segs)
    parts, labels = [], []
    for i, (a, b, speed) in enumerate(segs):
        parts.append(f"[0:v]trim={a:.3f}:{b:.3f},setpts=(PTS-STARTPTS)/{speed:.4f}[v{i}]")
        labels.append(f"[v{i}]")
    graph = ";".join(parts) + f";{''.join(labels)}concat=n={len(segs)}:v=1:a=0,scale={OUT_W}:{OUT_H}:flags=lanczos,fps={FPS}[v]"
    narr = [{**n, "t": remap(n["t"], segs)} for n in timeline["narration"] if n.get("wav")]
    inputs = ["-i", timeline["webm"]]
    for n in narr:
        inputs += ["-i", n["wav"]]
    if narr:
        mix = "".join(f"[{i + 1}:a]adelay={int(n['t'] * 1000)}|{int(n['t'] * 1000)}[a{i}];" for i, n in enumerate(narr))
        mix += "".join(f"[a{i}]" for i in range(len(narr))) + f"amix=inputs={len(narr)}:normalize=0,apad[a]"
        graph += ";" + mix
    else:
        inputs += ["-f", "lavfi", "-i", "anullsrc=r=48000:cl=stereo"]
        graph += ";[1:a]anull[a]"
    run(["ffmpeg", "-y", *inputs, "-filter_complex", graph, "-map", "[v]", "-map", "[a]",
         "-t", f"{total:.3f}", *ENC_FAST, str(out)])
    return total, narr


def chunks(text: str, limit: int = 84) -> list[str]:
    """Split a narration line into subtitle-sized pieces, preferring sentence ends."""
    words, lines, cur = text.split(), [], ""
    for w in words:
        if cur and len(cur) + 1 + len(w) > limit:
            lines.append(cur)
            cur = w
        else:
            cur = f"{cur} {w}".strip()
        if re.search(r"[.!?:;]$", w) and len(cur) > limit * 0.55:
            lines.append(cur)
            cur = ""
    if cur:
        lines.append(cur)
    return lines


def _ts(t: float) -> str:
    ms = int(round(t * 1000))
    return f"{ms // 3600000:02d}:{ms // 60000 % 60:02d}:{ms // 1000 % 60:02d},{ms % 1000:03d}"


def write_srt(cues: list[dict], path: Path) -> None:
    """cues: {t, dur, text} — each split into short lines, timed by length."""
    out, n = [], 1
    for cue in cues:
        lines = chunks(cue["text"])
        total_chars = sum(len(x) for x in lines) or 1
        t = cue["t"]
        for line in lines:
            d = cue["dur"] * len(line) / total_chars
            out.append(f"{n}\n{_ts(t)} --> {_ts(t + d)}\n{line}\n")
            n += 1
            t += d
    path.write_text("\n".join(out))


def concat_final(parts: list[Path], srt: Path, out: Path, crf: int = 28) -> None:
    listing = out.with_suffix(".txt")
    listing.write_text("".join(f"file '{p.resolve()}'\n" for p in parts))
    style = ("FontName=Inter,FontSize=11,PrimaryColour=&H00FFFFFF,BackColour=&H99000000,"
             "BorderStyle=4,Outline=0,Shadow=0,MarginV=14,MarginL=40,MarginR=40")
    # Escape for the filter graph: ":" separates options, "'" quotes, "," and ";" separate filters.
    srt_arg = re.sub(r"([\\:',;\[\]])", r"\\\1", str(srt.resolve()))
    try:
        run(["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(listing),
             "-vf", f"subtitles={srt_arg}:force_style='{style}'",
             "-c:v", "libx264", "-preset", "slow", "-crf", str(crf), "-pix_fmt", "yuv420p", "-movflags", "+faststart",
             "-af", AUDIO_POLISH, "-c:a", "aac", "-b:a", "96k", "-ac", "1", "-ar", "48000", str(out)])
    finally:
        listing.unlink(missing_ok=True)
