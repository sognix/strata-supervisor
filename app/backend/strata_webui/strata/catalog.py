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
def context_list(family, model):
    try:
        return sorted({int(c) for c in cat.contexts_for(family, model)})
    except Exception:
        return []
try:
    own = cat.strata.own_families()  # families with an engine path of their own (Qwen3.6-35B-A3B): one card, no RAM modes
except Exception:
    own = {}
# Families with an engine program and .strata files of their own (Strata 0.2.18 and later): Tencent Hy4-Flash
# ("hy4") and Inkling-2-Mini ("inkling"). own_families() marks them with that key. Every build of such a family
# lives in one folder, models/<family> (Catalog.model_folder, strata_mcp.expected_files).
NATIVE_KINDS = ("hy4", "inkling")
def native_kind(family):
    entry = own.get(family) or {}
    return next((k for k in NATIVE_KINDS if entry.get(k)), None)
def native_files(kind, model):
    try:
        import importlib
        mod = importlib.import_module("strata_control." + kind + "_family")
        names = mod.files_of(model, True)
        # Inkling-2-Mini's GGUF builds (Strata 0.2.21): setup fetches the original drafter mtp.safetensors, not the
        # DSpark GGUF that files_of() still names (setup.py inkling_fetch_drafter)
        if kind == "inkling" and mod.is_gguf_build(model):
            names = [mod.MTP_FILE if n == mod.DSPARK_GGUF_FILE else n for n in names]
        return [{"name": n, "bytes": mod.size_of(n)} for n in names]
    except Exception:
        return None
# Every family of the table, as Catalog.rows() lists them since Strata 0.2.16 (#5869).
for fam in cat.strata.model_table(hw):
    kind = native_kind(fam.get("family"))
    for size in fam.get("sizes") or []:
        fit = size.get("context_fit")  # own-path families: the card's need before the context, and bytes per context token
        nfit = size.get(kind) if kind else None  # native families: Strata's placement for this computer (hy4_fit / inkling_fit)
        contexts = context_list(fam.get("family"), size.get("model"))
        rows.append({
            "family": fam.get("family"), "family_title": fam.get("title"), "model": size.get("model"),
            "about": size.get("about"), "download_gb": size.get("download_gb"), "experts_gb": size.get("experts_gb"),
            "memory_gb": size.get("memory_gb"), "installed": bool(size.get("installed")),
            "experimental": bool(size.get("experimental") or fam.get("experimental")), "images": bool(size.get("images")),
            "own_path": bool((own.get(fam.get("family")) or {}).get("own_path")) and not kind,
            "context_fit": fit if isinstance(fit, dict) else None,
            "native": kind, "native_fit": nfit if isinstance(nfit, dict) else None,
            "files": native_files(kind, size.get("model")) if kind else None,
            "folder": str(fam.get("family") or "") if kind
                else (families.get(fam.get("family")) or {}).get("tag", "") + str(size.get("model") or ""),
            "contexts": contexts,
            "max_context": max(contexts) if contexts else None,
        })
print(json.dumps({"rows": rows, "contexts": [int(c) for c in cat.contexts()]}))
"""


def model_catalog(python_bin: Path, strata_bin: Path, runner: Runner) -> dict:
    code, stdout = runner([str(python_bin), "-I", "-c", CATALOG_PROBE, str(strata_bin)], CATALOG_TIMEOUT_S)
    data = last_json_line(stdout)
    if not isinstance(data, dict) or not isinstance(data.get("rows"), list):
        return {"rows": [], "contexts": [], "error": f"Strata's model list could not be read (exit code {code}). Type family and version yourself."}
    return {"rows": data["rows"], "contexts": data.get("contexts") or [], "error": None}
