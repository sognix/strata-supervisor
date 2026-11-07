"""Tests for the Models page: listing and deleting model files. Run: python3 -m unittest discover -s tests"""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "app" / "backend"))

from strata_webui.strata import model_files  # noqa: E402


class ModelFilesTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        root = Path(self._tmp.name)
        self.data = root / "Strata-data"
        self.own = root / "models"
        (self.data / "models" / "coder-IQ1_M").mkdir(parents=True)
        (self.data / "models" / "coder-IQ1_M" / "a.gguf").write_bytes(b"x" * 100)
        (self.data / "models" / "coder-IQ1_M" / "a.gguf.done").write_text("ok")
        (self.data / "models" / "mmproj.gguf").write_bytes(b"y" * 10)
        (self.data / "models" / "mmproj.gguf.done").write_text("ok")
        (self.data / "packs" / "coder-iq1_m").mkdir(parents=True)
        (self.data / "packs" / "coder-iq1_m" / "pack.bin").write_bytes(b"p" * 7)
        (self.data / "mtp").mkdir()
        (self.own / "mine").mkdir(parents=True)
        (self.own / "mine" / "m.gguf").write_bytes(b"z" * 50)
        os.link(self.own / "mine" / "m.gguf", self.own / "mine" / "second-name.gguf")
        os.symlink("/somewhere/else", self.own / "link")

    def test_listing(self) -> None:
        result = model_files.listing(self.data, self.own)
        strata = {e["name"]: e for e in result["strata"]}
        self.assertEqual(sorted(strata), ["coder-IQ1_M", "mmproj.gguf"])       # .done marks are not listed
        self.assertEqual(strata["coder-IQ1_M"]["size_bytes"], 102)
        self.assertEqual(strata["coder-IQ1_M"]["pack_bytes"], 7)
        own = {e["name"]: e for e in result["own"]}
        self.assertTrue(own["mine"]["hard_linked"])
        self.assertEqual((own["link"]["kind"], own["link"]["link_target"]), ("link", "/somewhere/else"))
        self.assertTrue(result["own_writable"])

    def test_delete_strata_model_takes_its_pack(self) -> None:
        result = model_files.delete(str(self.data / "models" / "coder-IQ1_M"), self.data, self.own)
        self.assertEqual(result["freed_bytes"], 109)
        self.assertFalse((self.data / "models" / "coder-IQ1_M").exists())
        self.assertFalse((self.data / "packs" / "coder-iq1_m").exists())
        self.assertTrue((self.data / "mtp").exists())

    def test_delete_file_takes_its_mark(self) -> None:
        model_files.delete(str(self.data / "models" / "mmproj.gguf"), self.data, self.own)
        self.assertFalse((self.data / "models" / "mmproj.gguf.done").exists())

    def test_link_only_the_link_and_hard_links_free_nothing(self) -> None:
        self.assertEqual(model_files.delete(str(self.own / "link"), self.data, self.own)["freed_bytes"], 0)
        self.assertFalse(os.path.lexists(self.own / "link"))
        self.assertEqual(model_files.delete(str(self.own / "mine"), self.data, self.own)["freed_bytes"], 0)

    def test_only_listed_entries(self) -> None:
        for bad in (str(self.data / "mtp"), str(self.data / "packs" / "coder-iq1_m"), "/etc", str(self.own / "mine" / "m.gguf"), ""):
            with self.assertRaises(model_files.FileError):
                model_files.delete(bad, self.data, self.own)
        self.assertTrue((self.data / "mtp").exists())

    def test_read_only_own_folder(self) -> None:
        self.own.chmod(0o555)
        self.addCleanup(self.own.chmod, 0o755)
        if os.access(self.own, os.W_OK):
            self.skipTest("running as root, the folder stays writable")
        with self.assertRaises(model_files.FileError):
            model_files.delete(str(self.own / "mine"), self.data, self.own)


if __name__ == "__main__":
    unittest.main()
