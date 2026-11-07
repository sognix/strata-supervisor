"""What the web API does: status, model settings, plan, load, unload, logs. No HTTP in here (see server.py)."""

from __future__ import annotations

import os
import re
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from ..core import commands, logbuf
from ..host import memory
from ..host.gpus import GpuSource
from ..host.startup import Report
from ..strata import catalog
from ..strata.engine_report import memory_report
from ..strata import model_files, own_models
from ..strata import model_config as mc
from ..strata.launcher import LaunchError, Launcher
from ..strata.placeholder import Placeholder

GIB = 1024 ** 3

STATUS_CACHE_S = 2.0

# Strata prints the start of the key in its launch summary ("API key  647ed19ee7••••"). The key is
# shown only on request (IMPLEMENTATION.md, 5), so that part is hidden in every log the UI serves.
KEY_LINE = re.compile(r"^(\s*API key\s+)\S+")

ANSI = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")


def versions(env: dict[str, str] | None = None) -> dict:
    """Versions for the sidebar, set by the Dockerfile from the build arguments. A local run has none of them."""
    env = os.environ if env is None else env
    supervisor = env.get("SUPERVISOR_VERSION") or "dev"
    commit = env.get("SUPERVISOR_COMMIT") or ""
    if supervisor == "dev" and commit:
        supervisor = f"dev ({commit})"
    return {
        "supervisor": supervisor,
        "strata": env.get("SUPERVISOR_STRATA_VERSION") or "unknown",
        "flavor": env.get("SUPERVISOR_STRATA_FLAVOR") or "",
    }


@dataclass
class AppState:
    """Everything the API needs. Built once by the supervisor."""

    report: Report
    launcher: Launcher
    placeholder: Placeholder
    store: mc.ConfigStore
    gpu_source: GpuSource | None
    strata_bin: Path
    python_bin: Path
    engine_log_dir: Path
    allowed_hosts: frozenset[str] = frozenset()
    cgroup_dir: Path = Path("/sys/fs/cgroup")
    meminfo: Path = Path("/proc/meminfo")
    models_root: Path = Path("/models")           # "Own GGUF files" in the template, mounted read-only
    strata_models_dir: Path | None = None         # the models Strata downloaded itself, for their architectures
    runner: Callable[[list[str], float], tuple[int, str]] = commands.run_command
    unload_result: dict | None = None
    unload_error: str | None = None
    _status_cache: commands.Cached | None = field(default=None, repr=False)
    _models_cache: commands.Cached | None = field(default=None, repr=False)
    _catalog_cache: commands.Cached | None = field(default=None, repr=False)
    _gpu_cache: commands.Cached | None = field(default=None, repr=False)
    _own_cache: commands.Cached | None = field(default=None, repr=False)
    _trained_cache: commands.Cached | None = field(default=None, repr=False)

    def __post_init__(self) -> None:
        self._gpu_cache = commands.Cached(self._query_gpus, STATUS_CACHE_S)  # one nvidia-smi run per 2 s, not per tab
        self._status_cache = commands.Cached(self._model_state, STATUS_CACHE_S)
        self._models_cache = commands.Cached(lambda: catalog.installed_models(self.strata_bin, self.runner), 30)
        self._trained_cache = commands.Cached(
            lambda: own_models.strata_contexts(self.strata_models_dir) if self.strata_models_dir else {}, 60)
        self._own_cache = commands.Cached(lambda: own_models.scan(self.models_root, self.strata_models_dir), 10)
        self._catalog_cache = commands.Cached(lambda: catalog.model_catalog(self.python_bin, self.strata_bin, self.runner), 600)

    # ---- read

    def _model_state(self) -> dict:
        active = self.launcher.active
        state: dict = {"active": active, "launch_running": self.launcher.launch_running(), "busy": self.launcher.busy}
        if active:
            status = self.launcher.status(active)
            state.update(state=status.state, error=status.error,
                         phase=status.raw.get("phase"), exit_code=status.raw.get("exit_code"))
        else:
            state.update(state="stopped", error=None, phase=None, exit_code=None)
        state["download"] = self._download_size(active) if active and state["launch_running"] else None
        return state

    def _download_size(self, name: str) -> dict | None:
        """While a Strata model loads: how big its folder is now and how big the download is in all.

        A second source for the download progress, independent of what `strata launch` prints. The page shows a
        percentage only while the folder grows. None for own GGUF files or when the version is not in the catalog.
        """
        if self.strata_models_dir is None:
            return None
        try:
            cfg = self.store.load(name)
        except mc.ConfigError:
            return None
        if cfg.model_path:
            return None
        rows = [r for r in (self._catalog_cache.get().get("rows") or [])
                if str(r.get("model", "")).lower() == cfg.model.lower() and (not cfg.family or r.get("family") == cfg.family)]
        if len(rows) != 1 or not rows[0].get("folder") or not isinstance(rows[0].get("download_gb"), (int, float)):
            return None
        wanted = str(rows[0]["folder"]).lower()
        try:
            folder = next((p for p in self.strata_models_dir.iterdir() if p.name.lower() == wanted), None)
        except OSError:
            folder = None
        size = model_files.folder_bytes(folder) if folder else 0
        return {"bytes": size, "total_bytes": int(rows[0]["download_gb"] * 1e9)}

    def _query_gpus(self) -> dict:
        if self.gpu_source is None:
            return {"gpus": [], "gpu_error": None}
        result = self.gpu_source.query()
        return {"gpus": [{**g.__dict__, "vram_free_mib": g.vram_free_mib} for g in result.gpus], "gpu_error": result.error}

    def status(self) -> dict:
        r = self.report
        gpu_state = self._gpu_cache.get()
        gpus, gpu_error = gpu_state["gpus"], gpu_state["gpu_error"]
        model = dict(self._status_cache.get())
        model["busy"] = self.launcher.busy
        model["launch_running"] = self.launcher.launch_running()
        return {
            "checks": {"errors": r.errors, "warnings": r.warnings, "notes": r.notes, "backend": r.backend,
                       "can_load": r.can_load, "memory_limit_bytes": r.memory_limit_bytes},
            "model": model,
            "unload": {"result": self.unload_result, "error": self.unload_error},
            "placeholder": {"held": self.placeholder.held, "error": self.placeholder.error, "port": r.port},
            "gpus": gpus, "gpu_error": gpu_error,
            "memory": memory.read_meminfo(self.meminfo),
            "limits": memory.memory_limits(self.cgroup_dir, self.meminfo),
            "container_memory": memory.container_usage(self.cgroup_dir),
            "ports": {"api": r.port, "ui": r.ui_port},
            "versions": versions(),
        }

    def models(self) -> dict:
        configs = []
        for name in self.store.names():
            try:
                cfg = self.store.load(name)
                configs.append({"name": name, "config": cfg.to_dict(), "error": None})
            except mc.ConfigError as exc:
                configs.append({"name": name, "config": None, "error": str(exc)})
        catalog = self._catalog_cache.get()
        trained = self._trained_cache.get()
        if trained and catalog.get("rows"):
            # The truth for a downloaded model is its file's header; the catalog's figure stays for the others.
            rows = [{**r, "trained_context": trained.get(str(r.get("folder") or "").lower())} for r in catalog["rows"]]
            catalog = {**catalog, "rows": rows}
        return {"configs": configs, "invalid_files": self.store.invalid_files(),
                "installed": self._models_cache.get(), "catalog": catalog}

    def model_files(self) -> dict:
        """The Models page: Strata's downloads and the own GGUF folders with their sizes."""
        if self.strata_models_dir is None:
            return {"strata": [], "own": [], "error": "The data folder is not known."}
        return model_files.listing(self.strata_models_dir.parent, self.models_root)

    def delete_model_file(self, body: dict) -> dict:
        """Delete one entry of the Models page. Refused while a model is loaded, loading or unloading."""
        if self.strata_models_dir is None:
            raise mc.ConfigError("The data folder is not known.")
        if self.launcher.active or self.launcher.busy or self.launcher.launch_running():
            raise LaunchError("A model is loaded or loading. Unload it first, so no file in use is deleted.")
        path = body.get("path")
        if not isinstance(path, str) or not path:
            raise mc.ConfigError("Say which entry to delete.")
        try:
            result = model_files.delete(path, self.strata_models_dir.parent, self.models_root)
        except model_files.FileError as exc:
            raise mc.ConfigError(str(exc)) from None
        for cache in (self._own_cache, self._trained_cache, self._catalog_cache):   # "on disk" and the folder list change
            cache.invalidate()
        return result

    def own_models(self) -> dict:
        """The folders under /models for the form's GGUF list. Read again at most every 10 s."""
        return self._own_cache.get()

    def _redact(self, lines: list[str]) -> list[str]:
        key = self.report.api_key or ""
        out = []
        for line in lines:
            if key:
                line = line.replace(key, "••••••••")
            out.append(KEY_LINE.sub(r"\1(hidden, use the API key button)", ANSI.sub("", line)))
        return out

    def logs(self) -> dict:
        """The Logs page: supervisor lines, the last launch and the engine log of the active model."""
        active = self.launcher.active
        engine = logbuf.tail(self.engine_log_dir / f"{active}.engine.log") if active else []
        return {"supervisor": self._redact(logbuf.lines()[-400:]), "launch": self._redact(self.launcher.output()[-400:]),
                "engine": self._redact(engine), "model": active}

    def model_logs(self, name: str) -> dict | None:
        """One model: its last launch output (this container run) and its engine log file, also when not loaded.

        The engine log of a model that is not active is from an earlier run, so its time is returned too.
        None if the UI knows nothing about this name.
        """
        path = self.engine_log_dir / f"{name}.engine.log"
        try:
            mtime = path.stat().st_mtime
        except OSError:
            mtime = None
        launch = self.launcher.output(name)
        if mtime is None and not launch and name not in self.store.names():
            return None
        return {"model": name, "active": self.launcher.active == name, "launch": self._redact(launch[-400:]),
                "engine": self._redact(logbuf.tail(path)) if mtime is not None else [], "engine_mtime": mtime,
                "memory": memory_report(path) if mtime is not None else None}

    # ---- change

    def _check_budget(self, cfg: mc.ModelConfig) -> None:
        """The RAM budget may be at most the RAM plus swap this container has (DESIGN.md, section 4)."""
        if cfg.ram_mode != "budget":
            return
        limits = memory.memory_limits(self.cgroup_dir, self.meminfo)
        if limits.get("ram_bytes") is None:
            return  # unknown: nothing to compare against
        total_gib = (limits["ram_bytes"] + (limits.get("swap_bytes") or 0)) // GIB
        if cfg.ram_budget_gib > total_gib:
            raise mc.ConfigError(f"The RAM budget can be at most {total_gib} GiB, the RAM plus swap this container has.")

    def save(self, name: str, body: dict) -> dict:
        cfg = mc.from_dict(body, self.store._roots)
        self._check_budget(cfg)
        self.store.save(name, cfg)
        return {"saved": name}

    def delete(self, name: str) -> dict:
        if self.launcher.active == name:
            raise mc.ConfigError(f"{name} is the loaded model. Unload it first.")
        self.store.delete(name)
        self.launcher.forget_output(name)
        return {"deleted": name}

    def plan(self, name: str, body: dict | None = None) -> dict:
        """Strata's plan for saved settings, or for the form's unsaved settings when `body` is given."""
        cfg = mc.from_dict(body, self.store._roots) if body else self.store.load(name)
        code, stdout = self.launcher.plan(name, mc.to_flags(cfg))
        data = commands.first_json(stdout)
        if isinstance(data, dict):
            return {"exit_code": code, "plan": data}
        return {"exit_code": code, "plan": None, "text": stdout.strip()[-4000:]}

    def load(self, name: str) -> dict:
        if not self.report.can_load:
            raise LaunchError("The start checks failed, so loading is blocked. See the problems at the top.")
        cfg = self.store.load(name)
        self.unload_result = self.unload_error = None
        self.launcher.start(name, mc.to_flags(cfg))
        self._status_cache.get(fresh=True)
        return {"loading": name}

    def unload(self) -> dict:
        if self.launcher.active is None:
            raise LaunchError("No model was started by this UI.")
        if self.launcher.busy:
            raise LaunchError("An unload is already in progress.")
        self.unload_result = self.unload_error = None

        def work() -> None:
            try:
                result = self.launcher.unload()
                self.unload_result = {"state": result.state, "error": result.error}
            except LaunchError as exc:
                self.unload_error = str(exc)
            self._status_cache.get(fresh=True)

        threading.Thread(target=work, daemon=True).start()
        return {"unloading": self.launcher.active}

    def api_key(self) -> dict:
        return {"api_key": self.report.api_key}
