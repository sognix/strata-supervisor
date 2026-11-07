"""Tests for the start checks. Run from the repo root: python3 -m unittest discover -s test/python"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "app" / "backend"))

from strata_webui.host import startup  # noqa: E402
from strata_webui.core.ownership import DIR_MODE, Owner, owner_from_env  # noqa: E402


def engine_json(*, usable: bool, backend: str | None, engine_backend: str | None = None,
                installed: tuple[str, ...] = (), notice: str | None = None) -> str:
    """Same keys as `strata components engine --output json` at v0.2.9 (engine_component.status())."""
    items = [{"version": "0.1.55", "platform": "linux-x86_64", "backend": b, "root": f"/opt/strata/components/engine/{b}"}
             for b in installed]
    engine = None
    if usable:
        engine = {"version": "0.1.55", "platform": "linux-x86_64", "backend": engine_backend or backend, "exe": "/x/strata-engine"}
    report = {"schema_version": 1, "components_dir": "/opt/strata/components", "platform": "linux-x86_64",
              "backend": backend, "backend_source": "nvidia-smi", "installed": items, "problems": [],
              "engine": engine, "usable": usable, "notice": notice, "acquires": False,
              "pinned": None, "previous": None, "update_notice": None}
    return json.dumps({"schema_version": 1, "ok": usable, **report})


USABLE_CUDA = engine_json(usable=True, backend="cuda", installed=("cuda",))
UNUSABLE = engine_json(usable=False, backend="cuda", installed=("cuda",), notice="Install NVIDIA driver 580.65 or newer.")


class FakeRunner:
    def __init__(self, code: int = 0, stdout: str = USABLE_CUDA) -> None:
        self.code = code
        self.stdout = stdout
        self.calls: list[list[str]] = []

    def __call__(self, args: list[str], timeout_s: int) -> tuple[int, str]:
        self.calls.append(args)
        return self.code, self.stdout


class StartupTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        root = Path(self._tmp.name)
        self.root = root
        (root / "dev").mkdir()
        (root / "dev" / "nvidia0").touch()
        (root / "seed").mkdir()
        (root / "cpuinfo").write_text("processor : 0\nflags : fpu sse avx avx2\n")
        (root / "memory.max").write_text("max\n")
        self.mountinfo = root / "mountinfo"
        self.write_mounts(root / "data", root / "config")
        self.paths = startup.Paths(
            data=root / "data",
            config=root / "config",
            dev=root / "dev",
            llama_seed=root / "seed",
            strata_bin=root / "strata",
            mountinfo=self.mountinfo,
            cpuinfo=root / "cpuinfo",
            memory_max=root / "memory.max",
        )
        self.env = {"PUID": str(os.getuid()), "PGID": str(os.getgid())}

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def write_mounts(self, *mount_points: Path) -> None:
        lines = [f"{30 + i} 1 0:{i} / {p} rw - btrfs /dev/x rw" for i, p in enumerate(mount_points)]
        self.mountinfo.write_text("\n".join(lines) + "\n")

    def run_checks(self, runner: FakeRunner | None = None, env: dict[str, str] | None = None) -> startup.Report:
        return startup.run_checks(self.paths, env if env is not None else self.env, runner or FakeRunner())

    def test_happy_path(self) -> None:
        runner = FakeRunner()
        report = self.run_checks(runner)
        self.assertEqual(report.errors, [])
        self.assertEqual(report.warnings, [])
        self.assertTrue(report.can_load)
        self.assertEqual(report.backend, "cuda")
        self.assertEqual(runner.calls, [[str(self.paths.strata_bin), "components", "engine", "--output", "json"]])
        for name in ("state", "home", "install", "Strata-data"):
            self.assertTrue((self.paths.data / name).is_dir(), name)
        self.assertTrue(self.paths.models_config.is_dir())
        self.assertEqual(self.paths.models_config.stat().st_mode & 0o777, DIR_MODE)
        link = self.paths.install / "third_party" / "llama.cpp"
        self.assertTrue(link.is_symlink())
        self.assertEqual(os.readlink(link), str(self.paths.llama_seed))
        self.assertIsNone(report.memory_limit_bytes)

    def test_engine_not_usable_blocks_loading(self) -> None:
        report = self.run_checks(FakeRunner(stdout=UNUSABLE))
        self.assertFalse(report.can_load)
        self.assertFalse(report.engine_usable)
        self.assertIn("Install NVIDIA driver 580.65 or newer.", report.errors[0])

    def test_engine_command_fails(self) -> None:
        report = self.run_checks(FakeRunner(code=-1, stdout=""))
        self.assertFalse(report.can_load)
        self.assertIn("exit code -1", report.errors[0])

    def test_bad_puid_is_an_error_not_an_exit(self) -> None:
        report = self.run_checks(env={"PUID": "abc"})
        self.assertFalse(report.can_load)
        self.assertIn("PUID", report.errors[0])
        self.assertFalse(self.paths.models_config.exists())

    def test_missing_nvidia_device(self) -> None:
        (self.root / "dev" / "nvidia0").unlink()
        report = self.run_checks()
        self.assertFalse(report.can_load)
        self.assertIn("--gpus all", report.errors[0])

    def test_hip_needs_kfd(self) -> None:
        hip = engine_json(usable=True, backend="hip", installed=("hip",))
        report = self.run_checks(FakeRunner(stdout=hip))
        self.assertIn("/dev/kfd", report.errors[0])
        (self.root / "dev" / "kfd").touch()
        self.assertTrue(self.run_checks(FakeRunner(stdout=hip)).can_load)

    def test_stale_run_config_is_removed_and_valid_one_kept(self) -> None:
        install = self.paths.install
        install.mkdir(parents=True)
        stale = install / "strata-old.json"
        stale.write_text(json.dumps({"engine": {"path": "/opt/strata/components/engine/gone-123/bin"}}))
        kept = install / "strata-ok.json"
        kept.write_text(json.dumps({"model": "/data/Strata-data/models/x"}))
        broken = install / "strata-broken.json"
        broken.write_text("{not json")
        report = self.run_checks()
        self.assertFalse(stale.exists())
        self.assertTrue(kept.exists())
        self.assertTrue(broken.exists())
        removed = [n for n in report.notes if "run settings" in n]
        self.assertEqual(len(removed), 1)
        self.assertIn("strata-old.json", removed[0])

    def test_not_mounted_warns(self) -> None:
        self.write_mounts()
        report = self.run_checks()
        self.assertTrue(report.can_load)
        self.assertEqual(len(report.warnings), 2)
        self.assertIn(str(self.paths.data), report.warnings[0])
        self.assertIn(str(self.paths.config), report.warnings[1])

    def test_mount_path_with_space(self) -> None:
        self.mountinfo.write_text("30 1 0:1 / /mnt/my\\040data rw - btrfs /dev/x rw\n")
        self.assertTrue(startup.is_mount_point(self.mountinfo, Path("/mnt/my data")))
        self.assertFalse(startup.is_mount_point(self.mountinfo, Path("/mnt/my")))

    def test_no_avx2_and_short_key_warn(self) -> None:
        (self.root / "cpuinfo").write_text("flags : fpu sse avx\n")
        report = self.run_checks(env={**self.env, "API_KEY": "short"})
        self.assertTrue(report.can_load)
        self.assertTrue(any("AVX2" in w for w in report.warnings))
        self.assertTrue(any("API_KEY is short" in w for w in report.warnings))

    def test_ports(self) -> None:
        report = self.run_checks()
        self.assertEqual((report.port, report.ui_port), (8080, 8081))
        report = self.run_checks(env={**self.env, "PORT": "9000", "UI_PORT": "9001"})
        self.assertEqual((report.port, report.ui_port), (9000, 9001))
        self.assertTrue(report.can_load)
        for env in ({"PORT": "abc"}, {"UI_PORT": "70000"}, {"PORT": "8081"}):
            self.assertFalse(self.run_checks(env={**self.env, **env}).can_load, env)

    def test_generated_key_is_kept(self) -> None:
        first = self.run_checks()
        self.assertEqual(len(first.api_key), 48)
        key_file = self.paths.key_file
        self.assertEqual(key_file.read_text(), first.api_key + "\n")
        self.assertEqual(key_file.stat().st_mode & 0o777, 0o600)
        self.assertTrue(any("key was made" in n for n in first.notes))
        second = self.run_checks()
        self.assertEqual(second.api_key, first.api_key)
        self.assertFalse(any("key was made" in n for n in second.notes))

    def test_api_key_sources(self) -> None:
        key_file = self.root / "key"
        key_file.write_text("from-file-0123456789\r\n")
        report = self.run_checks(env={**self.env, "API_KEY_FILE": str(key_file)})
        self.assertEqual(report.api_key, "from-file-0123456789")
        report = self.run_checks(env={**self.env, "API_KEY": "from-env-0123456789", "API_KEY_FILE": str(key_file)})
        self.assertEqual(report.api_key, "from-env-0123456789")
        self.assertEqual(self.paths.key_file.read_text(), "from-env-0123456789\n")
        report = self.run_checks(env={**self.env, "API_KEY_FILE": str(self.root / "missing")})
        self.assertFalse(report.can_load)
        self.assertNotIn("from-env", repr(report))

    def test_memory_limit_is_read(self) -> None:
        (self.root / "memory.max").write_text(f"{32 * 1024**3}\n")
        self.assertEqual(self.run_checks().memory_limit_bytes, 32 * 1024**3)

    def test_data_not_writable(self) -> None:
        self.paths.data.mkdir()
        self.paths.data.chmod(0o500)
        try:
            if os.access(self.paths.data, os.W_OK):
                self.skipTest("running as root, permissions are not enforced")
            report = self.run_checks(env={**self.env, "UI_PORT": "9091", "PORT": "9090"})
        finally:
            self.paths.data.chmod(0o700)
        self.assertFalse(report.can_load)
        self.assertIn("not writable", report.errors[0])
        # The UI must still come up on the configured port to show the error.
        self.assertEqual((report.port, report.ui_port), (9090, 9091))


class ParseEngineOutputTest(unittest.TestCase):
    def test_json(self) -> None:
        self.assertEqual(startup.parse_engine_output(USABLE_CUDA), startup.EngineStatus(True, "cuda", None))

    def test_usable_takes_backend_from_engine_not_installed(self) -> None:
        # Detection found nothing, Strata fell back to a cpu engine; installed[0] is another package.
        text = engine_json(usable=True, backend=None, engine_backend="cpu", installed=("cuda", "cpu"))
        self.assertEqual(startup.parse_engine_output(text), startup.EngineStatus(True, "cpu", None))

    def test_not_usable_uses_detected_backend_and_notice(self) -> None:
        self.assertEqual(startup.parse_engine_output(UNUSABLE),
                         startup.EngineStatus(False, "cuda", "Install NVIDIA driver 580.65 or newer."))

    def test_unparsable_is_not_usable(self) -> None:
        for text in ("", '"usable": true', USABLE_CUDA + "\ntrailing text", "[]"):
            self.assertFalse(startup.parse_engine_output(text).usable, text)


class OwnerTest(unittest.TestCase):
    def test_defaults(self) -> None:
        self.assertEqual(owner_from_env({}), Owner(99, 100))

    def test_values(self) -> None:
        self.assertEqual(owner_from_env({"PUID": "1000", "PGID": " 1000 "}), Owner(1000, 1000))

    def test_bad_value(self) -> None:
        with self.assertRaises(ValueError):
            owner_from_env({"PUID": "abc"})


if __name__ == "__main__":
    unittest.main()
