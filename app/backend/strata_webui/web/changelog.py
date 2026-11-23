"""The changelog for the web UI: the whole file on a click on the Supervisor version, and once after an update the
sections the person has not seen yet.

CHANGELOG.md is shipped in the image next to the backend (Dockerfile), so this works without internet. Which version
was seen last is kept in one small file in /config (`last-seen-version`), so the popup comes once per installation,
in whichever browser opens the page first (there are no users to tell apart). Rules:

- `dev` builds: no popup, the file is never written.
- No file and no saved model config: a fresh install. Startup writes the current version, so there is no popup.
- No file but saved configs: an update from a version before this feature. Only the current version's section shows.
- A file: every section newer than it, up to the current version. Closing the popup writes the current version.
"""

from __future__ import annotations

import os
import re
import tempfile
from pathlib import Path

from ..core.ownership import Owner, set_file_owner

SEEN_FILE = "last-seen-version"
HEADING = re.compile(r"^## \[v?([0-9]+(?:\.[0-9]+)*)\](?:\s*-\s*(\S+))?\s*$")
VERSION = re.compile(r"[0-9]+(?:\.[0-9]+){0,3}")


def default_path() -> Path:
    """CHANGELOG.md beside the app in the image (/opt/strata-supervisor), or at the root of a checkout."""
    here = Path(__file__).resolve()                   # .../backend/strata_webui/web/changelog.py
    for folder in here.parents[3:5]:
        if (folder / "CHANGELOG.md").is_file():
            return folder / "CHANGELOG.md"
    return here.parents[3] / "CHANGELOG.md"


def parse(text: str) -> list[dict]:
    """The version sections, newest first as in the file: {"version", "date", "body"}. The text before the first
    version heading (title, intro) is left out; the body is the raw Markdown of the section."""
    entries: list[dict] = []
    current: dict | None = None
    for line in text.splitlines():
        match = HEADING.match(line)
        if match:
            current = {"version": match.group(1), "date": match.group(2) or "", "lines": []}
            entries.append(current)
        elif line.startswith("## "):
            current = None                             # another level-2 heading (e.g. "Unreleased"): not a version
        elif current is not None:
            current["lines"].append(line)
    return [{"version": e["version"], "date": e["date"], "body": "\n".join(e["lines"]).strip()} for e in entries]


def _key(version: str) -> tuple[int, ...]:
    return tuple(int(part) for part in version.split("."))


def is_release(version: str) -> bool:
    return bool(VERSION.fullmatch(version or ""))


def read_seen(config_dir: Path) -> str | None:
    try:
        text = (config_dir / SEEN_FILE).read_text(encoding="utf-8").strip()
    except (OSError, UnicodeDecodeError):
        return None
    return text if is_release(text) else None


def write_seen(config_dir: Path, version: str, owner: Owner) -> None:
    """Write the file atomically, owned like the model configs (PUID/PGID)."""
    fd, tmp_name = tempfile.mkstemp(dir=config_dir, prefix=f".{SEEN_FILE}.", suffix=".tmp")
    tmp = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(version + "\n")
        set_file_owner(tmp, owner)
        os.replace(tmp, config_dir / SEEN_FILE)
    finally:
        tmp.unlink(missing_ok=True)


def mark_fresh_install(config_dir: Path, current: str, has_configs: bool, owner: Owner) -> None:
    """At startup: on a fresh install remember the current version, so the first page load shows no popup."""
    if not is_release(current) or has_configs or (config_dir / SEEN_FILE).exists():
        return
    try:
        write_seen(config_dir, current, owner)
    except OSError:
        pass                                           # /config not writable: the start checks report that already


def unseen(entries: list[dict], current: str, seen: str | None) -> list[dict]:
    """The sections for the popup: newer than `seen`, up to `current`. Without `seen` only the current one."""
    if not is_release(current):
        return []
    top = _key(current)
    if seen is None:
        return [e for e in entries if _key(e["version"]) == top]
    low = _key(seen)
    return [e for e in entries if low < _key(e["version"]) <= top]
