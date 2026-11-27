"""Tests for the families with .strata files of their own (Hy4-Flash, Inkling-2-Mini; Strata 0.2.18 and later) and the
temporary Inkling two-card workaround. Run: python3 -m unittest discover -s test/python"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "app" / "backend"))

from strata_webui.strata import catalog, model_files  # noqa: E402
from strata_webui.strata import model_config as mc  # noqa: E402

ROOTS = (Path("/data"),)


def cfg(**values) -> mc.ModelConfig:
    return mc.from_dict(values, ROOTS)


class NativeFlagsTest(unittest.TestCase):
    def test_no_flash_next_settings(self) -> None:
        # The form sends what it always sends; these engines refuse flags they do not know, so they are left out.
        flags = mc.to_flags(cfg(model="SQ-64", family="hy4-flash", context="128k", kv="q4_0", vision="on", mtp="off",
                                gpu_roles="0=main,1=off", ram_mode="budget", ram_budget_gib=20, vram_reserve_mib=512,
                                vram_reserve_later_mib=0))
        self.assertEqual(flags, ["--model", "SQ-64", "--family", "hy4-flash", "--context", "128k", "--vision", "on",
                                 "--drafting", "off", "--gpu-roles", "0=main,1=off"])

    def test_kv_formats_of_their_own(self) -> None:
        # Inkling-2-Mini and Qwen3.6 take their own --kv values (Strata 0.2.21); "" and an old Flash-Next value mean
        # Strata's default and pass nothing.
        ink = lambda kv: mc.to_flags(cfg(model="SQ-20", family="inkling-2-mini", kv=kv))  # noqa: E731
        for kv in ("fp16", "fp8", "k8v8", "k8v4"):
            self.assertEqual(ink(kv)[ink(kv).index("--kv") + 1], kv)
        for kv in ("", "int8"):
            self.assertNotIn("--kv", ink(kv))
        q36 = lambda kv: mc.to_flags(cfg(model="UD-Q4_K_XL", family="qwen36", kv=kv))  # noqa: E731
        self.assertIn("fp8", q36("fp8"))
        self.assertNotIn("--kv", q36(""))
        self.assertNotIn("--kv", q36("int8"))
        for family, kv in (("qwen36", "k8v4x"), ("qwen36", "fp4"), ("qwen", "fp8"), ("qwen", "")):
            with self.assertRaises(mc.ConfigError, msg=(family, kv)):
                cfg(model="Q2_0", family=family, kv=kv)

    def test_drafter_default_is_stratas(self) -> None:
        flags = mc.to_flags(cfg(model="SQ-20", family="inkling-2-mini", mtp=""))
        self.assertNotIn("--drafting", flags)
        self.assertNotIn("--mtp", flags)

    def test_pictures_only_on_or_off(self) -> None:
        for mode in ("gpu", "cpu"):
            with self.assertRaises(mc.ConfigError):
                cfg(model="SQ-20", family="inkling-2-mini", vision=mode)
        cfg(model="SQ-20", family="inkling-2-mini", vision="on")

    def test_parallel_limits(self) -> None:
        self.assertIn("64", mc.to_flags(cfg(model="SQ-20", family="inkling-2-mini", parallel=64)))
        with self.assertRaises(mc.ConfigError):
            cfg(model="SQ-20", family="inkling-2-mini", parallel=65)
        with self.assertRaises(mc.ConfigError):
            cfg(model="Q2_0", family="qwen", parallel=9)  # Flash-Next keeps its limit of 8

    def test_flash_next_unchanged(self) -> None:
        flags = mc.to_flags(cfg(model="Q2_0", family="qwen", mtp="off", vram_reserve_mib=512))
        self.assertIn("--kv", flags)
        self.assertIn("--mtp", flags)
        self.assertNotIn("--drafting", flags)
        self.assertIn("vram-reserve-mib=512", flags)


class NativeFilesTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        root = Path(self._tmp.name)
        self.data = root / "Strata-data"
        self.own = root / "models"
        self.own.mkdir()
        hy4 = self.data / "models" / "hy4-flash"
        hy4.mkdir(parents=True)
        (hy4 / "Hy4-Flash-SQ-64.strata").write_bytes(b"a" * 64)
        (hy4 / "Hy4-Flash-SQ-96.strata.part").write_bytes(b"b" * 9)   # an unfinished download of another build
        (hy4 / "Hy4-Flash-Engram-Q8.strata").write_bytes(b"e" * 30)
        (hy4 / "Hy4-Flash-Vision.strata").write_bytes(b"v" * 5)
        (self.data / "models" / "coder-IQ1_M").mkdir()
        (self.data / "models" / "coder-IQ1_M" / "a.gguf").write_bytes(b"x" * 10)
        self.hy4 = hy4

    def test_listed_file_by_file(self) -> None:
        names = sorted(e["name"] for e in model_files.listing(self.data, self.own)["strata"])
        self.assertEqual(names, ["coder-IQ1_M", "hy4-flash/Hy4-Flash-Engram-Q8.strata", "hy4-flash/Hy4-Flash-SQ-64.strata",
                                 "hy4-flash/Hy4-Flash-Vision.strata"])

    def test_one_build_goes_the_shared_file_stays(self) -> None:
        result = model_files.delete(str(self.hy4 / "Hy4-Flash-SQ-64.strata"), self.data, self.own)
        self.assertEqual(result["freed_bytes"], 64)
        self.assertTrue((self.hy4 / "Hy4-Flash-Engram-Q8.strata").is_file())
        self.assertFalse((self.hy4 / "Hy4-Flash-SQ-64.strata").exists())

    def test_download_counts_only_this_build(self) -> None:
        names = ["Hy4-Flash-SQ-96.strata", "Hy4-Flash-Engram-Q8.strata"]
        self.assertEqual(model_files.files_bytes(self.hy4, names), 9 + 30)

    def test_gguf_build_without_strata_file(self) -> None:
        # Inkling-2-Mini's GGUF builds (Strata 0.2.20) with pictures off leave only .gguf files in models/<family>
        ink = self.data / "models" / "inkling-2-mini"
        ink.mkdir()
        (ink / "Inkling-2-Mini-UD-IQ1_S.gguf").write_bytes(b"i" * 7)
        (ink / "dspark-Inkling-2-Mini-Q8_0.gguf").write_bytes(b"d" * 2)
        names = [e["name"] for e in model_files.listing(self.data, self.own)["strata"]]
        self.assertIn("inkling-2-mini/Inkling-2-Mini-UD-IQ1_S.gguf", names)
        self.assertIn("inkling-2-mini/dspark-Inkling-2-Mini-Q8_0.gguf", names)
        self.assertNotIn("inkling-2-mini", names)


# A stand-in for Strata's Terminal: just the parts the catalog probe reads, shaped like Strata 0.2.19
# (strata_cli/launch/catalog.py, tools/strata_mcp.py model_table and own_families, strata_control/hy4_family.py).
FAKE_CATALOG = """
class _Strata:
    def own_families(self):
        return {"qwen36": {"own_path": True}, "hy4-flash": {"own_path": True, "hy4": True},
                "inkling-2-mini": {"own_path": True, "inkling": True}}
    def model_table(self, hw):
        return [
            {"family": "qwen", "title": "Qwen3.8-Flash-Next", "sizes": [{"model": "Q2_0", "download_gb": 70.0}]},
            {"family": "qwen36", "title": "Qwen3.6-35B-A3B", "sizes": [{"model": "UD-Q4_K_XL", "context_fit": {"fixed_gb": 2.0}}]},
            {"family": "hy4-flash", "title": "Tencent Hy4-Flash", "sizes": [{"model": "SQ-64", "download_gb": 130.5,
                "hy4": {"placement": {"vram_gb": 15.0, "ram_gb": 40.0, "ssd_gb": 1.2}, "mode": "near_resident_ssd", "context": 131072}}]},
            {"family": "inkling-2-mini", "title": "Inkling-2-Mini", "sizes": [{"model": "SQ-10"}, {"model": "UD-IQ1_S"}]},
        ]
class Catalog:
    strata = _Strata()
    def hardware(self):
        return {}
    def tables(self):
        return ({}, {"qwen": {"tag": ""}, "qwen36": {"tag": "qwen36-"}, "hy4-flash": {"tag": "hy4-flash-"},
                     "inkling-2-mini": {"tag": "inkling-2-mini-"}}, {}, None)
    def contexts(self):
        return [32768, 65536]
    def contexts_for(self, family, model=None):
        return [8192, 32768, 65536, 131072] if family == "hy4-flash" else self.contexts()
"""
FAKE_HY4 = """
FILES = {"Hy4-Flash-SQ-64.strata": 62, "Hy4-Flash-Engram-Q8.strata": 67, "Hy4-Flash-Vision.strata": 1}
def files_of(build, vision=True):
    return ["Hy4-Flash-" + build + ".strata", "Hy4-Flash-Engram-Q8.strata"] + (["Hy4-Flash-Vision.strata"] if vision else [])
def size_of(name):
    return FILES.get(name)
"""

# strata_control/inkling_family.py of Strata 0.2.21: files_of() still names the DSpark GGUF, setup fetches mtp.safetensors
FAKE_INKLING = """
MTP_FILE = "mtp.safetensors"
DSPARK_GGUF_FILE = "dspark-Inkling-2-Mini-Q8_0.gguf"
FILES = {MTP_FILE: 636, DSPARK_GGUF_FILE: 402, "Inkling-2-Mini-UD-IQ1_S.gguf": 7764, "Inkling-2-Mini-SQ-10.strata": 10470,
         "Inkling-2-Mini-Vision.strata": 637}
def is_gguf_build(build):
    return build.startswith("UD-")
def files_of(build, vision=True, dspark=True):
    out = ["Inkling-2-Mini-" + build + (".gguf" if is_gguf_build(build) else ".strata")]
    if is_gguf_build(build) and dspark:
        out.append(DSPARK_GGUF_FILE)
    return out + (["Inkling-2-Mini-Vision.strata"] if vision else [])
def size_of(name):
    return FILES.get(name)
"""


class ProbeTest(unittest.TestCase):
    def test_native_rows(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            terminal = Path(tmp) / "terminal"
            for rel, text in (("strata_cli/__init__.py", ""), ("strata_cli/launch/__init__.py", ""),
                              ("strata_cli/launch/catalog.py", FAKE_CATALOG), ("strata_control/__init__.py", ""),
                              ("strata_control/hy4_family.py", FAKE_HY4), ("strata_control/inkling_family.py", FAKE_INKLING),
                              ("bin/strata", "")):
                (terminal / rel).parent.mkdir(parents=True, exist_ok=True)
                (terminal / rel).write_text(textwrap.dedent(text))
            done = subprocess.run([sys.executable, "-I", "-c", catalog.CATALOG_PROBE, str(terminal / "bin" / "strata")],
                                  capture_output=True, text=True, timeout=60, check=False)
        self.assertEqual(done.returncode, 0, done.stderr)
        all_rows = json.loads(done.stdout)["rows"]
        rows = {r["family"]: r for r in all_rows}
        ink = {r["model"]: r for r in all_rows if r["family"] == "inkling-2-mini"}
        self.assertEqual([f["name"] for f in ink["UD-IQ1_S"]["files"]],
                         ["Inkling-2-Mini-UD-IQ1_S.gguf", "mtp.safetensors", "Inkling-2-Mini-Vision.strata"])
        self.assertEqual(ink["UD-IQ1_S"]["files"][1]["bytes"], 636)
        self.assertEqual([f["name"] for f in ink["SQ-10"]["files"]],
                         ["Inkling-2-Mini-SQ-10.strata", "Inkling-2-Mini-Vision.strata"])
        hy4 = rows["hy4-flash"]
        self.assertEqual(hy4["native"], "hy4")
        self.assertFalse(hy4["own_path"])                       # not the Qwen3.6 rules
        self.assertEqual(hy4["folder"], "hy4-flash")            # one folder for every build
        self.assertEqual(hy4["native_fit"]["mode"], "near_resident_ssd")
        self.assertEqual([f["name"] for f in hy4["files"]],
                         ["Hy4-Flash-SQ-64.strata", "Hy4-Flash-Engram-Q8.strata", "Hy4-Flash-Vision.strata"])
        self.assertEqual(hy4["max_context"], 131072)
        self.assertTrue(rows["qwen36"]["own_path"])
        self.assertIsNone(rows["qwen36"]["native"])
        self.assertEqual(rows["qwen36"]["folder"], "qwen36-UD-Q4_K_XL")
        self.assertEqual(rows["qwen"]["folder"], "Q2_0")
        self.assertIsNone(rows["qwen"]["files"])


if __name__ == "__main__":
    unittest.main()
