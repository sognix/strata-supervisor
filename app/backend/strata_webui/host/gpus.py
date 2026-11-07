"""Card query behind one small interface (IMPLEMENTATION.md, "Goals and scope").

NVIDIA cards come from `nvidia-smi`, AMD cards from the kernel's KFD topology in /sys. The rest of the code only sees
`Gpu` and never asks for the vendor. Card numbers are never hardcoded: they depend on the PCI order.
The index shown is the one `--gpu-roles` expects (for NVIDIA the nvidia-smi index, IMPLEMENTATION.md,
section 12). Engine logs (CUDA0/CUDA1) and plan texts ("GPU 1"/"GPU 2") count differently and must
not be mapped onto it.
"""

from __future__ import annotations

import csv
import io
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Protocol

NVIDIA_SMI_TIMEOUT_S = 10
NVIDIA_QUERY = "index,name,pci.bus_id,memory.total,memory.used,memory.free"

Runner = Callable[[list[str], int], tuple[int, str]]


@dataclass(frozen=True)
class Gpu:
    index: int
    name: str
    pci_bus_id: str
    vram_total_mib: int | None
    vram_used_mib: int | None

    @property
    def vram_free_mib(self) -> int | None:
        if self.vram_total_mib is None or self.vram_used_mib is None:
            return None
        return self.vram_total_mib - self.vram_used_mib


@dataclass(frozen=True)
class GpuQuery:
    """Result of one query. `error` is a readable message for the UI when the query failed."""

    gpus: tuple[Gpu, ...]
    error: str | None = None


class GpuSource(Protocol):
    def query(self) -> GpuQuery: ...


class NvidiaSmi:
    def __init__(self, runner: Runner, binary: str | None = None) -> None:
        self._runner = runner
        self._binary = binary

    def query(self) -> GpuQuery:
        binary = self._binary or shutil.which("nvidia-smi")
        if not binary:
            return GpuQuery((), "nvidia-smi is not in the container. Start it with --gpus all; on Unraid the NVIDIA driver plugin must be installed.")
        args = [binary, f"--query-gpu={NVIDIA_QUERY}", "--format=csv,noheader,nounits"]
        code, stdout = self._runner(args, NVIDIA_SMI_TIMEOUT_S)
        if code != 0:
            return GpuQuery((), f"nvidia-smi failed (exit code {code}): {stdout.strip()[:300]}")
        try:
            return GpuQuery(parse_nvidia_smi(stdout))
        except ValueError as exc:
            return GpuQuery((), f"Could not read the nvidia-smi output: {exc}")


def parse_nvidia_smi(stdout: str) -> tuple[Gpu, ...]:
    gpus = []
    for row in csv.reader(io.StringIO(stdout), skipinitialspace=True):
        if not row or not "".join(row).strip():
            continue
        if len(row) != 6:
            raise ValueError(f"expected 6 fields, got {len(row)}: {row!r}")
        index, name, bus_id, total, used, _free = (field.strip() for field in row)
        gpus.append(Gpu(int(index), name, bus_id, _mib(total), _mib(used)))
    return tuple(sorted(gpus, key=lambda g: g.index))


def _mib(raw: str) -> int | None:
    # nvidia-smi writes "[N/A]" or "[Not Supported]" for values it cannot read.
    return int(raw) if raw.isdigit() else None


class AmdKfd:
    """AMD cards numbered as Strata numbers them for `--gpu-roles`.

    Same rules as `amd_gpus()` in Strata's setup.py (tag v0.2.9, line 4142), rebuilt because setup.py
    is an internal interface: KFD topology nodes in node order, CPU nodes (gfx_target_version 0 or
    simd_count 0) skipped, index = running count of GPU nodes. Integrated GPUs keep their index
    there too. Name and VRAM come from /sys/class/drm/renderD<drm_render_minor>/device.
    Unlike Strata, unsupported cards (e.g. an AMD iGPU) are not marked: `--gpu-roles` accepts their
    index, but they would not run. Neither of the user's machines has one.
    """

    def __init__(self, sysfs: Path = Path("/sys")) -> None:
        self._sysfs = Path(sysfs)

    def query(self) -> GpuQuery:
        nodes_dir = self._sysfs / "class/kfd/kfd/topology/nodes"
        if not nodes_dir.is_dir():
            # sysfs is the host's, so --device flags do not change this; /dev/kfd is checked in startup.py.
            return GpuQuery((), f"The kernel lists no AMD compute device ({nodes_dir} is missing). Is the amdgpu driver loaded on the host?")
        found: list[Gpu] = []
        nodes = sorted((p for p in nodes_dir.iterdir() if p.name.isdigit()), key=lambda p: int(p.name))
        for node in nodes:
            props = _read_properties(node / "properties")
            try:
                version = int(props.get("gfx_target_version") or 0)
                simds = int(props.get("simd_count") or 0)
            except ValueError:
                continue
            if version == 0 or simds == 0:
                continue
            arch = f"gfx{version // 10000}{(version // 100) % 100:x}{version % 100:x}"
            device = self._sysfs / f"class/drm/renderD{props.get('drm_render_minor', '')}/device"
            name = _read_text(device / "product_name") or f"AMD Radeon ({arch})"
            found.append(Gpu(
                index=len(found),
                name=name,
                pci_bus_id=_pci_address(device),
                vram_total_mib=_bytes_to_mib(_read_text(device / "mem_info_vram_total")),
                vram_used_mib=_bytes_to_mib(_read_text(device / "mem_info_vram_used")),
            ))
        return GpuQuery(tuple(found))


def _read_properties(path: Path) -> dict[str, str]:
    props: dict[str, str] = {}
    for line in (_read_text(path) or "").splitlines():
        key, _, value = line.partition(" ")
        props[key] = value.strip()
    return props


def _read_text(path: Path) -> str | None:
    try:
        return path.read_text(encoding="utf-8", errors="replace").strip()
    except OSError:
        return None


def _bytes_to_mib(raw: str | None) -> int | None:
    return int(raw) // 2**20 if raw and raw.isdigit() else None


def _pci_address(device: Path) -> str:
    try:
        return device.resolve(strict=True).name
    except OSError:
        return ""


def source_for_backend(backend: str | None, runner: Runner) -> GpuSource | None:
    """The card query for the engine backend from the start checks; None if there is none for it."""
    if backend == "cuda":
        return NvidiaSmi(runner)
    if backend == "hip":
        return AmdKfd()
    return None
