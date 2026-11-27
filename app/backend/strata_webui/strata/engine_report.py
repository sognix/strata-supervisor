"""What the engine itself reports about its memory, read from its log (the run that started last).

The engine writes these lines once while it starts (engine 0.1.55, Strata v0.2.9). They are plain text, not an
interface, so every value is optional: a line that is missing or worded differently just leaves its field out.
"""

from __future__ import annotations

import re
from pathlib import Path

RUN_START = re.compile(r"^\[strata\] (\S+ \S+) engine started")
# Since Strata v0.2.22 (#7044) Control passes the conversation cache itself on a PC (or container limit) with a lot of
# RAM: "--conversation-cache-mib N" in the arguments of the "engine started" line, and the engine then writes no
# "sized from the free RAM" line. A value set by hand ends up there too; 0 means the cache is off.
CONVERSATION_FLAG = re.compile(r"\s--conversation-cache-mib (\d+)(?:\s|$)")
# Hy4-Flash and Inkling-2-Mini: the expert limits Strata's setup worked out for this computer, also in the arguments of
# that line (setup.py install_hy4_flash / inkling_expert_flags, Strata 0.2.22). strata-inkling reads both as GiB (#6792);
# --ssd-experts: what fits in neither is read from the SSD. Left out on a layer split or unified memory.
NATIVE_FLAGS = {
    "native_vram_cap_gib": re.compile(r"\s--vram-experts-gb ([\d.]+)(?:\s|$)"),
    "native_ram_cap_gib": re.compile(r"\s--ram-experts-gb ([\d.]+)(?:\s|$)"),
}
SSD_FLAG = re.compile(r"\s--ssd-experts(?:\s|$)")
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
    # Qwen3.6-35B-A3B runs on its own engine (strata-q36) with its own lines. Taken from the engine's source
    # (engine/src/qwen35/q36_engine.cu, Strata v0.2.17), not from a real log yet.
    # "strata-q36: 2 sessions (--batch): 1.51 GiB of VRAM each at a context of 32768 (3.02 GiB in all), ..."
    "q36_sessions": re.compile(r"strata-q36: (\d+) sessions \(--batch\): ([\d.]+) GiB of VRAM each"),
    # "strata-q36: every routed expert in VRAM (19.80 GiB) in 4.1 s": nothing of the experts in RAM
    "q36_all_vram": re.compile(r"strata-q36: every routed expert in VRAM \(([\d.]+) GiB\)"),
    # "strata-q36: hybrid experts: 120 of 256 per layer in VRAM (9.30 GiB), all 19.80 GiB in pinned host memory, ..."
    "q36_hybrid": re.compile(r"strata-q36: hybrid experts: \d+ of \d+ per layer in VRAM \(([\d.]+) GiB\), all ([\d.]+) GiB in \S+ host memory"),
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
            flag = CONVERSATION_FLAG.search(line)
            if flag:
                out["conversation_gib"] = round(int(flag.group(1)) * GIB_PER_MIB, 2)
            for key, pattern in NATIVE_FLAGS.items():
                found = pattern.search(line)
                if found:
                    out[key] = float(found.group(1))
            if SSD_FLAG.search(line):
                out["native_ssd"] = True
            continue
        for key, pattern in PATTERNS.items():
            m = pattern.search(line)
            if not m:
                continue
            if key in ("sessions", "q36_sessions"):
                out["sessions"] = int(m.group(1))
                out["session_gib"] = float(m.group(2))
            elif key == "q36_all_vram":
                out["vram_experts_gib"] = float(m.group(1))
                out["ram_experts_gib"] = 0.0
            elif key == "q36_hybrid":
                out["vram_experts_gib"] = float(m.group(1))
                out["ram_experts_gib"] = float(m.group(2))
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
