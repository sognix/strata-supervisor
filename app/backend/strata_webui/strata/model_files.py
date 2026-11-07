"""The model files on disk, for the Models page: Strata's downloads and the own GGUF folders, with size and delete.

Strata's downloads live in /data/Strata-data/models/<folder> (one folder per version, plus loose files such as the
picture reader's mmproj), its prepared data for a version in /data/Strata-data/packs/<folder in lower case>
(strata_control/models_move.py: "models/<tag>", "packs/<tag>"). Deleting a Strata model removes both; the shared
draft layer (mtp) stays. Own GGUF files are the folders in /models. Strata has no delete command, so this module
deletes, but only an entry it listed itself, never a path from outside, and never while a model runs.
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path


class FileError(ValueError):
    """A delete that is refused, with a message for the UI."""


def _size(path: Path) -> tuple[int, bool]:
    """Bytes of a file or folder (links not followed) and whether any file has a second name (a hard link)."""
    total, shared = 0, False
    try:
        if path.is_symlink() or path.is_file():
            st = path.lstat()
            return st.st_size, (not path.is_symlink() and st.st_nlink > 1)
        for root, _dirs, files in os.walk(path):
            for name in files:
                try:
                    st = os.lstat(os.path.join(root, name))
                except OSError:
                    continue
                total += st.st_size
                shared = shared or st.st_nlink > 1
    except OSError:
        pass
    return total, shared


def folder_bytes(path: Path) -> int:
    """Bytes of all files in a folder, unfinished downloads (.part) included."""
    return _size(path)[0]


def _entry(path: Path, kind: str) -> dict:
    size, shared = _size(path)
    out = {"name": path.name, "path": str(path), "kind": kind, "size_bytes": size, "hard_linked": shared,
           "link_target": None}
    if path.is_symlink():
        out["kind"] = "link"
        out["link_target"] = os.readlink(path)
    return out


def _children(folder: Path) -> list[Path]:
    try:
        return sorted((p for p in folder.iterdir() if not p.name.startswith(".")), key=lambda p: p.name.lower())
    except OSError:
        return []


def listing(strata_data: Path, own_root: Path) -> dict:
    """Both lists. `strata_data` is /data/Strata-data, `own_root` is /models."""
    models, packs = strata_data / "models", strata_data / "packs"
    strata = []
    for path in _children(models):
        if path.name.endswith((".done", ".part")):
            continue                                   # marks and unfinished downloads belong to their file
        entry = _entry(path, "folder" if path.is_dir() else "file")
        pack = packs / path.name.lower()
        if path.is_dir() and pack.is_dir():
            entry["pack"] = str(pack)
            entry["pack_bytes"] = _size(pack)[0]
        strata.append(entry)
    own = [_entry(p, "folder" if p.is_dir() else "file") for p in _children(own_root)]
    return {"strata_dir": str(models), "own_dir": str(own_root), "own_exists": own_root.is_dir(),
            "own_writable": own_root.is_dir() and os.access(own_root, os.W_OK), "strata": strata, "own": own}


def delete(path_text: str, strata_data: Path, own_root: Path) -> dict:
    """Delete one listed entry. A Strata model takes its pack and its .done mark with it. Returns the bytes removed."""
    current = listing(strata_data, own_root)
    entry = next((e for e in current["strata"] + current["own"] if e["path"] == path_text), None)
    if entry is None:
        raise FileError("This entry is not in the lists (any more). Reload the page.")
    path = Path(entry["path"])
    if entry in current["own"] and not current["own_writable"]:
        raise FileError(f"{own_root} is mounted read-only, so the web UI cannot delete there. Mount it without :ro, or delete on the host.")
    if entry["kind"] in ("link", "file"):
        path.unlink()                                  # a link: only the link, never what it points to
        done = path.with_name(path.name + ".done")
        if entry in current["strata"] and done.is_file():
            done.unlink()
    else:
        shutil.rmtree(path)
    # Space comes back only for files with no other name: a link frees nothing, hard-linked files neither.
    freed = 0 if entry["kind"] == "link" or entry["hard_linked"] else entry["size_bytes"]
    if entry.get("pack"):
        shutil.rmtree(entry["pack"], ignore_errors=True)
        freed += entry.get("pack_bytes", 0)
    return {"deleted": entry["path"], "pack": entry.get("pack"), "freed_bytes": freed}
