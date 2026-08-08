"""A2 - run a candidate program on one test input under a hard timeout.

ConDefects programs are whole scripts, not importable functions: they read the
test input from stdin and write their answer to stdout. So the sandbox writes
the source to a file and runs `python3 <file>` with stdin connected to the
test input, exactly as AtCoder judged it. It cannot use the parent's stdin as
a control channel (the way a `python3 -c` worker fed a JSON payload would),
because the submissions themselves read stdin every way there is - `input()`,
`sys.stdin.readline`, `sys.stdin.buffer.read()`, `open(0).read()`.

Competitive-programming code recurses deeply and loops hard, so the run is
capped on wall time and given a raised recursion ceiling. The ceiling is
installed through a `sitecustomize.py` dropped next to the program rather than
by editing the source: `src.typer.edit_location` diffs candidates against the
faulty source line by line, and a prepended preamble would shift every line
number in that diff.
"""
from __future__ import annotations

import dataclasses
import os
import pathlib
import re
import subprocess
import sys
import tempfile
import time
from typing import Any

from dotenv import load_dotenv

load_dotenv()

# Higher than the QuixBugs-era default: AtCoder inputs run to ~2*10^5 items and
# a *correct* CPython solution can legitimately need seconds on the largest
# case. Override via .env - see SANDBOX_* there.
DEFAULT_TIMEOUT = float(os.environ.get("SANDBOX_TIMEOUT_SEC", 10.0))
# Contest solutions recurse over the whole input (DFS on 10^5 nodes); CPython's
# default 1000 would turn correct programs into RecursionErrors. Kept well under
# the C stack's real ceiling - a submission that sets its own limit to 10^6, as
# many do, can still crash the interpreter, which surfaces as WorkerCrash below.
DEFAULT_RECURSION_LIMIT = int(os.environ.get("SANDBOX_RECURSION_LIMIT", 10_000))

_SITECUSTOMIZE = "import sys\nsys.setrecursionlimit({limit})\n"

# Last line of a Python traceback: "ValueError: invalid literal for int()".
_ERROR_LINE = re.compile(r"^([A-Za-z_][A-Za-z0-9_.]*)\s*(?::\s*(.*))?$", re.DOTALL)


@dataclasses.dataclass(frozen=True)
class Outcome:
    """Result of running one candidate program on one test input.

    `value` is the program's stdout, verbatim - the thing src.oracle compares
    against the test case's expected output.
    """

    ok: bool
    value: Any = None
    error_type: str | None = None
    error_message: str | None = None
    traceback: str | None = None
    timed_out: bool = False
    duration: float = 0.0
    stderr: str = ""

    @property
    def crashed(self) -> bool:
        """Ran to completion but raised, as opposed to timing out or hanging."""
        return not self.ok and not self.timed_out


def _parse_error(stderr: str) -> tuple[str, str]:
    """(error_type, message) from a traceback, for src.typer's property half."""
    lines = [line for line in stderr.strip().splitlines() if line.strip()]
    if not lines:
        return "NonZeroExit", "program exited non-zero with no traceback"
    if not any(line.startswith("Traceback (most recent call last)") for line in lines):
        # `sys.exit("msg")` prints the bare message and no traceback, and
        # contest solutions bail out that way constantly. Without this the
        # message's first word would be read as the exception class, giving
        # src.typer a fresh failure type per distinct message.
        return "SystemExit", lines[-1].strip()
    match = _ERROR_LINE.match(lines[-1].strip())
    if not match:
        return "NonZeroExit", lines[-1].strip()
    # "module.ClassName" -> "ClassName", so a type raised from a stdlib module
    # buckets with the same class raised from builtins.
    return match.group(1).split(".")[-1], (match.group(2) or "").strip()


def run_program(
    source: str,
    stdin_text: str,
    *,
    timeout: float = DEFAULT_TIMEOUT,
    recursion_limit: int = DEFAULT_RECURSION_LIMIT,
) -> Outcome:
    """Execute ``source`` as a script with ``stdin_text`` on stdin.

    Isolation is process-level: a separate ``python3`` interpreter in a
    throwaway directory, killed on ``timeout`` seconds of wall time. An
    infinite loop, a segfault from a self-raised recursion limit, or a bare
    ``sys.exit()`` in candidate code can never take down the caller.
    """
    t0 = time.monotonic()
    with tempfile.TemporaryDirectory(prefix="ceg-run-") as tmp:
        tmpdir = pathlib.Path(tmp)
        main_py = tmpdir / "main.py"
        main_py.write_text(source, encoding="utf-8")
        (tmpdir / "sitecustomize.py").write_text(_SITECUSTOMIZE.format(limit=recursion_limit))

        env = dict(os.environ)
        env["PYTHONPATH"] = str(tmpdir)      # picks up sitecustomize.py above
        env["PYTHONIOENCODING"] = "utf-8"
        env.pop("PYTHONSTARTUP", None)

        try:
            proc = subprocess.run(
                [sys.executable, str(main_py)],
                input=stdin_text,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=timeout,
                cwd=tmpdir,
                env=env,
            )
        except subprocess.TimeoutExpired as exc:
            return Outcome(
                ok=False,
                timed_out=True,
                duration=time.monotonic() - t0,
                error_type="Timeout",
                error_message=f"exceeded {timeout}s wall time",
                stderr=(exc.stderr or "") if isinstance(exc.stderr, str) else "",
            )
        duration = time.monotonic() - t0

    if proc.returncode != 0:
        error_type, message = _parse_error(proc.stderr)
        if proc.returncode < 0:
            # Killed by a signal: a self-raised recursion limit blowing the C
            # stack, or the OOM killer. No traceback exists to parse.
            error_type, message = "WorkerCrash", f"killed by signal {-proc.returncode}"
        return Outcome(
            ok=False,
            value=proc.stdout,
            error_type=error_type,
            error_message=message,
            traceback=proc.stderr,
            duration=duration,
            stderr=proc.stderr,
        )

    return Outcome(ok=True, value=proc.stdout, duration=duration, stderr=proc.stderr)


if __name__ == "__main__":
    print(run_program("n = int(input())\nprint(n * 2)\n", "21\n"))
    print(run_program("import sys\nprint(sys.stdin.buffer.read().decode().strip()[::-1])\n", "abcde\n"))
    print(run_program("def f(n):\n    return f(n + 1)\nf(0)\n", ""))
    print(run_program("while True:\n    pass\n", "", timeout=1.0))
    print(run_program("import sys\nsys.exit('bye')\n", ""))
