"""Per-model settings in /config/models/<name>.toml and their `strata launch` flags.

These files are authoritative (IMPLEMENTATION.md, section 5, point 6): the launch always gets
`--save-preset <name>` and never `--preset`, so a setting deleted here does not live on in
Strata's own preset. The files are flat, read with tomllib and written by the small writer below,
so they stay easy to edit by hand on the host.
"""

from __future__ import annotations

import os
import re
import tempfile
import tomllib
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path

from .launcher import LaunchError, check_model_name
from ..core.ownership import Owner, set_file_owner

KV_FORMATS = ("int8", "k8v4", "q4_0", "fp16")
VISION_MODES = ("off", "on", "gpu", "cpu")
MTP_MODES = ("", "on", "off")
ROLES = ("main", "helper", "off")
RAM_MODES = ("normal", "resident-experts", "budget", "mmap-experts")

MODEL_SIZE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,47}")
FAMILY = re.compile(r"[a-z0-9][a-z0-9_.-]{0,31}")
CONTEXT = re.compile(r"[0-9]{1,8}|[0-9]{1,5}[kKmM]")
SET_ENTRY = re.compile(r"[A-Za-z0-9_][A-Za-z0-9_.-]*(=[^\s\x00-\x1f\x7f]*)?")
MAX_SET_ENTRIES = 32
MAX_NOTES = 2000

# Settings the form or the launcher set themselves. In the free --set list they would undo the single
# RAM-mode choice, or fight the host, port and parallel overrides Strata writes into the profile
# (runner.py, ensure_profile(), v0.2.9), which the API-port placeholder relies on.
MANAGED_SET_NAMES = frozenset({
    "resident-experts", "resident-budget-gib", "mmap-experts", "vram-reserve-mib", "vram-reserve-later-mib",
    "host", "port", "parallel", "gpu", "gpu-roles", "api-key", "vision", "mtp", "kv", "max-context",
})


@dataclass
class ModelConfig:
    model: str = ""
    family: str = ""
    model_path: str = ""
    context: str = "32k"
    kv: str = "int8"
    vision: str = "off"
    mtp: str = ""
    parallel: int = 0  # 0: Strata fits it to the cards
    gpu_roles: str = ""
    ram_mode: str = "normal"
    ram_budget_gib: int = 0
    vram_reserve_mib: int | None = None
    vram_reserve_later_mib: int | None = None
    extra_set: list[str] = field(default_factory=list)
    notes: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


class ConfigError(ValueError):
    """A setting the UI or a hand-edited file got wrong, with a message for the UI."""


def from_dict(data: dict, model_path_roots: tuple[Path, ...]) -> ModelConfig:
    """Build and validate a config from JSON (the form) or TOML (the file). Unknown keys are refused."""
    if not isinstance(data, dict):
        raise ConfigError("Settings must be an object.")
    known = {f.name for f in fields(ModelConfig)}
    unknown = sorted(set(data) - known)
    if unknown:
        raise ConfigError(f"Unknown settings: {', '.join(unknown)}.")
    cfg = ModelConfig()
    for name in known:
        if name in data:
            setattr(cfg, name, data[name])
    _validate(cfg, model_path_roots)
    return cfg


def _validate(cfg: ModelConfig, roots: tuple[Path, ...]) -> None:
    for name in ("model", "family", "model_path", "context", "kv", "vision", "mtp", "gpu_roles", "ram_mode", "notes"):
        value = getattr(cfg, name)
        if not isinstance(value, str):
            raise ConfigError(f"{name} must be text.")
        setattr(cfg, name, value.strip() if name != "notes" else value)
    for name in ("parallel", "ram_budget_gib"):
        if not _is_int(getattr(cfg, name)):
            raise ConfigError(f"{name} must be a whole number.")
    for name in ("vram_reserve_mib", "vram_reserve_later_mib"):
        value = getattr(cfg, name)
        if value is not None and not _is_int(value):
            raise ConfigError(f"{name} must be a whole number or empty.")

    if not cfg.model and not cfg.model_path:
        raise ConfigError("Set a version (e.g. Q2_0) or a folder with your own model files.")
    if cfg.model and not MODEL_SIZE.fullmatch(cfg.model):
        raise ConfigError(f"{cfg.model!r} is not a version: letters, digits, '_', '.', '-'.")
    if cfg.family and not FAMILY.fullmatch(cfg.family):
        raise ConfigError(f"{cfg.family!r} is not a model family: lowercase letters, digits, '_', '.', '-'.")
    if cfg.model_path:
        cfg.model_path = str(_inside_roots(cfg.model_path, roots))
    if not CONTEXT.fullmatch(cfg.context):
        raise ConfigError(f"{cfg.context!r} is not a context size: e.g. 32k, 128k, 1m or a number of tokens.")
    _one_of("kv", cfg.kv, KV_FORMATS)
    _one_of("vision", cfg.vision, VISION_MODES)
    _one_of("mtp", cfg.mtp, MTP_MODES)
    _one_of("ram_mode", cfg.ram_mode, RAM_MODES)
    if not 0 <= cfg.parallel <= 8:
        raise ConfigError("parallel must be 0 (fit to the cards) or 1 to 8.")
    if cfg.ram_mode == "budget":
        if not 1 <= cfg.ram_budget_gib <= 4096:
            raise ConfigError("The RAM budget must be 1 to 4096 GiB.")
    for name in ("vram_reserve_mib", "vram_reserve_later_mib"):
        value = getattr(cfg, name)
        if value is not None and not 0 <= value <= 262144:
            raise ConfigError(f"{name} must be 0 to 262144 MiB.")
    cfg.gpu_roles = normalize_roles(cfg.gpu_roles)
    if not isinstance(cfg.extra_set, list) or not all(isinstance(x, str) for x in cfg.extra_set):
        raise ConfigError("extra_set must be a list of KEY=VALUE texts.")
    cfg.extra_set = [x.strip() for x in cfg.extra_set if x.strip()]
    if len(cfg.extra_set) > MAX_SET_ENTRIES:
        raise ConfigError(f"At most {MAX_SET_ENTRIES} advanced settings.")
    for entry in cfg.extra_set:
        if not SET_ENTRY.fullmatch(entry):
            raise ConfigError(f"{entry!r} is not KEY=VALUE (no spaces, the key made of letters, digits, '_', '.', '-').")
        key = entry.split("=", 1)[0].lstrip("-").lower().replace("_", "-")
        if key in MANAGED_SET_NAMES:
            raise ConfigError(f"{key} is set by the form or the launcher, not in the advanced list.")
    if len(cfg.notes) > MAX_NOTES:
        raise ConfigError(f"Notes are limited to {MAX_NOTES} characters.")


def _is_int(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _one_of(name: str, value: str, allowed: tuple[str, ...]) -> None:
    if value not in allowed:
        shown = ", ".join(repr(a) for a in allowed)
        raise ConfigError(f"{name} must be one of {shown}.")


def _inside_roots(raw: str, roots: tuple[Path, ...]) -> Path:
    path = Path(raw)
    if not path.is_absolute():
        raise ConfigError("The model folder must be an absolute path inside the container, e.g. /data/gguf/IQ3_S.")
    resolved = Path(os.path.realpath(path))
    for root in roots:
        root_real = Path(os.path.realpath(root))
        if resolved == root_real or root_real in resolved.parents:
            return resolved
    shown = ", ".join(str(r) for r in roots)
    raise ConfigError(f"The model folder must be inside {shown}.")


def normalize_roles(text: str) -> str:
    """"0=main, 1=helper" -> "0=main,1=helper". Empty means the engine's choice. Exactly one main."""
    if not text:
        return ""
    seen: dict[int, str] = {}
    for part in text.split(","):
        index, sep, role = part.strip().partition("=")
        index, role = index.strip(), role.strip().lower()
        if not sep or not index.isdigit() or role not in ROLES:
            raise ConfigError(f"{part.strip()!r} is not a card role: use e.g. 0=main,1=helper (main, helper or off).")
        if int(index) in seen:
            raise ConfigError(f"Card {index} has two roles.")
        seen[int(index)] = role
    mains = [i for i, r in seen.items() if r == "main"]
    if len(mains) != 1:
        raise ConfigError("Give exactly one card the role main.")
    return ",".join(f"{i}={r}" for i, r in sorted(seen.items()))


def to_flags(cfg: ModelConfig) -> list[str]:
    """`strata launch` flags for this config. The fixed flags (port, access, preset) come from the launcher."""
    flags: list[str] = []
    if cfg.model:
        flags += ["--model", cfg.model]
    if cfg.family:
        flags += ["--family", cfg.family]
    if cfg.model_path:
        flags += ["--model-path", cfg.model_path]
    flags += ["--context", cfg.context, "--kv", cfg.kv]
    flags += ["--vision", cfg.vision]  # always explicit, the defaults disagree (IMPLEMENTATION.md, 5.11)
    if cfg.mtp:
        flags += ["--mtp", cfg.mtp]
    if cfg.parallel:
        flags += ["--parallel", str(cfg.parallel)]
    if cfg.gpu_roles:
        flags += ["--gpu-roles", cfg.gpu_roles]  # roles only, never --gpu (IMPLEMENTATION.md, 5.2)
    if cfg.ram_mode == "resident-experts":
        flags += ["--set", "resident-experts="]
    elif cfg.ram_mode == "budget":
        flags += ["--set", f"resident-budget-gib={cfg.ram_budget_gib}"]
    elif cfg.ram_mode == "mmap-experts":
        flags += ["--set", "mmap-experts="]
    if cfg.vram_reserve_mib is not None:
        flags += ["--set", f"vram-reserve-mib={cfg.vram_reserve_mib}"]
    if cfg.vram_reserve_later_mib is not None:
        flags += ["--set", f"vram-reserve-later-mib={cfg.vram_reserve_later_mib}"]
    for entry in cfg.extra_set:
        flags += ["--set", entry]
    return flags


# ---- files


def dumps(cfg: ModelConfig) -> str:
    """Flat TOML: strings, integers and one list of strings. Unset optional values are left out."""
    lines = ["# Strata web UI settings for one model. Edit by hand or in the web UI; reload the page after a change."]
    for f in fields(ModelConfig):
        value = getattr(cfg, f.name)
        if value is None:
            continue
        lines.append(f"{f.name} = {_toml_value(value)}")
    return "\n".join(lines) + "\n"


def _toml_value(value: object) -> str:
    if isinstance(value, bool):
        raise TypeError("booleans are not used in these files")
    if isinstance(value, int):
        return str(value)
    if isinstance(value, str):
        return _toml_string(value)
    if isinstance(value, list):
        return "[" + ", ".join(_toml_string(v) for v in value) + "]"
    raise TypeError(f"cannot write {type(value).__name__}")


def _toml_string(text: str) -> str:
    out = []
    for ch in text:
        if ch == "\\":
            out.append("\\\\")
        elif ch == '"':
            out.append('\\"')
        elif ch == "\n":
            out.append("\\n")
        elif ch == "\t":
            out.append("\\t")
        elif ord(ch) < 0x20 or ord(ch) == 0x7F:
            out.append(f"\\u{ord(ch):04x}")
        else:
            out.append(ch)
    return '"' + "".join(out) + '"'


class ConfigStore:
    def __init__(self, folder: Path, owner: Owner, model_path_roots: tuple[Path, ...]) -> None:
        self.folder = Path(folder)
        self._owner = owner
        self._roots = model_path_roots

    def path_of(self, name: str) -> Path:
        try:
            check_model_name(name)
        except LaunchError as exc:
            raise ConfigError(str(exc)) from None
        return self.folder / f"{name}.toml"

    def names(self) -> list[str]:
        return [p.stem for p in sorted(self.folder.glob("*.toml")) if _valid_name(p.stem)]

    def invalid_files(self) -> list[str]:
        """TOML files whose name is not a valid model name; the UI lists them so they are not lost."""
        return [p.name for p in sorted(self.folder.glob("*.toml")) if not p.name.startswith(".") and not _valid_name(p.stem)]

    def load(self, name: str) -> ModelConfig:
        path = self.path_of(name)
        try:
            data = tomllib.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            raise ConfigError(f"There are no settings for {name}.") from None
        except (OSError, tomllib.TOMLDecodeError, UnicodeDecodeError) as exc:
            raise ConfigError(f"{path.name} cannot be read: {exc}") from None
        try:
            return from_dict(data, self._roots)
        except ConfigError as exc:
            raise ConfigError(f"{path.name}: {exc}") from None

    def save(self, name: str, cfg: ModelConfig) -> None:
        path = self.path_of(name)
        text = dumps(cfg)
        tomllib.loads(text)  # the writer must always produce valid TOML
        fd, tmp_name = tempfile.mkstemp(dir=self.folder, prefix=f".{path.name}.", suffix=".tmp")
        tmp = Path(tmp_name)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(text)
            set_file_owner(tmp, self._owner)
            os.replace(tmp, path)
        finally:
            tmp.unlink(missing_ok=True)  # only left after a failure; os.replace moved it otherwise

    def delete(self, name: str) -> None:
        path = self.path_of(name)
        try:
            path.unlink()
        except FileNotFoundError:
            raise ConfigError(f"There are no settings for {name}.") from None


def _valid_name(name: str) -> bool:
    try:
        check_model_name(name)
    except LaunchError:
        return False
    return True
