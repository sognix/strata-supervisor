"""The folders of own GGUF files the UI can offer: one model per sub-folder of /models (Strata's docs/DOCKER.md).

Strata runs only its own models (docs/MODELS.md), so a folder is described, not judged: its shards, whether
they are complete, and what the first file's header says. The architectures of the models Strata itself
downloaded are returned too, so the page can point out a folder whose architecture Strata has not shown here.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

from .gguf import GgufError, read_header

SHARD = re.compile(r"-(\d{5})-of-(\d{5})\.gguf$", re.IGNORECASE)


def _ggufs(folder: Path) -> list[Path]:
    try:
        return sorted(p for p in folder.iterdir() if p.suffix.lower() == ".gguf" and p.is_file())
    except OSError:
        return []


def describe_folder(folder: Path) -> dict:
    """One folder: its .gguf files, whether the numbered shards are complete, and the header of the first one."""
    files = _ggufs(folder)
    out: dict = {"name": folder.name, "path": str(folder), "files": len(files), "size_bytes": 0,
                 "complete": bool(files), "missing": [], "header": None, "error": None}
    if not files:
        out["error"] = "No .gguf files in this folder. The shards of one model belong directly in it, not in sub-folders."
        return out
    for path in files:
        try:
            out["size_bytes"] += path.stat().st_size
        except OSError:
            pass
    shards = [SHARD.search(p.name) for p in files]
    if all(shards):
        totals = {int(m.group(2)) for m in shards}
        have = {int(m.group(1)) for m in shards}
        if len(totals) != 1:
            out["complete"] = False
            out["error"] = "The shards belong to different sets (their '-of-N' differs)."
        else:
            total = totals.pop()
            out["missing"] = [i for i in range(1, total + 1) if i not in have]
            out["complete"] = not out["missing"]
        first = min(files, key=lambda p: int(SHARD.search(p.name).group(1)))
    else:
        if len(files) > 1:
            out["complete"] = False
            out["error"] = "Several .gguf files that are not numbered shards (…-00001-of-00002.gguf): one model per folder."
        first = files[0]
    try:
        out["header"] = read_header(first)
    except (GgufError, OSError) as exc:
        out["error"] = out["error"] or f"{first.name}: {exc}"
    return out


def scan(root: Path, strata_models: Path | None = None) -> dict:
    """Every sub-folder of `root` with its description, loose .gguf files and links that lead nowhere."""
    out: dict = {"root": str(root), "exists": root.is_dir(), "folders": [], "loose_files": [], "broken": [],
                 "strata_architectures": sorted(strata_architectures(strata_models)) if strata_models else []}
    if not out["exists"]:
        return out
    try:
        entries = sorted(root.iterdir(), key=lambda p: p.name.lower())
    except OSError as exc:
        out["error"] = f"{root} cannot be read: {exc}"
        return out
    for entry in entries:
        if entry.name.startswith("."):
            continue
        if entry.is_symlink() and not entry.exists():
            out["broken"].append({"name": entry.name, "target": os.readlink(entry)})
        elif entry.is_dir():
            out["folders"].append(describe_folder(entry))
        elif entry.suffix.lower() == ".gguf":
            out["loose_files"].append(entry.name)
    return out


def strata_contexts(models_dir: Path) -> dict[str, int]:
    """Trained context length from the header of each model Strata downloaded: {folder name in lower case: tokens}."""
    out: dict[str, int] = {}
    try:
        folders = [p for p in models_dir.iterdir() if p.is_dir()]
    except OSError:
        return out
    for folder in folders:
        header = describe_folder(folder).get("header") or {}
        if isinstance(header.get("context_length"), int):
            out[folder.name.lower()] = header["context_length"]
    return out


def strata_architectures(models_dir: Path) -> set[str]:
    """Architectures named in the headers of the models Strata downloaded itself (one file per model folder)."""
    found: set[str] = set()
    try:
        folders = [p for p in models_dir.iterdir() if p.is_dir()]
    except OSError:
        return found
    for folder in folders:
        files = _ggufs(folder)
        if not files:
            continue
        try:
            arch = read_header(files[0]).get("architecture")
        except (GgufError, OSError):
            continue
        if isinstance(arch, str):
            found.add(arch)
    return found
