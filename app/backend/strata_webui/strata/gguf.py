"""Read the metadata at the start of a GGUF file (its header), without touching the weights behind it.

A GGUF file begins with "GGUF", a version, the tensor and key counts, then key/value pairs such as
general.architecture or general.name. Only those pairs are read; the tensors (gigabytes) are not.
File names are free, so this header is the only part of a file that says what it holds.
"""

from __future__ import annotations

import struct
from pathlib import Path
from typing import BinaryIO

# Value types of the GGUF format (ggml/docs/gguf.md).
_SCALARS = {0: "<B", 1: "<b", 2: "<H", 3: "<h", 4: "<I", 5: "<i", 6: "<f", 7: "<?", 10: "<Q", 11: "<q", 12: "<d"}
_STRING, _ARRAY = 8, 9
MAX_KEYS = 100_000
MAX_STRING = 16 * 1024 * 1024
MAX_HEADER_BYTES = 256 * 1024 * 1024   # the tokenizer arrays make headers of some MB; more means a broken file

# The keys the UI uses. "<arch>.context_length" is added for the file's own architecture.
WANTED = ("general.architecture", "general.name", "general.basename", "general.finetune", "general.size_label",
          "general.repo_url", "general.file_type", "general.quantized_by")


# general.file_type: llama.cpp's llama_ftype, the main storage format the file was quantized to.
FILE_TYPES = {0: "F32", 1: "F16", 2: "Q4_0", 3: "Q4_1", 7: "Q8_0", 8: "Q5_0", 9: "Q5_1", 10: "Q2_K", 11: "Q3_K_S",
              12: "Q3_K_M", 13: "Q3_K_L", 14: "Q4_K_S", 15: "Q4_K_M", 16: "Q5_K_S", 17: "Q5_K_M", 18: "Q6_K",
              19: "IQ2_XXS", 20: "IQ2_XS", 21: "Q2_K_S", 22: "IQ3_XS", 23: "IQ3_XXS", 24: "IQ1_S", 25: "IQ4_NL",
              26: "IQ3_S", 27: "IQ3_M", 28: "IQ2_S", 29: "IQ2_M", 30: "IQ4_XS", 31: "IQ1_M", 32: "BF16",
              36: "TQ1_0", 37: "TQ2_0", 38: "MXFP4_MOE"}


class GgufError(ValueError):
    """Not a GGUF file, or a header this reader cannot follow."""


class _Reader:
    def __init__(self, handle: BinaryIO, version: int) -> None:
        self.f = handle
        self.count_fmt = "<I" if version == 1 else "<Q"   # version 1 used 32-bit counts and lengths

    def take(self, n: int) -> bytes:
        data = self.f.read(n)
        if len(data) != n:
            raise GgufError("the file ends inside its header")
        if self.f.tell() > MAX_HEADER_BYTES:
            raise GgufError("the header is implausibly large")
        return data

    def unpack(self, fmt: str) -> int | float | bool:
        return struct.unpack(fmt, self.take(struct.calcsize(fmt)))[0]

    def string(self) -> str:
        n = int(self.unpack(self.count_fmt))
        if n > MAX_STRING:
            raise GgufError("a header string is implausibly long")
        return self.take(n).decode("utf-8", errors="replace")

    def value(self, vtype: int) -> object:
        if vtype == _STRING:
            return self.string()
        if vtype in _SCALARS:
            return self.unpack(_SCALARS[vtype])
        if vtype == _ARRAY:
            etype = int(self.unpack("<I"))
            n = int(self.unpack(self.count_fmt))
            if etype in _SCALARS:                      # fixed size: skip in one step
                self.take(n * struct.calcsize(_SCALARS[etype]))
            else:
                for _ in range(n):
                    self.value(etype)
            return None
        raise GgufError(f"unknown value type {vtype} in the header")


def read_header(path: Path) -> dict:
    """The wanted header keys of a GGUF file, plus "context_length" when the file states it.

    Raises GgufError for a file that is not GGUF or whose header is cut off; OSError when it cannot be read.
    """
    with open(path, "rb") as handle:
        if handle.read(4) != b"GGUF":
            raise GgufError("not a GGUF file")
        reader = _Reader(handle, 1)
        version = int(reader.unpack("<I"))
        if version not in (1, 2, 3):
            raise GgufError(f"GGUF version {version} is not known")
        reader = _Reader(handle, version)
        reader.unpack(reader.count_fmt)                  # tensor count
        keys = int(reader.unpack(reader.count_fmt))
        if keys > MAX_KEYS:
            raise GgufError("the header has implausibly many keys")
        raw: dict[str, object] = {}
        for _ in range(keys):
            key = reader.string()
            vtype = int(reader.unpack("<I"))
            value = reader.value(vtype)
            if value is not None and (key in WANTED or key.endswith(".context_length")):
                raw[key] = value
    out = {k.split(".", 1)[1]: raw[k] for k in WANTED if k in raw}
    arch = out.get("architecture")
    if isinstance(arch, str) and f"{arch}.context_length" in raw:
        out["context_length"] = raw[f"{arch}.context_length"]
    if isinstance(out.get("file_type"), int) and out["file_type"] in FILE_TYPES:
        out["file_type_name"] = FILE_TYPES[out["file_type"]]
    return out
