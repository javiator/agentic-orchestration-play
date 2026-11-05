"""Self-test: unit checks + a dry run and a full build against the bundled test app.

    python3 scripts/selftest.py            # in place (uses the normal cache)
    python3 scripts/selftest.py --fresh    # copy the skill to a temp dir with an EMPTY
                                           # cache and run there: proves the folder is
                                           # self-contained and first-run setup works
    python3 scripts/selftest.py --keep     # keep the output folder (prints its path)

Exits non-zero on the first failed check.
"""

from __future__ import annotations

import functools
import http.server
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
from pathlib import Path

HERE = Path(__file__).resolve().parent
SKILL = HERE.parent
sys.path.insert(0, str(HERE))


def check(cond: bool, what: str) -> None:
    print(f"  {'ok ' if cond else 'FAIL'} {what}", flush=True)
    if not cond:
        raise SystemExit(1)


def fresh() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="demo-video-fresh-"))
    copy = tmp / "demo-video"
    shutil.copytree(SKILL, copy, ignore=shutil.ignore_patterns("__pycache__"))
    env = {**os.environ, "DEMO_VIDEO_CACHE": str(tmp / "cache")}
    env.pop("DEMO_VIDEO_NO_REEXEC", None)
    print(f"[fresh] skill copied to {copy}; empty cache {tmp / 'cache'}", flush=True)
    py = shutil.which("python3") or sys.executable  # a plain interpreter, not the skill venv
    code = subprocess.run([py, str(copy / "scripts" / "selftest.py")], env=env, cwd=tmp).returncode
    shutil.rmtree(tmp, ignore_errors=True)
    return code


def unit() -> None:
    print("unit checks")
    from assemble import chunks, remap, segments
    from narrate import speech_text
    from record import _apply, _tokenize

    check(speech_text("Saved -43% in 6/6 runs, 3× faster → done") ==
          "Saved minus 43% in 6 out of 6 runs, 3 times faster to done", "speech clean-up")
    toks = _tokenize("a {js:[1].map(x => {return x})} {if:#x}y{else}z{end} {{lit}}")
    check([t for t in toks if t[0] == "tag"] == [("tag", "js:[1].map(x => {return x})"), ("tag", "if:#x"),
                                                  ("tag", "else"), ("tag", "end")], "template tokenizer (nested braces)")
    check(speech_text("It costs $1.10, e.g. vs ~5 calls") == "It costs 1.10 dollars, for example, versus about 5 calls",
          "speech clean-up: money, abbreviations")
    check(_apply("Result: 2/6 (detail)", ["before:(", "num"]) == "2", "filters before + num")
    check(_apply("One. Two three four five.", ["max:12"]) == "One.", "max cuts at a sentence end")
    check(_apply("a — b", ["replace: — =>, "]) == "a, b", "replace filter")
    check(all(len(c) <= 84 for c in chunks("word " * 60)), "subtitle chunks ≤ 84 chars")
    segs = segments(30.0, [(5.0, 25.0)])
    check(abs(remap(30.0, segs) - 12.5) < 1e-6, "fast-forward: 20 s wait → 2.5 s")


def preflight() -> None:
    print("preflight")
    from common import SetupError, check_ffmpeg
    path = os.environ["PATH"]
    os.environ["PATH"] = "/nonexistent"
    try:
        check_ffmpeg()
        ok = False
    except SetupError as exc:
        ok = "Install ffmpeg" in str(exc)
    finally:
        os.environ["PATH"] = path
    check(ok, "missing ffmpeg → clear error")
    check_ffmpeg()
    check(True, "ffmpeg with libass present")


def serve() -> tuple[str, http.server.HTTPServer]:
    handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(SKILL / "examples" / "testapp"))
    handler.log_message = lambda *a: None
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return f"http://127.0.0.1:{server.server_address[1]}", server


EXPECTED_PROBLEMS = ("template text empty: #nope", "highlight not found: #missing", "wait timed out (2s): js:false")


def build(base: str, out: Path, *extra: str) -> subprocess.CompletedProcess:
    cmd = [sys.executable, str(HERE / "build.py"), str(SKILL / "examples" / "selftest.yaml"),
           "--base", base, "--out", str(out), *extra]
    res = subprocess.run(cmd, capture_output=True, text=True)
    if res.returncode not in (0, 1):
        print(res.stdout[-3000:], res.stderr[-3000:])
    return res


def e2e(out: Path) -> None:
    base, server = serve()
    try:
        print("dry run")
        res = build(base, out, "--dry-run")
        shots = sorted((out / "selftest" / "shots").glob("*.png"))
        check(len([s for s in shots if s.name.startswith("step")]) == 5 and len(shots) == 9,
              f"5 step screenshots + 4 slide PNGs ({len(shots)} files)")
        for p in EXPECTED_PROBLEMS:
            check(p in res.stdout, f"dry run reports: {p}")
        check("Sample second sample, size large, agreed." in res.stdout, "actions + js/if templating")

        print("full build")
        res = build(base, out)
        mp4 = out / "selftest.mp4"
        check(mp4.exists(), "MP4 written")
        probe = json.loads(subprocess.run(["ffprobe", "-v", "error", "-show_streams", "-of", "json", str(mp4)],
                                          capture_output=True, text=True).stdout)["streams"]
        video = next(s for s in probe if s["codec_type"] == "video")
        check(video["codec_name"] == "h264" and (video["width"], video["height"]) == (1152, 720), "H.264 1152×720")
        check(any(s["codec_type"] == "audio" for s in probe), "audio track")
        check(mp4.stat().st_size < 15e6, f"{mp4.stat().st_size / 1e6:.1f} MB < 15 MB")
        check((out / "selftest.srt").read_text().count("-->") >= 8, "soft subtitles (.srt)")
        tl = json.loads((out / "selftest" / "timeline.json").read_text())
        texts = [line["text"] for line in tl["lines"]]
        result = next((t for t in texts if t.startswith("The result is visible")), "")
        check('Score: -43% on "second sample" after 6/6 runs, number -43, 3 samples, 1 checked.' in result,
              "result read from the page after the wait")
        check(result.endswith("Line one without punctuation. Line two."), "line breaks read as sentences")
        check(all(p in tl["problems"] for p in EXPECTED_PROBLEMS), "timeline.json lists the expected problems")
        unexpected = [p for p in tl["problems"] if p not in EXPECTED_PROBLEMS and not p.startswith("video is")]
        check(not unexpected, f"no unexpected problems {unexpected or ''}")
        times = [line["t"] for line in tl["lines"]]
        check(times == sorted(times) and times[-1] < tl["duration"], "narration times increase, inside the video")
        wait_line = texts.index("Run, and wait for the result.")
        check(times[wait_line + 1] - times[wait_line] < 9, "~12 s wait fast-forwarded")
        chapters = [c["title"] for c in tl["chapters"]]
        check(chapters[:3] == ["Self-test", "Boxes", "Diagram"] and "Result" in chapters, "chapters")

        print("reuse")
        before = mp4.stat().st_mtime
        res = build(base, out, "--reuse")
        check("recording selftest" not in res.stdout and mp4.stat().st_mtime > before, "--reuse re-assembles only")

        print("espeak fallback")
        if shutil.which("espeak-ng") or shutil.which("espeak"):
            res = build(base, out / "espeak", "--tts", "espeak")
            check((out / "espeak" / "selftest.mp4").exists(), "build with --tts espeak")
        else:
            from common import SetupError
            from narrate import Espeak
            try:
                Espeak()
                ok = False
            except SetupError as exc:
                ok = "espeak-ng" in str(exc)
            check(ok, "espeak-ng not installed here → clear error (fallback itself not exercised)")
    finally:
        server.shutdown()


def main() -> int:
    if "--fresh" in sys.argv:
        return fresh()
    from setup import ensure_env
    ensure_env()
    unit()
    preflight()
    out = Path(tempfile.mkdtemp(prefix="demo-video-selftest-"))
    try:
        e2e(out)
    finally:
        if "--keep" in sys.argv:
            print(f"output kept in {out}")
        else:
            shutil.rmtree(out, ignore_errors=True)
    print("all checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
