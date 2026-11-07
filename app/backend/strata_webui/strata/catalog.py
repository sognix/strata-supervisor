"""Strata's model lists: the installed models and the catalog of prepared models the desktop app offers."""

from __future__ import annotations

from pathlib import Path

from ..core.commands import Runner, first_json, last_json_line

CATALOG_TIMEOUT_S = 60
MODELS_TIMEOUT_S = 60


def installed_models(strata_bin: Path, runner: Runner) -> dict:
    """`strata models list --output json`: {"models": [...], "error": str | None}."""
    code, stdout = runner([str(strata_bin), "models", "list", "--output", "json"], MODELS_TIMEOUT_S)
    data = first_json(stdout)
    if not isinstance(data, dict) or not isinstance(data.get("models"), list):
        return {"models": [], "error": f"`strata models list` gave no readable answer (exit code {code})."}
    return {"models": data["models"], "error": None}


# Runs inside the image's Python with Strata's own modules. This is an internal interface
# (IMPLEMENTATION.md, section 6), so every field is optional and a failure only hides the catalog.
CATALOG_PROBE = r"""
import json, os, sys
terminal = os.path.dirname(os.path.dirname(os.path.realpath(sys.argv[1])))
for extra in (terminal, os.path.join(terminal, "lib")):
    if os.path.isdir(extra) and extra not in sys.path:
        sys.path.insert(0, extra)
from strata_cli.launch.catalog import Catalog
cat = Catalog()
hw = cat.hardware()
rows = []
try:
    families = cat.tables()[1]  # family -> {"tag": folder prefix, ...}: Strata's model folder is tag + model (Catalog.model_folder)
except Exception:
    families = {}
def max_context(family, model):
    try:
        return max(int(c) for c in cat.contexts_for(family, model))
    except Exception:
        return None
own = cat.strata.own_families()  # app-only families: `strata launch` does not offer them (Catalog.rows())
for fam in cat.strata.model_table(hw):
    if fam.get("family") in own and not own[fam["family"]].get("multi_file"):
        continue
    for size in fam.get("sizes") or []:
        rows.append({
            "family": fam.get("family"), "family_title": fam.get("title"), "model": size.get("model"),
            "about": size.get("about"), "download_gb": size.get("download_gb"), "experts_gb": size.get("experts_gb"),
            "memory_gb": size.get("memory_gb"), "installed": bool(size.get("installed")),
            "experimental": bool(size.get("experimental") or fam.get("experimental")), "images": bool(size.get("images")),
            "multi_file": bool(size.get("multi_file")),
            "folder": (families.get(fam.get("family")) or {}).get("tag", "") + str(size.get("model") or ""),
            "max_context": max_context(fam.get("family"), size.get("model")),
        })
print(json.dumps({"rows": rows, "contexts": [int(c) for c in cat.contexts()]}))
"""


def model_catalog(python_bin: Path, strata_bin: Path, runner: Runner) -> dict:
    code, stdout = runner([str(python_bin), "-I", "-c", CATALOG_PROBE, str(strata_bin)], CATALOG_TIMEOUT_S)
    data = last_json_line(stdout)
    if not isinstance(data, dict) or not isinstance(data.get("rows"), list):
        return {"rows": [], "contexts": [], "error": f"Strata's model list could not be read (exit code {code}). Type family and version yourself."}
    return {"rows": data["rows"], "contexts": data.get("contexts") or [], "error": None}
