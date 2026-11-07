"""Log lines: the supervisor's own, kept in memory for the web UI's Logs page, and tails of log files."""

from __future__ import annotations

import threading
import time
from collections import deque
from pathlib import Path

_LINES: deque[str] = deque(maxlen=1000)
_LOCK = threading.Lock()
TAIL_MAX_BYTES = 256 * 1024


def log(prefix: str, message: str) -> None:
    line = f"{time.strftime('%H:%M:%S')} {prefix} {message}"
    with _LOCK:
        _LINES.append(line)
    print(f"{prefix} {message}", flush=True)


def lines() -> list[str]:
    with _LOCK:
        return list(_LINES)


def tail(path: Path, lines: int = 200) -> list[str]:
    """Last lines of a text file, reading at most TAIL_MAX_BYTES from the end."""
    try:
        with path.open("rb") as handle:
            handle.seek(0, 2)
            size = handle.tell()
            handle.seek(max(0, size - TAIL_MAX_BYTES))
            data = handle.read()
    except OSError:
        return []
    text = data.decode("utf-8", errors="replace")
    return list(deque(text.splitlines(), maxlen=lines))
