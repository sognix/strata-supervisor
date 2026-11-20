# Plan: ask Strata whether it accepts an own GGUF folder

Status: idea, nothing built yet (written 2026-11-19, Strata 0.2.16). Pick this up from step 0.

## Goal

A user puts GGUF files into a sub-folder of `/models` and wants to know **before loading** whether Strata can run
them. Today the UI only describes a folder (`strata/own_models.py`: shards, completeness, the first file's header,
a hint when its architecture differs from the models Strata downloaded). Whether Strata really accepts it shows up
only on "Plan" or at load time.

Strata runs only its own model families (0.2.16: Qwen3.8-Flash-Next in several providers' versions, and
Qwen3.6-35B-A3B; Strata's `docs/MODELS.md`). An own GGUF folder makes sense only for these, e.g. another quantization.
Any other architecture (Mixtral, GLM, DeepSeek since 0.2.16, ...) cannot run.

## Step 0: find out what Strata offers (read-only, nothing built)

Strata's `docs/MODELS.md` names `strata models import <folder or .gguf> --plan`: "shows what a set of files you
already have would become, without changing anything". Not checked yet:

1. `strata models import --help` in the image: flags, and whether there is `--output json`.
2. Output for a real Qwen3.8-Flash-Next GGUF folder (the downloaded `coder IQ1_M` files can be pointed at).
3. Output for a foreign GGUF (a small non-Qwen model, a few hundred MB): clean refusal with a reason, or an error?
4. Output for an incomplete shard set and for a DeepSeek file (Strata's `strata_control/retired.py` recognises those).
5. Runtime: does it read only the headers (fast) or the whole files? Does it write anything (packs, run configs,
   `/data/install`)? Compare `/data` before and after.
6. Does `strata launch --plan --model-path <folder>` already give the same answer? Its JSON has a `download.own_files`
   field and a `model` block. If it already refuses foreign files clearly, no second command is needed.
7. Exit codes for accepted / refused / error.

Write the findings into this file (new section "Findings") before building anything.

## Step 1: backend

- New function in `strata/` (e.g. `own_models.check(folder)`): runs the command from step 0 via `core/commands.py`
  (argument list, never a shell, with a timeout), parses the result into
  `{"accepted": bool, "family": str|None, "model": str|None, "reason": str|None, "raw": str}`.
- Cache per folder, keyed by path plus size and mtime of the files, so a changed folder is checked again.
  Same pattern as `_own_cache` / `_catalog_cache` in `web/api.py`.
- Run it on request only (a button), not on every scan of `/models`: the scan runs every 10 s.
- Only one check at a time; never while a model is loading (the launcher is busy then).
- New route, e.g. `POST /api/own-models/check` with `{"path": ...}`. The path must be a folder under `/models`
  (reuse the root check of `model_config.from_dict` / `_roots`).

## Step 2: UI

- Where own GGUF folders are shown (config form, source "gguf", `ownModels` in `app.js`; Models page), a button
  "Check with Strata" per folder.
- Result next to the folder: "Strata recognises: <family> <model>" (green) or "Not supported: <reason from Strata>"
  (red). Keep Strata's own words; do not invent reasons.
- When Strata recognises family and model, offer to fill the form's family/model fields.

## Step 3: tests and docs

- Unit tests with `test/python/fake_strata.py`: accepted, refused, timeout, unreadable output, path outside `/models`.
- Local test on the 7900 XTX with a real and a foreign GGUF.
- `docs/USER-GUIDE.md`: one paragraph on own GGUF folders and the check.
- `AI/IMPLEMENTATION.md` section 15: add the command and its output shape to the list of things to check after a
  Strata update.

## Related open point (separate PR): models the UI's list drops

Before starting, run `python3 tools/check_strata_catalog.py` (local helper, see `AI/IMPLEMENTATION.md` section 15).
It compares, in the test container with the latest Strata release, what `strata launch` offers with what the UI's
catalog probe lists, so it gives the current state instead of the notes here. Fix what it reports as "missing" in a
separate PR first; it affects which families step 2 can offer.

Done on 2026-11-20 (Strata 0.2.17): the probe lists every family, `multi_file` is gone, Qwen3.6 has its own
estimate, and the form handles its one card (`AI/IMPLEMENTATION.md` section 6, "Qwen3.6"). The script reports 28 of 28.
Own GGUF folders for Qwen3.6: the form proposes family `qwen36` from the header; Strata accepts such files since
0.2.16 (#5869, `models.own_path_problem`), not tried yet. Step 0 above still applies.

State on 2026-11-19 (Strata 0.2.16), for orientation only: the script reported the three Qwen3.6-35B-A3B models as
missing. The probe in `strata/catalog.py` skips Strata's "own path" families, which `strata launch` did not offer up
to 0.2.13 (since 0.2.16 it does, Strata issue #5869). The same lines carry `multi_file`, which only DeepSeek used and
which is always `False` in 0.2.16. Qwen3.6 also has its own cache rule for the memory estimate (Strata's
`native-fit.ts`, `contextFit`), which the UI's estimate does not know yet.
