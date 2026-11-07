"""Tests for the launch control against the fake strata CLI. Run: python3 -m unittest discover -s test/python"""

from __future__ import annotations

import json
import os
import stat
import sys
import tempfile
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "app" / "backend"))

from strata_webui.strata import launcher  # noqa: E402

FAKE = Path(__file__).resolve().parent / "fake_strata.py"


def process_alive(pid: int) -> bool:
    """True while pid runs. A zombie counts as gone (state Z in /proc)."""
    try:
        stat_line = Path(f"/proc/{pid}/stat").read_text()
    except OSError:
        return False
    return stat_line.rsplit(")", 1)[1].split()[0] != "Z"


class LauncherTestBase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.state = self.root / "fake-state"
        FAKE.chmod(FAKE.stat().st_mode | stat.S_IXUSR)
        self.env = {"PATH": os.environ.get("PATH", ""), "FAKE_STRATA_STATE": str(self.state)}

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def make(self, api_key: str | None = "secret-key-0123456789", **knobs: str) -> launcher.Launcher:
        env = {**self.env, **knobs}
        os.environ.update({k: v for k, v in env.items() if k.startswith("FAKE_")})  # for status/stop calls
        self.addCleanup(lambda: [os.environ.pop(k, None) for k in env if k.startswith("FAKE_")])
        return launcher.Launcher(FAKE, port=8080, data_dir=self.root / "Strata-data", api_key=api_key, env=env)

    def wait_launch_done(self, ctl: launcher.Launcher, timeout: float = 10) -> None:
        deadline = time.monotonic() + timeout
        while ctl.launch_running() and time.monotonic() < deadline:
            time.sleep(0.02)
        time.sleep(0.3)  # let the follower thread read the status and save the key

    def calls(self) -> list[dict]:
        return [json.loads(line) for line in (self.state / "calls.log").read_text().splitlines()]


class LauncherTest(LauncherTestBase):
    def test_load_then_unload(self) -> None:
        ctl = self.make()
        ctl.start("q2_0-64k", ["--model", "Q2_0", "--context", "64k"])
        self.wait_launch_done(ctl)
        self.assertEqual(ctl.status("q2_0-64k").state, "ready")
        self.assertIn("Strata is serving.", ctl.output())

        launch_call = self.calls()[0]
        self.assertEqual(launch_call["args"][:12], [
            "launch", "--no-tui", "--yes", "--access", "lan", "--port", "8080",
            "--data-dir", str(self.root / "Strata-data"), "--save-preset", "q2_0-64k", "--model"])
        self.assertIn("--api-key-env", launch_call["args"])
        self.assertNotIn("secret-key-0123456789", " ".join(launch_call["args"]))
        self.assertTrue(launch_call["key_env"])

        status = ctl.unload()
        self.assertTrue(status.unloaded)
        self.assertIsNone(ctl.active)

    def test_no_key_given(self) -> None:
        ctl = self.make(api_key=None)
        ctl.start("m1", [])
        self.wait_launch_done(ctl)
        launch_call = self.calls()[0]
        self.assertNotIn("--api-key-env", launch_call["args"])
        self.assertFalse(launch_call["key_env"])

    def test_second_load_refused_while_loaded(self) -> None:
        ctl = self.make()
        ctl.start("m1", [])
        with self.assertRaisesRegex(launcher.LaunchError, "still loading"):
            ctl.start("m2", [])
        self.wait_launch_done(ctl)
        with self.assertRaisesRegex(launcher.LaunchError, "m1 is ready"):
            ctl.start("m2", [])
        ctl.unload()
        ctl.start("m2", [])
        self.wait_launch_done(ctl)
        self.assertEqual(ctl.active, "m2")

    def test_failed_launch_blocks_switch_until_unloaded(self) -> None:
        ctl = self.make(FAKE_LAUNCH_RESULT="failed")
        ctl.start("m1", [])
        self.wait_launch_done(ctl)
        with self.assertRaisesRegex(launcher.LaunchError, "m1 is failed"):
            ctl.start("m2", [])
        self.assertTrue(ctl.unload().unloaded)

    def test_stop_that_does_nothing_is_detected(self) -> None:
        ctl = self.make(FAKE_STOP_FAILS="1")
        ctl.start("m1", [])
        self.wait_launch_done(ctl)
        original = launcher.UNLOAD_SETTLE_S
        launcher.UNLOAD_SETTLE_S = 0.5
        try:
            status = ctl.unload()
        finally:
            launcher.UNLOAD_SETTLE_S = original
        self.assertEqual(status.state, "ready")
        self.assertEqual(ctl.active, "m1")

    def test_cancel_during_download(self) -> None:
        ctl = self.make(FAKE_LAUNCH_SECONDS="30")
        ctl.start("m1", [])
        time.sleep(0.5)
        started = time.monotonic()
        status = ctl.unload()
        self.assertLess(time.monotonic() - started, 5)
        self.assertTrue(status.unloaded)
        self.wait_launch_done(ctl)
        self.assertTrue(any("Cancelled" in line for line in ctl.output()))

    def test_cancel_also_ends_the_download_child(self) -> None:
        pid_file = self.root / "child.pid"
        ctl = self.make(FAKE_LAUNCH_SECONDS="30", FAKE_CHILD_PID_FILE=str(pid_file))
        ctl.start("m1", [])
        deadline = time.monotonic() + 5
        while not pid_file.exists() and time.monotonic() < deadline:
            time.sleep(0.05)
        child = int(pid_file.read_text())
        self.assertTrue(ctl.unload().unloaded)
        deadline = time.monotonic() + 3
        while process_alive(child) and time.monotonic() < deadline:
            time.sleep(0.05)
        self.assertFalse(process_alive(child))

    def test_leftover_child_is_ended_after_the_launch_exited(self) -> None:
        pid_file = self.root / "child.pid"
        ctl = self.make(FAKE_LAUNCH_SECONDS="0.3", FAKE_CHILD_PID_FILE=str(pid_file))
        ctl.start("m1", [])
        self.wait_launch_done(ctl)  # the launch is gone, its child sleeps on
        child = int(pid_file.read_text())
        self.assertTrue(process_alive(child))
        ctl.unload()
        deadline = time.monotonic() + 3
        while process_alive(child) and time.monotonic() < deadline:
            time.sleep(0.05)
        self.assertFalse(process_alive(child))

    def test_shutdown_within_budget_when_launch_ignores_signals(self) -> None:
        ctl = self.make(FAKE_LAUNCH_SECONDS="60", FAKE_IGNORE_SIGNALS="1", FAKE_STOP_SECONDS="30")
        ctl.start("m1", [])
        time.sleep(0.5)
        started = time.monotonic()
        ctl.shutdown()
        elapsed = time.monotonic() - started
        self.assertLess(elapsed, 9.5)
        self.assertFalse(ctl.launch_running())

    def test_shutdown_when_serving(self) -> None:
        ctl = self.make()
        ctl.start("m1", [])
        self.wait_launch_done(ctl)
        started = time.monotonic()
        ctl.shutdown()
        self.assertLess(time.monotonic() - started, 3)
        self.assertEqual(ctl.status("m1").state, "stopped")

    def test_bad_names(self) -> None:
        for name in ("", "Q2_0", "1model", "a--b", "a b", "../x", "a-", "x" * 49):
            with self.assertRaises(launcher.LaunchError, msg=name):
                launcher.check_model_name(name)
        for name in ("q2_0", "iq3_s-512k", "a_b-c", "x" * 48):
            self.assertEqual(launcher.check_model_name(name), name)


class FakePlaceholder:
    def __init__(self) -> None:
        self.held = True
        self.events: list[str] = []

    def try_hold(self) -> bool:
        self.events.append("try_hold")
        self.held = True
        return True

    def hold(self) -> bool:
        self.events.append("hold")
        self.held = True
        return True

    def release(self) -> None:
        self.events.append("release")
        self.held = False


class LauncherPortTest(LauncherTestBase):
    def test_port_released_for_launch_and_taken_back_after_unload(self) -> None:
        ph = FakePlaceholder()
        ctl = self.make()
        ctl._placeholder = ph
        ctl.start("m1", [])
        self.assertEqual(ph.events, ["release"])
        ctl.tick()  # launch running: must not bind
        self.assertEqual(ph.events, ["release"])
        self.wait_launch_done(ctl)
        ctl.unload()
        self.assertEqual(ph.events, ["release", "hold"])

    def test_tick_takes_port_after_failed_launch(self) -> None:
        ph = FakePlaceholder()
        ctl = self.make(FAKE_LAUNCH_RESULT="failed")
        ctl._placeholder = ph
        ctl.start("m1", [])
        self.wait_launch_done(ctl)
        ctl.tick()
        self.assertEqual(ph.events, ["release", "try_hold"])
        ctl.tick()  # already held: nothing more
        self.assertEqual(ph.events, ["release", "try_hold"])


class ParseRuntimeStatusTest(unittest.TestCase):
    def test_stopped(self) -> None:
        status = launcher.parse_runtime_status('{"schema_version": 1, "ok": true, "profile": "launch-a", "state": "stopped"}\n')
        self.assertTrue(status.unloaded)

    def test_stale(self) -> None:
        status = launcher.parse_runtime_status(json.dumps(
            {"schema_version": 1, "ok": False, "profile": "launch-a", "state": "stale", "error": "owner_host_lost", "exit_code": 6}))
        self.assertEqual((status.state, status.error), ("stale", "owner_host_lost"))
        self.assertFalse(status.unloaded)

    def test_garbage_is_unknown_not_stopped(self) -> None:
        for text in ("", "launch-a: stopped", '{"ok": true}', "[]"):
            status = launcher.parse_runtime_status(text)
            self.assertEqual(status.state, "unknown", text)
            self.assertFalse(status.unloaded)


if __name__ == "__main__":
    unittest.main()
