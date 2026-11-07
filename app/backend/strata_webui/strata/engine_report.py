"""What the engine itself reports about its memory, read from its log (the run that started last).

The engine writes these lines once while it starts (engine 0.1.55, Strata v0.2.9). They are plain text, not an
interface, so every value is optional: a line that is missing or worded differently just leaves its field out.
"""

from __future__ import annotations

import re
from pathlib import Path

RUN_START = re.compile(r"^\[strata\] (\S+ \S+) engine started")
PATTERNS = {
    # "--batch: 3 slot sessions on CUDA0 (0.56 GiB each); 17.97 GiB free"
    "sessions": re.compile(r"--batch: (\d+) slot sessions on \S+ \(([\d.]+) GiB each\)"),
    # "expert cache 8114 slots, 15.44 GiB of VRAM" (one line per card on a split)
    "vram_experts": re.compile(r"expert cache (\d+) slots, ([\d.]+) GiB of VRAM"),
    # "resident RAM mode: 11.37 GiB of experts in RAM (page-locked)"
    "resident": re.compile(r"resident RAM mode: ([\d.]+) GiB of experts in RAM"),
    # "loaded 24.02 GiB at 3.02 GiB/s": the expert arena, all experts in RAM (normal mode)
    "arena": re.compile(r"strata generate: loaded ([\d.]+) GiB at"),
    # "conversation cache sized from the free RAM: 8192 MiB for up to 4 parked conversations"
    "conversation": re.compile(r"conversation cache sized from the free RAM: (\d+) MiB"),
    # "strata mtp: draft layer loaded, 839 MiB of VRAM"
    "mtp": re.compile(r"draft layer loaded, (\d+) MiB of VRAM"),
    # "900 MiB of VRAM free with everything loaded"
    "vram_free": re.compile(r"(\d+) MiB of VRAM free with everything loaded"),
}
MAX_LOG_BYTES = 16 * 1024 * 1024
GIB_PER_MIB = 1 / 1024


def memory_report(path: Path) -> dict | None:
    """Memory figures of the last run in this engine log, in GiB. None if the log has no run."""
    try:
        if path.stat().st_size > MAX_LOG_BYTES:
            return None
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return None
    start = None
    for i, line in enumerate(lines):
        if RUN_START.match(line):
            start = i
    if start is None:
        return None
    return parse(lines[start:])


def parse(lines: list[str]) -> dict:
    """The figures found in one run's lines. Missing ones are left out."""
    out: dict = {}
    cache_gib = 0.0
    for line in lines:
        m = RUN_START.match(line)
        if m:
            out["started"] = m.group(1)
            continue
        for key, pattern in PATTERNS.items():
            m = pattern.search(line)
            if not m:
                continue
            if key == "sessions":
                out["sessions"] = int(m.group(1))
                out["session_gib"] = float(m.group(2))
            elif key == "vram_experts":
                cache_gib += float(m.group(2))
                out["vram_experts_gib"] = round(cache_gib, 2)
            elif key == "resident":
                out["ram_experts_gib"] = float(m.group(1))
            elif key == "arena":
                out.setdefault("arena_gib", float(m.group(1)))
            elif key == "conversation":
                out["conversation_gib"] = round(int(m.group(1)) * GIB_PER_MIB, 2)
            elif key == "mtp":
                out["mtp_gib"] = round(int(m.group(1)) * GIB_PER_MIB, 2)
            elif key == "vram_free":
                out["vram_free_gib"] = round(int(m.group(1)) * GIB_PER_MIB, 2)
    # Without the resident RAM mode the whole expert arena stays in RAM.
    if "ram_experts_gib" not in out and "arena_gib" in out:
        out["ram_experts_gib"] = out["arena_gib"]
    return out
