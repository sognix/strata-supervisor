"""Tests for reading the engine's memory figures from its log. Run: python3 -m unittest discover -s test/python"""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "app" / "backend"))

from strata_webui.host import memory  # noqa: E402
from strata_webui.strata import engine_report  # noqa: E402

# Lines as engine 0.1.55 wrote them for coder IQ1_M on an RX 7900 XTX (resident RAM mode), shortened.
OLD_RUN = """[strata] 2026-11-04 15:22:31 engine started: /opt/strata/components/engine/strata --serve
strata generate: expert cache 9000 slots, 17.00 GiB of VRAM; policy is
strata generate: resident RAM mode: 9.99 GiB of experts in RAM (page-locked), 9000 in the GPU cache
"""
NEW_RUN = """[strata] 2026-11-07 05:01:31 engine started: /opt/strata/components/engine/strata --serve
strata generate: --batch: 3 slot sessions on CUDA0 (0.56 GiB each); 17.97 GiB free
strata mtp: draft layer loaded, 839 MiB of VRAM (experts 675, dense 111), files read in 0.14 s (5764 MiB/s)
strata generate: loaded 23.42 GiB at 3.14 GiB/s
strata generate: expert cache 8114 slots, 15.44 GiB of VRAM; policy is
strata generate: resident RAM mode: 11.37 GiB of experts in RAM (page-locked), 8114 in the GPU cache; adaptive swaps
strata serve: conversation cache sized from the free RAM: 8192 MiB for up to 4 parked conversations
strata serve: 900 MiB of VRAM free with everything loaded
"""


class EngineReportTest(unittest.TestCase):
    def test_last_run_only(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            log = Path(tmp) / "m.engine.log"
            log.write_text(OLD_RUN + NEW_RUN)
            report = engine_report.memory_report(log)
        self.assertEqual(report, {
            "started": "2026-11-07 05:01:31", "sessions": 3, "session_gib": 0.56, "mtp_gib": 0.82, "arena_gib": 23.42,
            "vram_experts_gib": 15.44, "ram_experts_gib": 11.37, "conversation_gib": 8.0, "vram_free_gib": 0.88})

    def test_normal_mode_keeps_the_whole_arena(self) -> None:
        lines = NEW_RUN.replace("resident RAM mode", "something else").splitlines()
        self.assertEqual(engine_report.parse(lines)["ram_experts_gib"], 23.42)

    def test_split_adds_the_caches(self) -> None:
        lines = ["strata generate: expert cache 100 slots, 10.00 GiB of VRAM", "strata generate: expert cache 50 slots, 5.50 GiB of VRAM"]
        self.assertEqual(engine_report.parse(lines)["vram_experts_gib"], 15.5)

    def test_no_run_or_no_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            log = Path(tmp) / "m.engine.log"
            self.assertIsNone(engine_report.memory_report(log))
            log.write_text("strata generate: something\n")
            self.assertIsNone(engine_report.memory_report(log))


class ContainerUsageTest(unittest.TestCase):
    def test_shmem_is_split_from_the_file_cache(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.assertEqual(memory.container_usage(root), {})
            (root / "memory.stat").write_text("anon 100\nfile 1000\nshmem 600\ninactive_file 300\n")
            self.assertEqual(memory.container_usage(root), {"anon": 100, "shmem": 600, "file_cache": 400})


if __name__ == "__main__":
    unittest.main()
