"""Start checks the official entrypoint does before it runs `strata launch`.

The official entrypoint (docker/entrypoint.sh, tag v0.2.9) cannot be reused in parts, so the
supervisor repeats the steps it needs (IMPLEMENTATION.md, section 4):

1. create the folders under /data
2. link the llama.cpp seed into /data/install, so the first start fetches nothing from GitHub
3. drop run settings (/data/install/strata-*.json) that name a missing engine folder
4. require `"usable": true` from `strata components engine --output json`
5. require the device files of the card's backend in the container

Unlike the entrypoint, a failed check does not end the container: the UI keeps running and shows
the problem, and loading a model stays blocked. Without the engine check `strata launch` would
download the whole model on a wrongly set up host and still exit with code 0 (IMPLEMENTATION.md, section 11).
"""

from __future__ import annotations

import json
import os
import re
import secrets
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Iterator

from ..core.ownership import Owner, make_dirs, owner_from_env
from ..core.commands import run_command

# Engine folders inside the image; run settings that point below here must exist.
COMPONENTS_PREFIX = "/opt/strata/components/"
ENGINE_CHECK_TIMEOUT_S = 60
MIN_API_KEY_LENGTH = 16
DEFAULT_PORT = 8080
DEFAULT_UI_PORT = 8081


@dataclass(frozen=True)
class Paths:
    """Every place the checks touch, so tests can point them at a temporary folder."""

    data: Path = Path("/data")
    config: Path = Path("/config")
    dev: Path = Path("/dev")
    llama_seed: Path = Path("/opt/strata/seed/third_party/llama.cpp")
    strata_bin: Path = Path("/opt/strata/terminal/bin/strata")
    mountinfo: Path = Path("/proc/self/mountinfo")
    cpuinfo: Path = Path("/proc/cpuinfo")
    memory_max: Path = Path("/sys/fs/cgroup/memory.max")

    @property
    def install(self) -> Path:
        return self.data / "install"

    @property
    def models_config(self) -> Path:
        return self.config / "models"

    @property
    def key_file(self) -> Path:
        # Same file the official entrypoint and the `strata` wrapper use (STRATA_KEY_FILE default).
        return self.data / "state" / "api-key"


@dataclass
class Report:
    """Outcome of the start checks. `errors` block loading a model, `warnings` do not."""

    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    backend: str | None = None
    engine_usable: bool = False
    memory_limit_bytes: int | None = None
    port: int = DEFAULT_PORT
    ui_port: int = DEFAULT_UI_PORT
    api_key: str | None = field(default=None, repr=False)

    @property
    def can_load(self) -> bool:
        return not self.errors


# Runs a command given as an argument list and returns (exit code, stdout). Never a shell.
Runner = Callable[[list[str], int], tuple[int, str]]


def run_checks(paths: Paths, env: dict[str, str], runner: Runner = run_command) -> Report:
    report = Report()
    # Ports first: the UI must listen on UI_PORT even when a later check fails, or the page that
    # shows the error cannot be reached.
    _read_ports(env, report)
    if not _prepare_data(paths, report):
        return report
    _link_llama_seed(paths, report)
    _warn_if_not_volume(paths, paths.data, "-v /mnt/user/appdata/strata:/data", report)
    try:
        owner = owner_from_env(env)
    except ValueError as exc:
        report.errors.append(f"{exc} Nothing was created in {paths.config}.")
    else:
        _prepare_config(paths, owner, report)
    _drop_stale_run_configs(paths, report)
    _check_engine(paths, runner, report)
    if report.engine_usable:
        _check_devices(paths, report)
    _warn_cpu(paths, report)
    _read_api_key(paths, env, report)
    report.memory_limit_bytes = read_memory_limit(paths.memory_max)
    return report


def _read_ports(env: dict[str, str], report: Report) -> None:
    for name, attr, default in (("PORT", "port", DEFAULT_PORT), ("UI_PORT", "ui_port", DEFAULT_UI_PORT)):
        raw = env.get(name, "").strip()
        if not raw:
            setattr(report, attr, default)
        elif raw.isdigit() and 1 <= int(raw) <= 65535:
            setattr(report, attr, int(raw))
        else:
            report.errors.append(f"{name}={raw!r} is not a port number (1 to 65535).")
    if report.port == report.ui_port:
        report.errors.append(f"PORT and UI_PORT are both {report.port}. The API and the web UI need different ports.")


def _prepare_data(paths: Paths, report: Report) -> bool:
    for name in ("state", "home", "install", "Strata-data"):
        try:
            (paths.data / name).mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            report.errors.append(f"{paths.data} is not writable ({exc}). Map a host folder to /data.")
            return False
    return True


def _link_llama_seed(paths: Paths, report: Report) -> None:
    target = paths.install / "third_party" / "llama.cpp"
    if not paths.llama_seed.is_dir() or target.exists() or target.is_symlink():
        return
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.symlink_to(paths.llama_seed)
    except OSError as exc:
        report.warnings.append(f"Could not link {paths.llama_seed} to {target} ({exc}). The first start may fetch llama.cpp from GitHub.")


def _warn_if_not_volume(paths: Paths, mount_point: Path, example: str, report: Report) -> None:
    if not is_mount_point(paths.mountinfo, mount_point):
        report.warnings.append(f"{mount_point} is not mapped to a host folder, so its content is lost when the container is removed. Add: {example}")


def is_mount_point(mountinfo: Path, mount_point: Path) -> bool:
    """True if mount_point appears as a mount point (field 5) in /proc/self/mountinfo."""
    try:
        lines = mountinfo.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return False
    wanted = str(mount_point)
    for line in lines:
        fields = line.split()
        if len(fields) > 4 and _unescape_mount_path(fields[4]) == wanted:
            return True
    return False


def _unescape_mount_path(raw: str) -> str:
    # mountinfo writes space, tab, newline and backslash as octal escapes.
    return re.sub(r"\\([0-7]{3})", lambda m: chr(int(m.group(1), 8)), raw)


def _prepare_config(paths: Paths, owner: Owner, report: Report) -> None:
    try:
        paths.config.mkdir(parents=True, exist_ok=True)
        make_dirs(paths.models_config, owner, stop_at=paths.config)
    except OSError as exc:
        report.errors.append(f"{paths.config} is not writable ({exc}). Map a host folder to /config.")
        return
    _warn_if_not_volume(paths, paths.config, "-v /mnt/user/appdata/strata-supervisor:/config", report)


def _drop_stale_run_configs(paths: Paths, report: Report) -> None:
    for run_config in sorted(paths.install.glob("strata-*.json")):
        if not run_config.is_file():
            continue
        missing = missing_component_path(run_config)
        if missing is None:
            continue
        try:
            run_config.unlink()
        except OSError as exc:
            report.warnings.append(f"Could not remove {run_config.name}, which names {missing} that this image does not have ({exc}).")
            continue
        report.notes.append(
            f"Removed the run settings {run_config.name}: they name {missing}, which this image does not have "
            "(made by an image for another card or engine version). Strata writes them again; the model files are kept."
        )


def missing_component_path(run_config: Path) -> str | None:
    """First string in the JSON that points below COMPONENTS_PREFIX and does not exist, else None."""
    try:
        data = json.loads(run_config.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return None
    for value in _strings(data):
        if value.startswith(COMPONENTS_PREFIX) and not Path(value).exists():
            return value
    return None


def _strings(value: object) -> Iterator[str]:
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for item in value.values():
            yield from _strings(item)
    elif isinstance(value, list):
        for item in value:
            yield from _strings(item)


def _check_engine(paths: Paths, runner: Runner, report: Report) -> None:
    code, stdout = runner([str(paths.strata_bin), "components", "engine", "--output", "json"], ENGINE_CHECK_TIMEOUT_S)
    status = parse_engine_output(stdout)
    report.engine_usable = status.usable
    report.backend = status.backend
    if status.usable:
        return
    reason = status.notice or f"`strata components engine` gave no readable answer (exit code {code})."
    report.errors.append(
        f"The engine in this image cannot run on this computer: {reason} "
        "NVIDIA: start the container with --gpus all and install the NVIDIA driver plugin on Unraid "
        "(driver 580.65 or newer for cuda13). AMD: add --device /dev/kfd --device /dev/dri --group-add video. "
        "Then check that the image flavor matches the card."
    )


@dataclass(frozen=True)
class EngineStatus:
    usable: bool
    backend: str | None
    notice: str | None


def parse_engine_output(stdout: str) -> EngineStatus:
    """Read `strata components engine --output json` (strata_control/engine_component.py, status(), v0.2.9).

    The output is one JSON object. `usable` exists only at the top level. When usable, `engine.backend`
    is the package that will run; otherwise the top-level `backend` is the detected one (may be null).
    `installed[].backend` lists every package found and is deliberately ignored. Output that does not
    parse counts as not usable, so a broken answer never lets a download start.
    """
    try:
        data = json.loads(stdout)
    except ValueError:
        return EngineStatus(False, None, None)
    if not isinstance(data, dict):
        return EngineStatus(False, None, None)
    usable = data.get("usable") is True
    engine = data.get("engine") if isinstance(data.get("engine"), dict) else {}
    backend = engine.get("backend") if usable else data.get("backend")
    notice = data.get("notice")
    return EngineStatus(
        usable,
        backend if isinstance(backend, str) and backend else None,
        notice if isinstance(notice, str) and notice else None,
    )


def _check_devices(paths: Paths, report: Report) -> None:
    dev = paths.dev
    if report.backend == "cuda":
        if not any(dev.glob("nvidia[0-9]*")) and not (dev / "dxg").exists():
            report.errors.append(
                "The engine sees an NVIDIA card, but the container cannot use it. Start the container with "
                "--gpus all (needs the NVIDIA driver plugin on Unraid, or the NVIDIA Container Toolkit)."
            )
    elif report.backend == "hip":
        if not (dev / "kfd").exists():
            report.errors.append(
                "This is an AMD card, but /dev/kfd is not in the container. Add: --device /dev/kfd "
                "--device /dev/dri --group-add video"
            )
    elif report.backend == "sycl":
        if not any((dev / "dri").glob("renderD*")):
            report.errors.append(
                "This is an Intel card, but /dev/dri is not in the container. Add: --device /dev/dri"
            )


def _warn_cpu(paths: Paths, report: Report) -> None:
    try:
        text = paths.cpuinfo.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return
    flags = next((line for line in text.splitlines() if line.startswith("flags")), None)
    if flags is not None and "avx2" not in flags.split(":", 1)[-1].split():
        report.warnings.append("This CPU has no AVX2. The engine is built for AVX2 and may stop with 'illegal instruction'.")


def _read_api_key(paths: Paths, env: dict[str, str], report: Report) -> None:
    """One inference key for every model (IMPLEMENTATION.md, "Settled at build start").

    API_KEY, else API_KEY_FILE (entrypoint step 6), else the key kept in /data/state/api-key, else a
    new one. It is always passed through --api-key-env, so it does not change between models, and
    it is written to /data/state/api-key (0600) for `docker exec ... strata monitor`.
    """
    key = env.get("API_KEY", "")
    key_file = env.get("API_KEY_FILE", "")
    if not key and key_file:
        try:
            key = Path(key_file).read_text(encoding="utf-8").replace("\r", "").replace("\n", "")
        except OSError as exc:
            report.errors.append(f"API_KEY_FILE={key_file} cannot be read ({exc}).")
            return
    if key and len(key) < MIN_API_KEY_LENGTH:
        report.warnings.append(
            f"API_KEY is short ({len(key)} characters). Use {MIN_API_KEY_LENGTH} or more; 'openssl rand -hex 24' makes a good one."
        )
    if not key:
        try:
            key = paths.key_file.read_text(encoding="utf-8").strip()
        except OSError:
            key = ""
    if not key:
        key = secrets.token_hex(24)
        report.notes.append(f"No API_KEY was set, so a key was made and kept in {paths.key_file}. The web UI shows it on request.")
    report.api_key = key
    try:
        write_private(paths.key_file, key + "\n")
    except OSError as exc:
        report.warnings.append(f"Could not write {paths.key_file} ({exc}). `docker exec ... strata monitor` will need the key typed in.")


def write_private(path: Path, text: str) -> None:
    """Write a file only root can read (mode 0600), like the entrypoint's `umask 077`."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        handle.write(text)
    os.chmod(path, 0o600)


def read_memory_limit(memory_max: Path) -> int | None:
    """Container RAM limit from cgroup v2, None if there is none. Shown in the UI, not warned about."""
    try:
        raw = memory_max.read_text(encoding="ascii").strip()
    except OSError:
        return None
    return int(raw) if raw.isdigit() else None
