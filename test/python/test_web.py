"""Tests for the web API against the fake strata CLI. Run: python3 -m unittest discover -s tests"""

from __future__ import annotations

import http.client
import json
import os
import socket
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "app" / "backend"))

from strata_webui.strata import model_config  # noqa: E402
from strata_webui.web import api, server  # noqa: E402
from strata_webui.strata.launcher import Launcher  # noqa: E402
from strata_webui.core.ownership import Owner  # noqa: E402
from strata_webui.strata.placeholder import Placeholder  # noqa: E402
from strata_webui.host.startup import Report  # noqa: E402

FAKE = Path(__file__).resolve().parent / "fake_strata.py"


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


class CachedTest(unittest.TestCase):
    def test_errors_expire_sooner(self) -> None:
        from strata_webui.core import commands
        calls = []
        cache = commands.Cached(lambda: calls.append(1) or {"error": "probe failed"}, ttl_s=600)
        cache.get()
        cache._at -= commands.ERROR_TTL_S + 1  # pretend time passed
        cache.get()
        self.assertEqual(len(calls), 2)
        ok = commands.Cached(lambda: calls.append(1) or {"error": None}, ttl_s=600)
        ok.get()
        ok._at -= commands.ERROR_TTL_S + 1
        ok.get()
        self.assertEqual(len(calls), 3)


class MemoryLimitsTest(unittest.TestCase):
    def test_sources(self) -> None:
        from strata_webui.host import memory
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            meminfo = root / "meminfo"
            meminfo.write_text("MemTotal: 1048576 kB\nSwapTotal: 2097152 kB\n")
            # no cgroup files at all: both from the host
            self.assertEqual(memory.memory_limits(root, meminfo),
                             {"ram_bytes": 1024**3, "ram_from": "host", "swap_bytes": 2 * 1024**3, "swap_from": "host"})
            (root / "memory.max").write_text("max\n")
            (root / "memory.swap.max").write_text("536870912\n")
            self.assertEqual(memory.memory_limits(root, meminfo),
                             {"ram_bytes": 1024**3, "ram_from": "host", "swap_bytes": 512 * 1024**2, "swap_from": "container"})


class WebTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        root = Path(self._tmp.name)
        os.environ["FAKE_STRATA_STATE"] = str(root / "fake-state")
        os.environ["FAKE_LAUNCH_SECONDS"] = "0.3"
        self.addCleanup(lambda: [os.environ.pop(k, None) for k in ("FAKE_STRATA_STATE", "FAKE_LAUNCH_SECONDS")])
        (root / "config" / "models").mkdir(parents=True)
        self.report = Report(backend="cuda", engine_usable=True, port=free_port(), ui_port=free_port(), api_key="k" * 24)
        self.placeholder = Placeholder(self.report.port, host="127.0.0.1")
        self.placeholder.hold()
        self.addCleanup(self.placeholder.release)
        launcher = Launcher(FAKE, port=self.report.port, data_dir=root / "Strata-data", api_key=self.report.api_key,
                            placeholder=self.placeholder)
        self.app = api.AppState(
            report=self.report, launcher=launcher, placeholder=self.placeholder,
            store=model_config.ConfigStore(root / "config" / "models", Owner(os.getuid(), os.getgid()), (root,)),
            gpu_source=None, strata_bin=FAKE, python_bin=Path(sys.executable), engine_log_dir=root / "logs",
        )
        self.server = server.make_server(self.app, self.report.ui_port, host="127.0.0.1")
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        self.addCleanup(self._tmp.cleanup)

    def call(self, method: str, path: str, body: object = None, headers: dict | None = None) -> tuple[int, dict]:
        conn = http.client.HTTPConnection("127.0.0.1", self.report.ui_port, timeout=10)
        all_headers = {"Content-Type": "application/json"} if body is not None else {}
        all_headers.update(headers or {})
        raw = json.dumps(body).encode() if body is not None and not isinstance(body, bytes) else body
        try:
            conn.request(method, path, body=raw, headers=all_headers)
            response = conn.getresponse()
            data = response.read()
            return response.status, json.loads(data) if data and response.getheader("Content-Type", "").startswith("application/json") else {}
        finally:
            conn.close()

    def test_page_and_health(self) -> None:
        conn = http.client.HTTPConnection("127.0.0.1", self.report.ui_port, timeout=5)
        conn.request("GET", "/")
        response = conn.getresponse()
        self.assertEqual(response.status, 200)
        self.assertIn("default-src 'self'", response.getheader("Content-Security-Policy"))
        self.assertIn(b"<title>Strata Supervisor</title>", response.read())
        conn.close()
        self.assertEqual(self.call("GET", "/api/health"), (200, {"ok": True}))
        self.assertEqual(self.call("GET", "/nope")[0], 404)
        conn = http.client.HTTPConnection("127.0.0.1", self.report.ui_port, timeout=5)
        conn.request("GET", "/logo.svg")
        response = conn.getresponse()
        self.assertEqual((response.status, response.getheader("Content-Type")), (200, "image/svg+xml"))
        self.assertIn(b"<svg", response.read())
        conn.close()

    def test_changes_need_json_and_same_origin(self) -> None:
        good = {"model": "Q2_0"}
        self.assertEqual(self.call("POST", "/api/models/m1", b'{"model": "Q2_0"}', {"Content-Type": "text/plain"})[0], 415)
        status, _ = self.call("POST", "/api/models/m1", good, {"Origin": "http://evil.example"})
        self.assertEqual(status, 403)
        host = f"127.0.0.1:{self.report.ui_port}"
        status, _ = self.call("POST", "/api/models/m1", good, {"Origin": f"http://{host}"})
        self.assertEqual(status, 200)
        self.assertEqual(self.call("POST", "/api/models/Bad", good)[0], 400)
        self.assertEqual(self.call("POST", "/api/models/m1", {"model": "Q2_0", "kv": "q8"})[0], 400)

    def test_allowed_hosts(self) -> None:
        self.app.allowed_hosts = server.parse_allowed_hosts("tower.local, 192.168.1.5")
        self.assertEqual(self.call("GET", "/api/status")[0], 403)
        self.assertEqual(self.call("GET", "/api/status", headers={"Host": "tower.local:8081"})[0], 200)
        self.assertEqual(self.call("GET", "/api/health")[0], 200)  # the health check uses 127.0.0.1
        self.app.allowed_hosts = server.parse_allowed_hosts("::1")
        self.assertEqual(self.call("GET", "/api/status", headers={"Host": "[::1]"})[0], 200)
        self.assertEqual(self.call("GET", "/api/status", headers={"Host": "[::1]:8081"})[0], 200)

    def test_key_only_by_post(self) -> None:
        self.assertEqual(self.call("GET", "/api/key")[0], 404)
        self.assertEqual(self.call("POST", "/api/key", {}), (200, {"api_key": "k" * 24}))
        status, body = self.call("GET", "/api/status")
        self.assertEqual(status, 200)
        self.assertNotIn("k" * 24, json.dumps(body))

    def test_load_and_unload(self) -> None:
        self.assertEqual(self.call("POST", "/api/models/m1", {"model": "Q2_0", "context": "64k"})[0], 200)
        self.assertEqual(self.call("POST", "/api/models/m1/load", {})[0], 202)
        self.assertFalse(self.placeholder.held)
        self.assertEqual(self.call("POST", "/api/models/m1/delete", {})[0], 400)
        deadline = time.monotonic() + 10
        while self.app.launcher.launch_running() and time.monotonic() < deadline:
            time.sleep(0.05)
        status, body = self.call("GET", "/api/status")
        self.assertEqual(body["model"]["active"], "m1")
        self.assertEqual(self.call("POST", "/api/models/m1/load", {})[0], 409)
        self.assertEqual(self.call("POST", "/api/unload", {})[0], 202)
        deadline = time.monotonic() + 10
        while (self.app.launcher.active or self.app.launcher.busy) and time.monotonic() < deadline:
            time.sleep(0.05)
        self.assertIsNone(self.app.launcher.active)
        self.assertTrue(self.placeholder.held)
        launch = self.call("GET", "/api/logs")[1]["launch"]
        self.assertEqual(launch[-3:], ["[strata launch ended with exit code 0]", "[unload requested]", "[unload finished: stopped]"])
        status, body = self.call("GET", "/api/logs/m1")
        self.assertEqual((status, body["active"], body["launch"][-1]), (200, False, "[unload finished: stopped]"))

    def test_key_is_hidden_in_logs(self) -> None:
        self.app.launcher._outputs["m1"] = __import__("collections").deque(
            ["  API key        kkkkkkkkkk••••••••   (show it: ...)", "full key " + "k" * 24 + " here"])
        self.call("POST", "/api/models/m1", {"model": "Q2_0"})
        launch = self.call("GET", "/api/logs/m1")[1]["launch"]
        self.assertEqual(launch[0], "  API key        (hidden, use the API key button)   (show it: ...)")
        self.assertNotIn("k" * 24, launch[1])

    def test_model_logs(self) -> None:
        self.assertEqual(self.call("GET", "/api/logs/unknown")[0], 404)
        self.assertEqual(self.call("GET", "/api/logs/..%2Fetc")[0], 400)
        self.call("POST", "/api/models/m1", {"model": "Q2_0"})
        status, body = self.call("GET", "/api/logs/m1")
        self.assertEqual((status, body["engine"], body["engine_mtime"]), (200, [], None))
        self.app.engine_log_dir.mkdir(parents=True)
        (self.app.engine_log_dir / "m1.engine.log").write_text("old run line 1\nold run line 2\n")
        body = self.call("GET", "/api/logs/m1")[1]
        self.assertEqual(body["engine"], ["old run line 1", "old run line 2"])
        self.assertIsInstance(body["engine_mtime"], float)
        self.assertFalse(body["active"])

    def test_plan_is_nested_and_refusal_is_passed_on(self) -> None:
        self.call("POST", "/api/models/m1", {"model": "Q2_0"})
        status, body = self.call("POST", "/api/models/m1/plan", {})
        self.assertEqual(status, 200)
        self.assertEqual(body["plan"]["plan"]["warnings"], ["512K is more than the model was trained on (256K)."])
        self.call("POST", "/api/models/m2", {"model": "nope"})
        status, body = self.call("POST", "/api/models/m2/plan", {})
        self.assertEqual((body["exit_code"], body["plan"]["ok"]), (2, False))

    def test_plan_of_unsaved_form(self) -> None:
        status, body = self.call("POST", "/api/models/draft/plan", {"config": {"model": "Q2_0", "context": "64k"}})
        self.assertEqual(status, 200)
        self.assertTrue(body["plan"]["ok"])
        self.assertNotIn("draft", self.app.store.names())  # planning saves nothing
        self.assertEqual(self.call("POST", "/api/models/draft/plan", {"config": {"model": "Q2_0", "kv": "q8"}})[0], 400)

    def test_budget_limited_to_ram_and_swap(self) -> None:
        root = Path(self._tmp.name)
        cgroup = root / "cgroup"
        cgroup.mkdir()
        (cgroup / "memory.max").write_text(f"{25 * 1024**3}\n")
        (cgroup / "memory.swap.max").write_text("max\n")
        meminfo = root / "meminfo"
        meminfo.write_text("MemTotal: 50331648 kB\nMemAvailable: 40000000 kB\nSwapTotal: 50331648 kB\nSwapFree: 50331648 kB\n")
        self.app.cgroup_dir, self.app.meminfo = cgroup, meminfo
        limits = self.call("GET", "/api/status")[1]["limits"]
        self.assertEqual(limits, {"ram_bytes": 25 * 1024**3, "ram_from": "container", "swap_bytes": 48 * 1024**3, "swap_from": "host"})
        good = {"model": "Q2_0", "ram_mode": "budget", "ram_budget_gib": 73}
        self.assertEqual(self.call("POST", "/api/models/m1", good)[0], 200)
        status, body = self.call("POST", "/api/models/m1", {**good, "ram_budget_gib": 74})
        self.assertEqual(status, 400)
        self.assertIn("at most 73 GiB", body["error"])

    def test_own_models_route(self) -> None:
        self.app.models_root = Path(self._tmp.name) / "models"
        status, body = self.call("GET", "/api/own-models")
        self.assertEqual((status, body["exists"], body["folders"]), (200, False, []))

    def test_load_blocked_by_failed_checks(self) -> None:
        self.report.errors.append("engine not usable")
        self.call("POST", "/api/models/m1", {"model": "Q2_0"})
        status, body = self.call("POST", "/api/models/m1/load", {})
        self.assertEqual(status, 409)
        self.assertIn("start checks failed", body["error"])


if __name__ == "__main__":
    unittest.main()
