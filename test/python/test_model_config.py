"""Tests for the per-model TOML files and their flags. Run: python3 -m unittest discover -s tests"""

from __future__ import annotations

import os
import sys
import tempfile
import tomllib
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "app" / "backend"))

from strata_webui.strata import model_config as mc  # noqa: E402
from strata_webui.core.ownership import Owner  # noqa: E402

ROOTS = (Path("/data"),)


def cfg(**values) -> mc.ModelConfig:
    return mc.from_dict({"model": "Q2_0", **values}, ROOTS)


class ValidateTest(unittest.TestCase):
    def test_defaults_and_flags(self) -> None:
        self.assertEqual(mc.to_flags(cfg()), ["--model", "Q2_0", "--context", "32k", "--kv", "int8", "--vision", "off"])

    def test_full_flags(self) -> None:
        flags = mc.to_flags(cfg(family="qwen", context="512k", kv="k8v4", vision="gpu", mtp="off", parallel=2,
                                gpu_roles="1=helper, 0=main", ram_mode="resident-experts", vram_reserve_mib=512,
                                vram_reserve_later_mib=0, extra_set=["prefill=65536", "STRATA_X=1"]))
        self.assertEqual(flags, [
            "--model", "Q2_0", "--family", "qwen", "--context", "512k", "--kv", "k8v4", "--vision", "gpu",
            "--mtp", "off", "--parallel", "2", "--gpu-roles", "0=main,1=helper",
            "--set", "resident-experts=", "--set", "vram-reserve-mib=512", "--set", "vram-reserve-later-mib=0",
            "--set", "prefill=65536", "--set", "STRATA_X=1"])
        self.assertNotIn("--gpu", flags)

    def test_ram_modes(self) -> None:
        self.assertNotIn("--set", mc.to_flags(cfg(ram_mode="normal")))
        self.assertIn("resident-budget-gib=28", mc.to_flags(cfg(ram_mode="budget", ram_budget_gib=28)))
        self.assertIn("mmap-experts=", mc.to_flags(cfg(ram_mode="mmap-experts")))
        with self.assertRaises(mc.ConfigError):
            cfg(ram_mode="budget", ram_budget_gib=0)

    def test_refusals(self) -> None:
        bad = [
            {"model": ""}, {"model": "Q2 0"}, {"family": "Qwen!"}, {"context": "lots"}, {"kv": "q8"},
            {"vision": "maybe"}, {"parallel": 9}, {"parallel": True}, {"gpu_roles": "0=main,1=main"},
            {"gpu_roles": "0=helper"}, {"gpu_roles": "0=boss"}, {"gpu_roles": "0=main,0=helper"},
            {"extra_set": ["bad name=1"]}, {"extra_set": ["prefill=1 2"]}, {"extra_set": ["--resident-budget-gib=4"]},
            {"extra_set": ["vram_reserve_mib=1"]}, {"extra_set": "prefill=1"}, {"unknown": 1},
            {"model_path": "relative/dir"}, {"model_path": "/etc"}, {"model_path": "/data/../etc"},
            {"vram_reserve_mib": -1}, {"extra_set": ["port=9000"]}, {"extra_set": ["host=127.0.0.1"]},
            {"extra_set": ["--gpu=0"]}, {"extra_set": ["max_context=1000"]},
        ]
        for values in bad:
            with self.assertRaises(mc.ConfigError, msg=values):
                mc.from_dict({"model": "Q2_0", **values}, ROOTS)

    def test_model_path_only(self) -> None:
        c = mc.from_dict({"model_path": "/data/gguf/IQ3_S"}, ROOTS)
        self.assertEqual(mc.to_flags(c)[:2], ["--model-path", "/data/gguf/IQ3_S"])

    def test_symlink_out_of_root_is_refused(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "data"
            root.mkdir()
            (root / "escape").symlink_to("/etc")
            with self.assertRaises(mc.ConfigError):
                mc.from_dict({"model_path": str(root / "escape")}, (root,))


class FileTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.folder = Path(self._tmp.name)
        self.store = mc.ConfigStore(self.folder, Owner(os.getuid(), os.getgid()), ROOTS)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_round_trip(self) -> None:
        original = cfg(notes='He said "hi"\\ and\nleft\ttab \x01', extra_set=["a=b"], vram_reserve_mib=100)
        self.store.save("q2_0", original)
        path = self.folder / "q2_0.toml"
        self.assertEqual(path.stat().st_mode & 0o777, 0o664)
        self.assertEqual(self.store.load("q2_0"), original)
        self.assertNotIn("vram_reserve_later_mib", tomllib.loads(path.read_text()))
        self.assertEqual(self.store.names(), ["q2_0"])

    def test_hand_edited_file_is_checked(self) -> None:
        (self.folder / "m1.toml").write_text('model = "Q2_0"\nkv = "q8"\n')
        with self.assertRaisesRegex(mc.ConfigError, "m1.toml"):
            self.store.load("m1")
        (self.folder / "m2.toml").write_text("model = \n")
        with self.assertRaisesRegex(mc.ConfigError, "cannot be read"):
            self.store.load("m2")

    def test_names_skip_foreign_files_but_report_them(self) -> None:
        (self.folder / "Bad Name.toml").write_text('model = "Q2_0"\n')
        (self.folder / "My-Model.toml").write_text('model = "Q2_0"\n')
        (self.folder / ".m.toml.tmp").write_text("")
        self.store.save("good", cfg())
        self.assertEqual(self.store.names(), ["good"])
        self.assertEqual(self.store.invalid_files(), ["Bad Name.toml", "My-Model.toml"])

    def test_failed_save_leaves_no_tmp_file(self) -> None:
        original = mc.set_file_owner
        mc.set_file_owner = lambda *_: (_ for _ in ()).throw(PermissionError("no chown"))
        try:
            with self.assertRaises(PermissionError):
                self.store.save("m1", cfg())
        finally:
            mc.set_file_owner = original
        self.assertEqual([p.name for p in self.folder.iterdir()], [])

    def test_bad_names(self) -> None:
        for name in ("../x", "X", ""):
            with self.assertRaises(mc.ConfigError):
                self.store.path_of(name)

    def test_delete(self) -> None:
        self.store.save("m1", cfg())
        self.store.delete("m1")
        self.assertEqual(self.store.names(), [])
        with self.assertRaises(mc.ConfigError):
            self.store.delete("m1")


if __name__ == "__main__":
    unittest.main()
