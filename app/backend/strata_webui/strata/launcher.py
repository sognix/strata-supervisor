"""Control of `strata launch`: load, unload, state and the stop on SIGTERM.

Rules from IMPLEMENTATION.md, section 5:
- The model state comes only from `strata runtime status --profile launch-<name> --output json`.
  Only `stopped` counts as unloaded. A PID says nothing, because the server runs in its own session.
- `strata launch --stop` always exits 0, so its result is checked through the status.
- Cancelling a download or load: SIGINT, wait, SIGTERM, then `launch --stop`. The signals go to the
  launch's whole process group: the launch runs setup.py as a child for the download and does not
  end it on SIGINT (runner.py, run(), v0.2.9). The server is not hit, it has its own session.
- The container stop by Unraid kills after DOCKER_TIMEOUT (10 s), so shutdown() keeps a fixed budget.
- Processes start with argument lists only, never through a shell.

Model names are chosen by the UI and must already be valid Strata slugs, so the profile name is
always `launch-<name>` and never a guess (an unknown profile also reports `stopped`).
"""

from __future__ import annotations

import json
import os
import re
import signal
import subprocess
import threading
import time
from collections import deque
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Protocol

from ..core.commands import run_command

# Same character rules as slug() in strata_cli/launch/settings.py (v0.2.9), which keeps such names unchanged.
MODEL_NAME = re.compile(r"[a-z][a-z0-9_]*(?:-[a-z0-9_]+)*")
MODEL_NAME_MAX = 48
OUTPUT_LINES = 2000

STATUS_TIMEOUT_S = 30
PLAN_TIMEOUT_S = 180
UNLOAD_STOP_TIMEOUT_S = 200  # `launch --stop` waits up to 180 s per profile itself
UNLOAD_POLL_S = 1.0
UNLOAD_SETTLE_S = 20

# Interactive unload: as patient as the official entrypoint.
CANCEL_INT_WAIT_S = 30
CANCEL_TERM_WAIT_S = 5

# Container stop: everything must end well under DOCKER_TIMEOUT (10 s).
SHUTDOWN_BUDGET_S = 8.0
SHUTDOWN_INT_WAIT_S = 3.0
SHUTDOWN_TERM_WAIT_S = 1.5
SHUTDOWN_MIN_STOP_S = 1.0


class LaunchError(Exception):
    """A request the controller refuses, with a message for the UI."""


def check_model_name(name: str) -> str:
    if len(name) > MODEL_NAME_MAX or not MODEL_NAME.fullmatch(name) or "--" in name:
        raise LaunchError(
            f"{name!r} is not a valid model name: use lowercase letters, digits, '-' and '_', "
            f"start with a letter, at most {MODEL_NAME_MAX} characters."
        )
    return name


def profile_of(name: str) -> str:
    return "launch-" + check_model_name(name)


@dataclass(frozen=True)
class RuntimeStatus:
    """Parsed `strata runtime status`. `state` is "unknown" when the answer could not be read."""

    state: str
    raw: dict
    error: str | None = None

    @property
    def unloaded(self) -> bool:
        return self.state == "stopped"


def parse_runtime_status(stdout: str) -> RuntimeStatus:
    line = next((line for line in stdout.splitlines() if line.strip()), "")
    try:
        data = json.loads(line)
    except ValueError:
        return RuntimeStatus("unknown", {}, f"unreadable status output: {stdout.strip()[:200]!r}")
    if not isinstance(data, dict) or not isinstance(data.get("state"), str):
        return RuntimeStatus("unknown", data if isinstance(data, dict) else {}, "status output has no state")
    return RuntimeStatus(data["state"], data, data.get("error") if isinstance(data.get("error"), str) else None)


Runner = Callable[[list[str], float], tuple[int, str]]


class PortHolder(Protocol):
    """The API-port placeholder, see placeholder.py."""

    held: bool

    def try_hold(self) -> bool: ...
    def hold(self) -> bool: ...
    def release(self) -> None: ...


class _NoPlaceholder:
    held = False

    def try_hold(self) -> bool:
        return False

    def hold(self) -> bool:
        return False

    def release(self) -> None:
        pass


@dataclass
class _Launch:
    name: str
    process: subprocess.Popen
    started: float
    exit_code: int | None = None


class Launcher:
    def __init__(
        self,
        strata_bin: Path,
        *,
        port: int,
        data_dir: Path = Path("/data/Strata-data"),
        api_key: str | None = None,
        env: dict[str, str] | None = None,
        runner: Runner = run_command,
        clock: Callable[[], float] = time.monotonic,
        placeholder: PortHolder | None = None,
    ) -> None:
        self._strata = str(strata_bin)
        self._port = port
        self._data_dir = data_dir
        self._api_key = api_key or None
        self._env = dict(os.environ if env is None else env)
        self._runner = runner
        self._clock = clock
        self._lock = threading.Lock()
        self._launch: _Launch | None = None
        self._active: str | None = None
        self._output: deque[str] = deque(maxlen=OUTPUT_LINES)  # the last launch's output
        self._outputs: dict[str, deque[str]] = {}  # the last launch's output per model
        self._busy = False  # an unload or shutdown is in progress
        self._placeholder = placeholder or _NoPlaceholder()

    # ---- state

    @property
    def active(self) -> str | None:
        """The model this supervisor last started, until it is confirmed unloaded."""
        return self._active

    def launch_running(self) -> bool:
        launch = self._launch
        return launch is not None and launch.process.poll() is None

    @property
    def busy(self) -> bool:
        """True while an unload or the container stop runs."""
        return self._busy

    def forget_output(self, name: str) -> None:
        """Drop the kept output of a model whose settings were deleted."""
        if name != self._active:
            self._outputs.pop(name, None)

    def output(self, name: str | None = None) -> list[str]:
        """Output of the last launch, or of the last launch of one model (kept until the container ends)."""
        if name is None:
            return list(self._output)
        return list(self._outputs.get(name, ()))

    def status(self, name: str) -> RuntimeStatus:
        args = [self._strata, "runtime", "status", "--profile", profile_of(name), "--output", "json"]
        code, stdout = self._runner(args, STATUS_TIMEOUT_S)
        status = parse_runtime_status(stdout)
        if status.state == "unknown" and code != 0:
            return RuntimeStatus("unknown", status.raw, f"exit code {code}: {status.error}")
        return status

    # ---- load

    def build_args(self, name: str, model_flags: list[str]) -> list[str]:
        args = [
            self._strata, "launch", "--no-tui", "--yes",
            "--access", "lan", "--port", str(self._port), "--data-dir", str(self._data_dir),
            "--save-preset", check_model_name(name),
        ]
        args += model_flags
        if self._api_key:
            args += ["--api-key-env", "STRATA_LAUNCH_KEY"]
        return args

    def plan(self, name: str, model_flags: list[str]) -> tuple[int, str]:
        """`strata launch --plan --output json` with the same flags a load would use. Changes nothing."""
        args = self.build_args(name, model_flags)
        args.insert(2, "--plan")
        args[3:3] = ["--output", "json"]
        env = dict(self._env)
        env.pop("STRATA_LAUNCH_KEY", None)
        if self._api_key:
            env["STRATA_LAUNCH_KEY"] = self._api_key
        try:
            done = subprocess.run(args, capture_output=True, text=True, timeout=PLAN_TIMEOUT_S, check=False,
                                  env=env, stdin=subprocess.DEVNULL)
        except (OSError, subprocess.TimeoutExpired) as exc:
            return -1, str(exc)
        return done.returncode, done.stdout if done.stdout.strip() else done.stderr

    def start(self, name: str, model_flags: list[str]) -> None:
        """Start `strata launch` in the background. Refuses while anything is loaded or busy."""
        check_model_name(name)
        with self._lock:
            if self._busy:
                raise LaunchError("An unload is in progress.")
            if self.launch_running():
                raise LaunchError(f"{self._launch.name} is still loading.")
            if self._active is not None:
                current = self.status(self._active)
                if not current.unloaded:
                    raise LaunchError(f"{self._active} is {current.state}. Unload it first.")
                self._active = None
            env = dict(self._env)
            env.pop("STRATA_LAUNCH_KEY", None)
            if self._api_key:
                env["STRATA_LAUNCH_KEY"] = self._api_key  # not on the command line, so not in the process list
            # strata launch is Python: into a pipe it would hold its lines back in a buffer, and the download
            # progress would only arrive at the end. Unbuffered, every line reaches the UI at once.
            env["PYTHONUNBUFFERED"] = "1"
            self._output = deque(maxlen=OUTPUT_LINES)
            self._outputs[name] = self._output
            self._placeholder.release()  # `strata launch` treats a busy port as a conflict
            # SIGINT must reach the launch: it is how it cancels a download cleanly. The child starts with
            # default handlers because exec resets handled signals, and this process never ignores SIGINT.
            # Own session, so the whole group (launch plus its setup.py child) can be signalled.
            try:
                process = subprocess.Popen(
                    self.build_args(name, model_flags),
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    errors="replace",
                    env=env,
                    start_new_session=True,
                )
            except OSError as exc:
                self._placeholder.try_hold()
                raise LaunchError(f"Could not start strata launch: {exc}") from exc
            launch = _Launch(name, process, self._clock())
            self._launch = launch
            self._active = name
        threading.Thread(target=self._follow, args=(launch,), daemon=True).start()

    def _follow(self, launch: _Launch) -> None:
        assert launch.process.stdout is not None
        out = self._outputs[launch.name]
        with launch.process.stdout:
            for line in launch.process.stdout:
                out.append(line.rstrip("\n"))
        launch.exit_code = launch.process.wait()
        out.append(f"[strata launch ended with exit code {launch.exit_code}]")

    def tick(self) -> None:
        """Called about once a second: take the API port back when nothing serves on it any more.

        Not while a launch runs (it may bind the port any moment) or during an unload or shutdown.
        While Strata's server listens, the bind just fails.
        """
        with self._lock:
            if self._busy or self._placeholder.held or self.launch_running():
                return
            self._placeholder.try_hold()

    # ---- unload

    def unload(self) -> RuntimeStatus:
        """Stop the active model and wait until the status says `stopped`. Blocks; run it in a thread."""
        with self._lock:
            if self._busy:
                raise LaunchError("An unload is already in progress.")
            name = self._active
            if name is None:
                raise LaunchError("No model was started by this UI.")
            self._busy = True
        out = self._outputs.setdefault(name, deque(maxlen=OUTPUT_LINES))
        try:
            out.append("[unload requested]")
            self._cancel_launch(CANCEL_INT_WAIT_S, CANCEL_TERM_WAIT_S)
            self._runner([self._strata, "launch", "--stop", "--preset", name], UNLOAD_STOP_TIMEOUT_S)
            status = self._wait_unloaded(name, UNLOAD_SETTLE_S)
            out.append(f"[unload finished: {status.state}]")
            if status.unloaded:
                self._active = None
                self._placeholder.hold()
            return status
        finally:
            self._busy = False

    def _wait_unloaded(self, name: str, settle_s: float) -> RuntimeStatus:
        deadline = self._clock() + settle_s
        while True:
            status = self.status(name)
            if status.unloaded or self._clock() >= deadline:
                return status
            time.sleep(UNLOAD_POLL_S)

    def _cancel_launch(self, int_wait_s: float, term_wait_s: float) -> None:
        launch = self._launch
        if launch is None:
            return
        group = launch.process.pid  # start_new_session: the group ID is the launch's PID
        if launch.process.poll() is not None:
            # The launch has ended, but a child (setup.py) may still run in its group. The group ID
            # stays reserved while the group has members, so this cannot hit an unrelated process.
            _signal_group(group, signal.SIGKILL)
            return
        for sig, wait_s in ((signal.SIGINT, int_wait_s), (signal.SIGTERM, term_wait_s)):
            if not _signal_group(group, sig):
                return
            try:
                launch.process.wait(timeout=wait_s)
                break
            except subprocess.TimeoutExpired:
                continue
        else:
            _signal_group(group, signal.SIGKILL)
            try:
                launch.process.wait(timeout=1)
            except subprocess.TimeoutExpired:
                pass
        # The launch may have ended while a child (setup.py) still runs in its group.
        _signal_group(group, signal.SIGKILL)

    # ---- container stop

    def shutdown(self, budget_s: float = SHUTDOWN_BUDGET_S) -> None:
        """Stop everything within budget_s seconds. Called once on SIGTERM; no status polling."""
        start = self._clock()
        with self._lock:
            self._busy = True
            name = self._active
        self._cancel_launch(SHUTDOWN_INT_WAIT_S, SHUTDOWN_TERM_WAIT_S)
        if name is None:
            return
        remaining = budget_s - (self._clock() - start)
        self._runner([self._strata, "launch", "--stop", "--preset", name], max(remaining, SHUTDOWN_MIN_STOP_S))


def _signal_group(group: int, sig: signal.Signals) -> bool:
    """Signal a process group. False if the group is gone."""
    try:
        os.killpg(group, sig)
    except ProcessLookupError:
        return False
    except PermissionError:
        return False
    return True
