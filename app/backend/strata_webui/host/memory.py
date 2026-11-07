"""RAM and swap: what the host has and what this container may use."""

from __future__ import annotations

from pathlib import Path


def read_meminfo(path: Path = Path("/proc/meminfo")) -> dict[str, int]:
    """Values in bytes: total, available, swap_total, swap_free. Missing ones are left out."""
    wanted = {"MemTotal": "total", "MemAvailable": "available", "SwapTotal": "swap_total", "SwapFree": "swap_free"}
    out: dict[str, int] = {}
    try:
        lines = path.read_text(encoding="ascii", errors="replace").splitlines()
    except OSError:
        return out
    for line in lines:
        key, _, rest = line.partition(":")
        if key in wanted:
            parts = rest.split()
            if parts and parts[0].isdigit():
                out[wanted[key]] = int(parts[0]) * 1024
    return out


def container_usage(cgroup_dir: Path = Path("/sys/fs/cgroup")) -> dict[str, int]:
    """What this container's processes hold now, in bytes, from cgroup v2 memory.stat.

    anon: ordinary process memory. shmem: shared memory, where Strata keeps the experts it page-locks in RAM.
    file_cache: file pages that are not shmem, mostly the model files read at load; the kernel frees them when needed.
    Empty when the file cannot be read.
    """
    try:
        lines = (cgroup_dir / "memory.stat").read_text(encoding="ascii").splitlines()
    except OSError:
        return {}
    stat: dict[str, int] = {}
    for line in lines:
        key, _, value = line.partition(" ")
        if value.strip().isdigit():
            stat[key] = int(value)
    if "anon" not in stat:
        return {}
    shmem = stat.get("shmem", 0)
    return {"anon": stat["anon"], "shmem": shmem, "file_cache": max(0, stat.get("file", 0) - shmem)}


def _cgroup_value(path: Path) -> int | None | str:
    """A cgroup v2 limit file: an int, "max" for no limit, or None if the file is missing or unreadable."""
    try:
        raw = path.read_text(encoding="ascii").strip()
    except OSError:
        return None
    if raw == "max":
        return "max"
    return int(raw) if raw.isdigit() else None


def memory_limits(cgroup_dir: Path = Path("/sys/fs/cgroup"), meminfo: Path = Path("/proc/meminfo")) -> dict:
    """RAM and swap this container may use, in bytes (DESIGN.md, section 4).

    RAM: memory.max, or the host's MemTotal without a limit. Swap: memory.swap.max, or the host's
    SwapTotal without a limit or when the file is missing (then the kernel does not count swap per cgroup).
    "*_from" says where a value came from: "container" or "host".
    """
    host = read_meminfo(meminfo)
    out: dict = {}
    for key, file, host_key in (("ram", "memory.max", "total"), ("swap", "memory.swap.max", "swap_total")):
        value = _cgroup_value(cgroup_dir / file)
        if isinstance(value, int):
            out[f"{key}_bytes"], out[f"{key}_from"] = value, "container"
        else:
            out[f"{key}_bytes"], out[f"{key}_from"] = host.get(host_key), "host"
    return out
