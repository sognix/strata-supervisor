"""Ownership of files the supervisor creates in /config.

The container runs as root. Files in /config must stay editable from the host, so everything the
supervisor creates there gets the owner PUID:PGID (default 99:100, nobody:users on Unraid) and
write permission for owner and group. See IMPLEMENTATION.md, "Goals and scope".
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

DEFAULT_PUID = 99
DEFAULT_PGID = 100
DIR_MODE = 0o775
FILE_MODE = 0o664


@dataclass(frozen=True)
class Owner:
    uid: int
    gid: int


def owner_from_env(env: dict[str, str]) -> Owner:
    """Read PUID and PGID. Raises ValueError with a readable message on bad values."""
    return Owner(_id_from_env(env, "PUID", DEFAULT_PUID), _id_from_env(env, "PGID", DEFAULT_PGID))


def _id_from_env(env: dict[str, str], name: str, default: int) -> int:
    raw = env.get(name, "").strip()
    if not raw:
        return default
    if not raw.isdigit():
        raise ValueError(f"{name}={raw!r} is not a number. Use a numeric user or group ID, e.g. {name}={default}.")
    return int(raw)


def make_dirs(path: Path, owner: Owner, stop_at: Path) -> None:
    """Create path and missing parents below stop_at, each owned by owner with DIR_MODE.

    Directories that already exist are left as they are. stop_at itself is never changed: it is the
    mount point, and its owner belongs to whoever mounted it.
    """
    path = Path(path)
    stop_at = Path(stop_at)
    missing: list[Path] = []
    current = path
    while current != stop_at and not current.exists():
        missing.append(current)
        if current.parent == current:
            break
        current = current.parent
    for directory in reversed(missing):
        directory.mkdir()
        _apply(directory, owner, DIR_MODE)


def set_file_owner(path: Path, owner: Owner) -> None:
    """Give a file the supervisor wrote the owner and FILE_MODE."""
    _apply(Path(path), owner, FILE_MODE)


def _apply(path: Path, owner: Owner, mode: int) -> None:
    os.chmod(path, mode)
    if os.geteuid() == 0:
        os.chown(path, owner.uid, owner.gid)
