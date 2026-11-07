#!/usr/bin/env python3
"""Fake `strata` CLI for local tests without a GPU (IMPLEMENTATION.md, section 11).

Covers only what the supervisor calls: launch, launch --stop, launch --show-key, runtime status.
State lives in $FAKE_STRATA_STATE (one file per profile). Every call is appended to calls.log there,
together with whether STRATA_LAUNCH_KEY was set, so tests can check that the key never is an argument.

Knobs (environment):
  FAKE_LAUNCH_SECONDS   how long a launch "downloads and loads" (default 0.3)
  FAKE_LAUNCH_RESULT    ready | failed (default ready); failed exits 0 like `"serving": false`
  FAKE_IGNORE_SIGNALS   1: the launch ignores SIGINT and SIGTERM
  FAKE_STOP_SECONDS     how long `launch --stop` takes (default 0)
  FAKE_STOP_FAILS       1: `launch --stop` leaves the state as it is, and still exits 0
  FAKE_BACKEND          backend `components engine` reports (default cuda)
  FAKE_CHILD_PID_FILE   path: the launch starts a child (like setup.py for the download) that ignores
                        nothing and writes its PID here; the launch does not end it on SIGINT
"""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

STATE = Path(os.environ["FAKE_STRATA_STATE"])


def state_file(profile: str) -> Path:
    return STATE / f"{profile}.state"


def read_state(profile: str) -> str:
    try:
        return state_file(profile).read_text().strip()
    except OSError:
        return "stopped"  # like Strata: no state file reports stopped


def write_state(profile: str, state: str) -> None:
    state_file(profile).write_text(state)


def arg_after(args: list[str], flag: str) -> str | None:
    return args[args.index(flag) + 1] if flag in args and args.index(flag) + 1 < len(args) else None


def say(text: str) -> None:
    print(text, flush=True)


def launch(args: list[str]) -> int:
    if "--stop" in args:
        profile = "launch-" + arg_after(args, "--preset")
        time.sleep(float(os.environ.get("FAKE_STOP_SECONDS", "0")))
        if read_state(profile) in ("ready", "starting", "failed", "stale") and os.environ.get("FAKE_STOP_FAILS") != "1":
            write_state(profile, "stopped")
            say(f"Stopped {profile[7:]}.")
        else:
            say("Nothing from `strata launch` is running.")
        return 0
    if "--plan" in args:
        if arg_after(args, "--model") == "nope":
            say(json.dumps({"schema_version": 1, "ok": False, "error": {"code": 2, "message": "There is no model nope."}}))
            return 2
        plan = {"profile": "launch-" + (arg_after(args, "--save-preset") or "x"), "settings": {},
                "warnings": ["512K is more than the model was trained on (256K)."], "flags": " ".join(args[1:]),
                "steps": [], "model": {"title": "Qwen " + (arg_after(args, "--model") or "?")},
                "download": {"gb": 71.0, "text": "71.0 GB to download"}, "drafting": "MTP draft layer"}
        say(json.dumps({"schema_version": 1, "ok": True, "plan": plan}))  # nested, like cmd.py (v0.2.9)
        return 0
    if "--show-key" in args:
        say("fake-key-0123456789abcdef")
        return 0
    profile = "launch-" + arg_after(args, "--save-preset")
    if os.environ.get("FAKE_IGNORE_SIGNALS") == "1":
        signal.signal(signal.SIGINT, signal.SIG_IGN)
        signal.signal(signal.SIGTERM, signal.SIG_IGN)
    write_state(profile, "starting")
    child_pid_file = os.environ.get("FAKE_CHILD_PID_FILE")
    if child_pid_file:
        child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(120)"])
        Path(child_pid_file).write_text(str(child.pid))
    total = float(os.environ.get("FAKE_LAUNCH_SECONDS", "0.3"))
    steps = 10
    try:
        for i in range(steps):
            say(f"    Downloading the model  {i * 10}%")
            time.sleep(total / steps)
    except KeyboardInterrupt:
        write_state(profile, "stopped")
        say("[!] Cancelled; nothing is running.")
        return 130
    result = os.environ.get("FAKE_LAUNCH_RESULT", "ready")
    write_state(profile, result)
    say("Strata is serving." if result == "ready" else "The engine could not start.")
    return 0


def runtime(args: list[str]) -> int:
    profile = arg_after(args, "--profile")
    state = read_state(profile)
    say(json.dumps({"schema_version": 1, "ok": state != "stale", "profile": profile, "state": state}))
    return 0


def main() -> int:
    args = sys.argv[1:]
    STATE.mkdir(parents=True, exist_ok=True)
    with (STATE / "calls.log").open("a") as log:
        log.write(json.dumps({"args": args, "key_env": "STRATA_LAUNCH_KEY" in os.environ}) + "\n")
    if args[:1] == ["launch"]:
        return launch(args)
    if args[:2] == ["runtime", "status"]:
        return runtime(args)
    if args[:2] == ["components", "engine"]:
        backend = os.environ.get("FAKE_BACKEND", "cuda")
        say(json.dumps({"schema_version": 1, "ok": True, "backend": backend, "installed": [], "problems": [],
                        "engine": {"backend": backend, "version": "0.1.55"}, "usable": True, "notice": None}))
        return 0
    if args[:2] == ["models", "list"]:
        installed = [{"id": p.stem[len("launch-"):], "component_status": "complete", "name": p.stem}
                     for p in STATE.glob("launch-*.state")]
        say(json.dumps({"schema_version": 1, "ok": True, "install_dir": "/data/install", "models": installed}))
        return 0
    print(f"fake strata: unsupported command {args}", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
