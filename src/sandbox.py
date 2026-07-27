"""A2 - run candidate code on one input under a hard timeout.

QuixBugs is full of recursion and loops; a wrong patch hangs easily. Run in a
subprocess, cap wall time, and catch every exception including SystemExit.
"""
from __future__ import annotations

import dataclasses
import json
import os
import subprocess
import sys
import time
from typing import Any

from dotenv import load_dotenv

load_dotenv()

# Low on purpose: QuixBugs' recursive bugs (gcd, mergesort, hanoi, ...) diverge
# by re-recursing on an unchanged argument, so a low ceiling turns an eventual
# RecursionError into a fast one. Override via .env - see SANDBOX_* there.
DEFAULT_TIMEOUT = float(os.environ.get("SANDBOX_TIMEOUT_SEC", 5.0))
DEFAULT_RECURSION_LIMIT = int(os.environ.get("SANDBOX_RECURSION_LIMIT", 300))

# Runs inside a fresh `python3 -c` process. Talks JSON over stdin/stdout only,
# so the parent never has to trust (or unpickle) anything the candidate does.
_WORKER = r"""
import contextlib, io, json, sys, traceback

def _main():
    payload = json.loads(sys.stdin.read())
    sys.setrecursionlimit(payload["recursion_limit"])
    namespace: dict = {}
    buf = io.StringIO()
    try:
        with contextlib.redirect_stdout(buf):
            exec(payload["source"], namespace)
            fn = namespace[payload["func_name"]]
            value = fn(*payload["args"])
            if hasattr(value, "__next__"):  # generators / map / filter
                value = list(value)
        out = {"ok": True, "value": value}
    except BaseException as exc:  # candidate code is untrusted: catch everything,
        out = {                   # including SystemExit and KeyboardInterrupt.
            "ok": False,
            "error_type": type(exc).__name__,
            "error_message": str(exc),
            "traceback": traceback.format_exc(),
        }
    try:
        text = json.dumps(out)
    except TypeError:
        text = json.dumps({
            "ok": False,
            "error_type": "Unserializable",
            "error_message": f"return value is not JSON-serialisable: {out.get('value')!r}",
        })
    sys.stdout.write(text)

_main()
"""


@dataclasses.dataclass(frozen=True)
class Outcome:
    """Result of running one candidate on one input."""

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


def run_call(
    source: str,
    func_name: str,
    args: list[Any],
    *,
    timeout: float = DEFAULT_TIMEOUT,
    recursion_limit: int = DEFAULT_RECURSION_LIMIT,
) -> Outcome:
    """Execute ``func_name(*args)`` from ``source`` in a fresh subprocess.

    Isolation is process-level: a separate ``python3`` interpreter, killed on
    ``timeout`` seconds of wall time, with its own (low) recursion ceiling.
    A segfault, an infinite loop, or a bare ``sys.exit()`` in candidate code
    can never take down the caller. ``value`` must be JSON-serialisable;
    generators/iterators are drained to a list before crossing the pipe.
    """
    payload = json.dumps({
        "source": source,
        "func_name": func_name,
        "args": args,
        "recursion_limit": recursion_limit,
    })

    t0 = time.monotonic()
    try:
        proc = subprocess.run(
            [sys.executable, "-c", _WORKER],
            input=payload,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired as exc:
        return Outcome(
            ok=False,
            timed_out=True,
            duration=time.monotonic() - t0,
            error_type="Timeout",
            error_message=f"exceeded {timeout}s wall time",
            stderr=exc.stderr or "",
        )
    duration = time.monotonic() - t0

    if proc.returncode != 0 or not proc.stdout.strip():
        return Outcome(
            ok=False,
            duration=duration,
            stderr=proc.stderr,
            error_type="WorkerCrash",
            error_message=f"worker exited {proc.returncode} with no result on stdout",
        )

    try:
        result = json.loads(proc.stdout)
    except json.JSONDecodeError:
        return Outcome(
            ok=False,
            duration=duration,
            stderr=proc.stderr,
            error_type="WorkerCrash",
            error_message="worker produced non-JSON stdout",
        )

    return Outcome(
        ok=result["ok"],
        value=result.get("value"),
        error_type=result.get("error_type"),
        error_message=result.get("error_message"),
        traceback=result.get("traceback"),
        duration=duration,
        stderr=proc.stderr,
    )


if __name__ == "__main__":
    print(run_call("def f(n):\n    return n * 2\n", "f", [21]))
    print(run_call("def f():\n    return 1 + f()\n", "f", [], recursion_limit=100))
    print(run_call("def f():\n    while True: pass\n", "f", [], timeout=1.0))
    print(run_call("def f():\n    import sys; sys.exit('bye')\n", "f", []))
