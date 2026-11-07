"""Tests for the card query. Run from the repo root: python3 -m unittest discover -s tests"""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "app" / "backend"))

from strata_webui.host import gpus  # noqa: E402

# Numbers from the server test on 2026-11-03 (IMPLEMENTATION.md, section 12).
SERVER_OUTPUT = (
    "1, NVIDIA GeForce RTX 5060 Ti, 00000000:05:00.0, 16311, 15124, 1187\n"
    "0, NVIDIA GeForce RTX 3090, 00000000:01:00.0, 24576, 23702, 874\n"
)


class FakeRunner:
    def __init__(self, code: int = 0, stdout: str = SERVER_OUTPUT) -> None:
        self.code = code
        self.stdout = stdout
        self.calls: list[list[str]] = []

    def __call__(self, args: list[str], timeout_s: int) -> tuple[int, str]:
        self.calls.append(args)
        return self.code, self.stdout


class NvidiaSmiTest(unittest.TestCase):
    def test_server_cards_sorted_by_index(self) -> None:
        runner = FakeRunner()
        result = gpus.NvidiaSmi(runner, binary="/usr/bin/nvidia-smi").query()
        self.assertIsNone(result.error)
        self.assertEqual([g.index for g in result.gpus], [0, 1])
        rtx3090 = result.gpus[0]
        self.assertEqual(rtx3090.name, "NVIDIA GeForce RTX 3090")
        self.assertEqual(rtx3090.pci_bus_id, "00000000:01:00.0")
        self.assertEqual((rtx3090.vram_total_mib, rtx3090.vram_used_mib, rtx3090.vram_free_mib), (24576, 23702, 874))
        self.assertEqual(runner.calls[0][0], "/usr/bin/nvidia-smi")
        self.assertIn("--format=csv,noheader,nounits", runner.calls[0])

    def test_not_available_values(self) -> None:
        result = gpus.parse_nvidia_smi("0, Some Card, 00000000:01:00.0, [N/A], [N/A], [N/A]\n")
        self.assertIsNone(result[0].vram_total_mib)
        self.assertIsNone(result[0].vram_free_mib)

    def test_missing_binary(self) -> None:
        source = gpus.NvidiaSmi(FakeRunner())
        source._binary = None
        original = gpus.shutil.which
        gpus.shutil.which = lambda _name: None
        try:
            result = source.query()
        finally:
            gpus.shutil.which = original
        self.assertEqual(result.gpus, ())
        self.assertIn("--gpus all", result.error)

    def test_command_fails(self) -> None:
        result = gpus.NvidiaSmi(FakeRunner(code=9, stdout="NVIDIA-SMI has failed"), binary="nvidia-smi").query()
        self.assertEqual(result.gpus, ())
        self.assertIn("exit code 9", result.error)

    def test_garbage_output(self) -> None:
        result = gpus.NvidiaSmi(FakeRunner(stdout="only, three, fields\n"), binary="nvidia-smi").query()
        self.assertEqual(result.gpus, ())
        self.assertIn("expected 6 fields", result.error)


class AmdKfdTest(unittest.TestCase):
    """Mocked sysfs shaped like the main PC: node 0 CPU, node 1 the 7900 XTX on renderD129."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.sysfs = Path(self._tmp.name)
        self.add_node(0, gfx=0, simds=0, minor=0)
        self.add_node(1, gfx=110000, simds=192, minor=129, pci="0000:03:00.0", total=25753026560, used=2201038848)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def add_node(self, number: int, *, gfx: int, simds: int, minor: int, pci: str | None = None,
                 total: int | None = None, used: int | None = None, name: str | None = None) -> None:
        node = self.sysfs / "class/kfd/kfd/topology/nodes" / str(number)
        node.mkdir(parents=True)
        node.joinpath("properties").write_text(
            f"cpu_cores_count 0\nsimd_count {simds}\ngfx_target_version {gfx}\ndrm_render_minor {minor}\n")
        if pci is None:
            return
        device = self.sysfs / "devices/pci0000:00" / pci
        device.mkdir(parents=True)
        if total is not None:
            device.joinpath("mem_info_vram_total").write_text(f"{total}\n")
        if used is not None:
            device.joinpath("mem_info_vram_used").write_text(f"{used}\n")
        if name is not None:
            device.joinpath("product_name").write_text(name + "\n")
        render = self.sysfs / f"class/drm/renderD{minor}"
        render.mkdir(parents=True)
        render.joinpath("device").symlink_to(device)

    def test_main_pc(self) -> None:
        result = gpus.AmdKfd(self.sysfs).query()
        self.assertIsNone(result.error)
        self.assertEqual(result.gpus, (gpus.Gpu(0, "AMD Radeon (gfx1100)", "0000:03:00.0", 24560, 2099),))

    def test_node_order_not_name_order(self) -> None:
        # Node 10 sorts after node 2 numerically; a text sort would put it first.
        self.add_node(10, gfx=120001, simds=64, minor=131, pci="0000:07:00.0", total=17179869184, used=0, name="Radeon B")
        self.add_node(2, gfx=110001, simds=120, minor=130, pci="0000:05:00.0", total=17179869184, used=0, name="Radeon A")
        names = [(g.index, g.name) for g in gpus.AmdKfd(self.sysfs).query().gpus]
        self.assertEqual(names, [(0, "AMD Radeon (gfx1100)"), (1, "Radeon A"), (2, "Radeon B")])

    def test_arch_with_hex_digits(self) -> None:
        self.add_node(2, gfx=110501, simds=16, minor=130, pci="0000:08:00.0")
        self.assertEqual(gpus.AmdKfd(self.sysfs).query().gpus[1].name, "AMD Radeon (gfx1151)")

    def test_missing_vram_files(self) -> None:
        self.add_node(2, gfx=110000, simds=96, minor=130, pci="0000:09:00.0")
        card = gpus.AmdKfd(self.sysfs).query().gpus[1]
        self.assertIsNone(card.vram_total_mib)
        self.assertIsNone(card.vram_free_mib)

    def test_no_kfd(self) -> None:
        result = gpus.AmdKfd(self.sysfs / "nothing").query()
        self.assertEqual(result.gpus, ())
        self.assertIn("amdgpu driver", result.error)


class SourceForBackendTest(unittest.TestCase):
    def test_mapping(self) -> None:
        self.assertIsInstance(gpus.source_for_backend("cuda", FakeRunner()), gpus.NvidiaSmi)
        self.assertIsInstance(gpus.source_for_backend("hip", FakeRunner()), gpus.AmdKfd)
        self.assertIsNone(gpus.source_for_backend("cpu", FakeRunner()))
        self.assertIsNone(gpus.source_for_backend(None, FakeRunner()))


if __name__ == "__main__":
    unittest.main()
