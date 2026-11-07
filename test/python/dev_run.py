"""Run the whole supervisor locally against the fake strata CLI, without Docker or a GPU.

    python3 test/python/dev_run.py /path/to/scratch/folder [UI_PORT] [PORT]

Everything is written below the scratch folder. Stop it with Ctrl+C (SIGINT) or SIGTERM.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "app" / "backend"))

from strata_webui import supervisor  # noqa: E402
from strata_webui.host.startup import Paths  # noqa: E402

scratch = Path(sys.argv[1]).resolve()
os.environ.setdefault("UI_PORT", sys.argv[2] if len(sys.argv) > 2 else "18081")
os.environ.setdefault("PORT", sys.argv[3] if len(sys.argv) > 3 else "18080")
os.environ.setdefault("PUID", str(os.getuid()))
os.environ.setdefault("PGID", str(os.getgid()))
os.environ["FAKE_STRATA_STATE"] = str(scratch / "fake-state")
(scratch / "dev").mkdir(parents=True, exist_ok=True)
(scratch / "dev" / "nvidia0").touch()
(scratch / "dev" / "kfd").touch()  # FAKE_BACKEND=hip reads the real AMD cards from /sys, read-only
paths = Paths(
    data=scratch / "data",
    config=scratch / "config",
    dev=scratch / "dev",
    llama_seed=scratch / "no-seed",
    strata_bin=ROOT / "test" / "python" / "fake_strata.py",
    mountinfo=scratch / "no-mountinfo",
)
sys.exit(supervisor.main(paths, python_bin=Path(sys.executable)))
