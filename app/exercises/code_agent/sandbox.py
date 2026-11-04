"""Runs model-written Python in a separate, limited process.

    code ──▶ workdir/step_N.py ──▶ python -I step_N.py   (new process, new session)
                                     │  cwd      = a fresh temp dir with copies of data/
                                     │  env      = PATH only — NO API keys from .env
                                     │  limits   = CPU seconds, memory, file size, processes
                                     │  timeout  = wall clock; the whole process group is killed
                                     ▼
                    exit code + stdout + stderr (truncated) ──▶ back to the model

The defences, and what they are NOT:
  * a fresh process per run: a crash or infinite loop can't take the server
    down, and nothing (variables, imports) survives between runs — files
    written to the workdir do;
  * a stripped environment: the code can't read your provider keys;
  * resource limits (POSIX only) and a wall-clock timeout.
This is NOT a security boundary. The code still runs as your user and can
read your files and reach the network. Fine for a local playground with
models you chose; for untrusted users run it in a container or microVM
(Docker with --network=none, gVisor, Firecracker, or a hosted sandbox) —
`run_python()` is the one function to swap.
"""

import asyncio
import os
import shutil
import signal
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path

DATA_DIR = Path(__file__).parent / "data"
CODE_TIMEOUT = float(os.getenv("CODE_TIMEOUT", "10"))
CODE_MEMORY_MB = int(os.getenv("CODE_MEMORY_MB", "512"))
MAX_OUTPUT_CHARS = 4000  # per stream; long output is cut in the middle


@dataclass
class ExecResult:
    exit_code: int | None  # None = killed by the timeout
    stdout: str
    stderr: str
    duration: float
    timed_out: bool

    @property
    def ok(self) -> bool:
        return self.exit_code == 0

    def for_model(self) -> str:
        """The observation the model sees after running its code."""
        status = (
            f"TIMED OUT after {CODE_TIMEOUT:g}s (process killed)" if self.timed_out
            else f"exit code {self.exit_code}"
        )
        parts = [status]
        parts.append(f"stdout:\n{self.stdout}" if self.stdout else "stdout: (empty — print() what you need to see)")
        if self.stderr:
            parts.append(f"stderr:\n{self.stderr}")
        return "\n".join(parts)


def _truncate(text: str) -> str:
    if len(text) <= MAX_OUTPUT_CHARS:
        return text
    half = MAX_OUTPUT_CHARS // 2
    return f"{text[:half]}\n… [{len(text) - MAX_OUTPUT_CHARS} chars cut] …\n{text[-half:]}"


def _limit_resources() -> None:  # runs in the child, just before python starts
    try:
        import resource
    except ImportError:  # Windows: timeout only
        return
    mem = CODE_MEMORY_MB * 1024 * 1024
    cpu = int(CODE_TIMEOUT) + 1
    for limit, value in (
        (resource.RLIMIT_CPU, cpu),
        (resource.RLIMIT_AS, mem),
        (resource.RLIMIT_FSIZE, 20 * 1024 * 1024),  # no file over 20 MB
        (resource.RLIMIT_NPROC, 64),                 # no fork bombs
    ):
        try:
            resource.setrlimit(limit, (value, value))
        except (ValueError, OSError):
            pass


class Workspace:
    """A temp directory holding the data files, one per agent run."""

    def __init__(self) -> None:
        self.path = Path(tempfile.mkdtemp(prefix="code-agent-"))
        for f in DATA_DIR.iterdir():
            if f.is_file():
                shutil.copy(f, self.path / f.name)
        self._runs = 0

    def files(self) -> list[str]:
        return sorted(p.name for p in self.path.iterdir() if not p.name.startswith("step_"))

    async def run_python(self, code: str) -> ExecResult:
        self._runs += 1
        script = self.path / f"step_{self._runs}.py"
        script.write_text(code)
        posix = os.name == "posix"
        started = time.perf_counter()
        proc = await asyncio.create_subprocess_exec(
            sys.executable, "-I", script.name,  # -I: ignore PYTHON* env vars + user site-packages
            cwd=self.path,
            env={"PATH": os.environ.get("PATH", ""), "PYTHONIOENCODING": "utf-8", "HOME": str(self.path)},
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            start_new_session=posix,  # own process group, so the timeout kills children too
            preexec_fn=_limit_resources if posix else None,
        )
        timed_out = False
        try:
            out, err = await asyncio.wait_for(proc.communicate(), timeout=CODE_TIMEOUT)
        except asyncio.TimeoutError:
            timed_out = True
            try:
                os.killpg(proc.pid, signal.SIGKILL) if posix else proc.kill()
            except ProcessLookupError:
                pass
            out, err = await proc.communicate()
        return ExecResult(
            exit_code=None if timed_out else proc.returncode,
            stdout=_truncate(out.decode("utf-8", "replace").strip()),
            stderr=_truncate(err.decode("utf-8", "replace").strip()),
            duration=round(time.perf_counter() - started, 2),
            timed_out=timed_out,
        )

    def cleanup(self) -> None:
        shutil.rmtree(self.path, ignore_errors=True)
