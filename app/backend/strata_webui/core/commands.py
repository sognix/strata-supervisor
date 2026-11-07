"""Running commands and reading their JSON output, plus a small cache shared by request threads."""

from __future__ import annotations

import json
import subprocess
import threading
import time
from typing import Callable

Runner = Callable[[list[str], float], tuple[int, str]]


def run_command(args: list[str], timeout_s: float) -> tuple[int, str]:
    """Run an argument list (never a shell) and return (exit code, stdout); -1 if it could not run or timed out."""
    try:
        done = subprocess.run(args, capture_output=True, text=True, timeout=timeout_s, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return -1, str(exc)
    return done.returncode, done.stdout


def first_json(stdout: str) -> object | None:
    """The first JSON value in a command's output; some strata commands print a text line after it."""
    text = stdout.lstrip()
    if not text:
        return None
    try:
        value, _ = json.JSONDecoder().raw_decode(text)
    except ValueError:
        return None
    return value


def last_json_line(stdout: str) -> object | None:
    """The last line that parses as JSON. Strata code may print other text before it."""
    for line in reversed(stdout.splitlines()):
        line = line.strip()
        if line.startswith("{"):
            try:
                return json.loads(line)
            except ValueError:
                continue
    return None


ERROR_TTL_S = 10.0


class Cached:
    """A value computed at most once per `ttl_s` seconds, shared by all request threads.

    A result that is a dict with a non-empty "error" is kept only ERROR_TTL_S seconds, so a failure
    (for example a catalog probe during start) is retried soon instead of sticking for minutes.
    """

    def __init__(self, compute: Callable[[], object], ttl_s: float) -> None:
        self._compute = compute
        self._ttl = ttl_s
        self._lock = threading.Lock()
        self._value: object = None
        self._at = float("-inf")
        self._valid_for = ttl_s

    def invalidate(self) -> None:
        """The next get() computes the value again; nothing runs now."""
        with self._lock:
            self._at = float("-inf")

    def get(self, fresh: bool = False) -> object:
        with self._lock:
            if fresh or time.monotonic() - self._at > self._valid_for:
                self._value = self._compute()
                self._at = time.monotonic()
                failed = isinstance(self._value, dict) and bool(self._value.get("error"))
                self._valid_for = min(self._ttl, ERROR_TTL_S) if failed else self._ttl
            return self._value
