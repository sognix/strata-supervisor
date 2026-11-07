"""Supervisor entry point: start checks, API-port placeholder, launch control and the web UI.

Nothing is loaded at container start (IMPLEMENTATION.md, "Goals and scope"). On SIGTERM the launch control
stops everything within its budget, well under Unraid's DOCKER_TIMEOUT of 10 s.
"""

from __future__ import annotations

import os
import signal
import sys
import threading
import time
from pathlib import Path

from .core.commands import run_command
from .core.logbuf import log  # printed and kept for the web UI's Logs page
from .core.ownership import Owner, owner_from_env
from .host.gpus import source_for_backend
from .host.startup import Paths, Report, run_checks
from .strata import model_config
from .strata.launcher import Launcher
from .strata.placeholder import Placeholder
from .web import api, server

PYTHON_BIN = Path("/opt/strata/terminal/python/bin/python3")


def print_report(report: Report) -> None:
    for note in report.notes:
        log("[i]", note)
    for warning in report.warnings:
        log("[!]", warning)
    for error in report.errors:
        log("[X]", error)
    if report.memory_limit_bytes is not None:
        log("[i]", f"Container RAM limit: {report.memory_limit_bytes / 1024**3:.1f} GiB.")
    if report.can_load:
        log("[i]", f"Start checks passed (backend {report.backend}).")
    else:
        log("[X]", "Start checks failed: loading a model stays blocked until this is fixed and the container is restarted.")


def print_gpus(backend: str | None) -> None:
    source = source_for_backend(backend, run_command)
    if source is None:
        log("[!]", f"No card query for backend {backend!r}.")
        return
    result = source.query()
    if result.error:
        log("[X]", result.error)
    for gpu in result.gpus:
        vram = f"{gpu.vram_used_mib}/{gpu.vram_total_mib} MiB used" if gpu.vram_total_mib is not None else "VRAM unknown"
        log("[i]", f"Card {gpu.index}: {gpu.name} ({gpu.pci_bus_id}), {vram}.")


def main(paths: Paths | None = None, python_bin: Path = PYTHON_BIN) -> int:
    """`paths` and `python_bin` are only changed by the local dev run (tests/dev_run.py)."""
    env = dict(os.environ)
    paths = paths or Paths()
    report = run_checks(paths, env)
    print_report(report)
    print_gpus(report.backend)
    try:
        owner = owner_from_env(env)
    except ValueError:
        owner = Owner(99, 100)  # already reported by the start checks

    placeholder = Placeholder(report.port)
    if not placeholder.hold():
        log("[X]", f"{placeholder.error} The API port placeholder is not running.")
    launch_control = Launcher(paths.strata_bin, port=report.port, data_dir=paths.data / "Strata-data",
                              api_key=report.api_key, placeholder=placeholder)
    app = api.AppState(
        report=report,
        launcher=launch_control,
        placeholder=placeholder,
        # Own GGUF files: inside /data, or a folder mounted read-only at /models as Strata's DOCKER.md suggests.
        store=model_config.ConfigStore(paths.models_config, owner, model_path_roots=(paths.data, Path("/models"))),
        gpu_source=source_for_backend(report.backend, run_command),
        strata_bin=paths.strata_bin,
        python_bin=python_bin,
        engine_log_dir=paths.data / "state" / "strata" / "launch",
        strata_models_dir=paths.data / "Strata-data" / "models",
        allowed_hosts=server.parse_allowed_hosts(env.get("ALLOWED_HOSTS", "")),
    )
    try:
        http_server = server.make_server(app, report.ui_port)
    except OSError as exc:
        log("[X]", f"The web UI cannot listen on port {report.ui_port} ({exc}). Set UI_PORT to a free port.")
        placeholder.release()
        return 1
    threading.Thread(target=http_server.serve_forever, kwargs={"poll_interval": 0.5}, daemon=True).start()
    log("[i]", f"Web UI on port {report.ui_port}, API placeholder on port {report.port}. No model is loaded.")

    # The handler only sets a flag. No signal mask, because child processes would inherit it and
    # `strata launch` must see SIGINT (IMPLEMENTATION.md, 5.4). Short sleeps: after SIGTERM the 8 s stop
    # budget must start at once (DOCKER_TIMEOUT is 10 s).
    stopping: list[int] = []

    def on_signal(signum: int, _frame: object) -> None:
        stopping.append(signum)

    signal.signal(signal.SIGTERM, on_signal)
    signal.signal(signal.SIGINT, on_signal)
    ticks = 0
    while not stopping:
        time.sleep(0.2)
        ticks += 1
        if ticks % 5 == 0:
            launch_control.tick()
    log("[i]", f"Got {signal.Signals(stopping[0]).name}, shutting down.")
    launch_control.shutdown()
    placeholder.release()
    http_server.shutdown()
    return 0


if __name__ == "__main__":
    sys.exit(main())
