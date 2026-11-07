"""Tests for reading GGUF headers and listing the folders under /models. Run: python3 -m unittest discover -s test/python"""

from __future__ import annotations

import os
import struct
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "app" / "backend"))

from strata_webui.strata import gguf, own_models  # noqa: E402


def _string(text: str) -> bytes:
    data = text.encode()
    return struct.pack("<Q", len(data)) + data


def write_gguf(path: Path, meta: dict, version: int = 3) -> None:
    """A minimal GGUF v3 header: strings, u32 values and one string array (like the tokenizer's), no tensors."""
    body = b""
    for key, value in meta.items():
        body += _string(key)
        if isinstance(value, str):
            body += struct.pack("<I", 8) + _string(value)
        elif isinstance(value, list):
            body += struct.pack("<I", 9) + struct.pack("<IQ", 8, len(value)) + b"".join(_string(v) for v in value)
        else:
            body += struct.pack("<I", 4) + struct.pack("<I", value)
    path.write_bytes(b"GGUF" + struct.pack("<I", version) + struct.pack("<QQ", 0, len(meta)) + body + b"\0" * 64)


CODER = {"general.architecture": "qwen4exp", "general.name": "Qwen3.8-Flash-Next GSQ-RCO Coder",
         "tokenizer.ggml.tokens": ["a", "b", "c"], "general.file_type": 31, "qwen4exp.context_length": 262144,
         "general.repo_url": "https://huggingface.co/ISTA-DASLab/Qwen3.8-Flash-Next-GSQ-RCO-Coder-GGUF"}


class GgufTest(unittest.TestCase):
    def test_header(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "x.gguf"
            write_gguf(path, CODER)
            header = gguf.read_header(path)
        self.assertEqual(header["architecture"], "qwen4exp")
        self.assertEqual(header["name"], "Qwen3.8-Flash-Next GSQ-RCO Coder")
        self.assertEqual((header["file_type"], header["file_type_name"]), (31, "IQ1_M"))
        self.assertEqual(header["context_length"], 262144)

    def test_not_gguf_and_cut_off(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "x.gguf"
            path.write_bytes(b"GGML" + b"\0" * 20)
            with self.assertRaises(gguf.GgufError):
                gguf.read_header(path)
            write_gguf(path, CODER)
            path.write_bytes(path.read_bytes()[:40])
            with self.assertRaises(gguf.GgufError):
                gguf.read_header(path)


class OwnModelsTest(unittest.TestCase):
    def test_scan(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "models"
            (root / "coder").mkdir(parents=True)
            write_gguf(root / "coder" / "anything-00001-of-00002.gguf", CODER)
            (root / "coder" / "anything-00002-of-00002.gguf").write_bytes(b"x" * 10)
            (root / "half").mkdir()
            write_gguf(root / "half" / "m-00001-of-00003.gguf", CODER)
            (root / "empty").mkdir()
            (root / "loose.gguf").write_bytes(b"GGUF")
            os.symlink("../nowhere", root / "link")
            strata = Path(tmp) / "strata-models"
            (strata / "coder-IQ1_M").mkdir(parents=True)
            write_gguf(strata / "coder-IQ1_M" / "a.gguf", CODER)
            result = own_models.scan(root, strata)
        folders = {f["name"]: f for f in result["folders"]}
        self.assertEqual(sorted(folders), ["coder", "empty", "half"])
        self.assertTrue(folders["coder"]["complete"])
        self.assertEqual(folders["coder"]["files"], 2)
        self.assertEqual(folders["coder"]["header"]["architecture"], "qwen4exp")
        self.assertEqual((folders["half"]["complete"], folders["half"]["missing"]), (False, [2, 3]))
        self.assertIn("No .gguf files", folders["empty"]["error"])
        self.assertEqual(result["loose_files"], ["loose.gguf"])
        self.assertEqual(result["broken"], [{"name": "link", "target": "../nowhere"}])
        self.assertEqual(result["strata_architectures"], ["qwen4exp"])

    def test_missing_root(self) -> None:
        result = own_models.scan(Path("/nonexistent/models"))
        self.assertEqual((result["exists"], result["folders"]), (False, []))


if __name__ == "__main__":
    unittest.main()
