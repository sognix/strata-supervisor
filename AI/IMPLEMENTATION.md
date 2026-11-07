# Strata web UI: implementation, decisions and findings

Audience: Claude sessions working on this repo. Facts, decisions and test results; the code is the source for how things are built. Section numbers are referenced from code comments and `CLAUDE.md`, so keep them stable. End-user documentation: `README.md` and `docs/USER-GUIDE.md`.

**State (2026-11-07):** Built and tested locally on the 7900 XTX with real models (section 13). What is built and what is open: section 14. Next step: server test with the published image (not yet run with the new UI).

Target system: Unraid, NVIDIA RTX 3090 (24 GB) + RTX 5060 Ti (16 GB), 48 GB RAM, 48 GB swap. Multi-GPU is mandatory.

## Goals and scope

- Small web UI inside the official `cuda13` image: one container, no second container, no Docker socket. Built with `FROM`, no Strata code changed. New Strata versions may change CLI details (section 15). License not checked; only matters if the image is distributed publicly.
- **Several models managed, only one loaded.** Load and unload only by click. No auto-load, no auto-swap. Nothing loads at container start.
- Flow like llama-swap without automation: the container runs only the supervisor and the UI. Loading starts a Strata instance via `strata launch`; unloading ends it, and another model can follow without a container restart.
- **Per-model settings** live in `/config/models/<name>.toml`, separate from model data in `/data` (Unraid default `/mnt/user/appdata/strata-supervisor`), editable outside the container. TOML because Python's stdlib reads it (`tomllib`); files are flat, a small own writer saves them. On load they are translated into `strata launch` flags.
- **Ownership in `/config`:** the container runs as root. `PUID`/`PGID` (default `99:100`, `nobody:users` as usual on Unraid) set the owner of every file and folder the supervisor creates in `/config`, with write permission for owner and group.
- **Ports via env vars:** `PORT` for the inference API (default `8080`, already in the image; passed as `--port "$PORT"`, the `strata` wrapper reads it too). `UI_PORT` for the web UI (default `8081`). In bridge mode `-p` decides the host port anyway; the vars matter for `--network host` or port clashes.
- **Placeholder on `PORT`** while no model serves: HTTP 503 with "No model loaded in Strata." in the JSON error format; `GET /v1/models` returns an empty list. Not a proxy, loads nothing. It releases the port before a load (`strata launch` treats a busy port as a conflict) and takes it back as soon as nothing listens, whether after unload, error or crash. The launch process ends when the model is ready while the server runs on in its own session (section 5, point 5), so a bind attempt is the test: it fails while Strata listens. No attempt while a launch runs or an unload is in progress; retries a few times if the port is still busy briefly; the UI shows an error if it never works. While loading, nothing answers on the port. Tested OK (section 13).
- Both cards are used, the second card's VRAM is needed.
- NVIDIA (`cuda13`) is the target. Since 2026-11-07 CI publishes every Strata flavor the UI can drive (section 14); AMD (`rocm-gfx110x`) is also the local test platform (7900 XTX). Flavor is a build ARG; the card query sits behind a small interface (`nvidia-smi` or `/sys/class/drm` + KFD); the rest of the code knows no vendor. AMD counts as tested on this one card only.
- Local tests with a real model only after the user's explicit OK: they take GPU, RAM and disk, and the desktop app must have no model loaded. Pick models via `--plan` first. Download each model once and reuse it.
- Local test mounts: `local-mounts/config` on `/config` (create it before the first start, else Docker creates it as root), `PUID=1000`/`PGID=1000` so the user can edit without `sudo`. `/data` is a local folder with room for the models. Files in `/data` belong to root; delete them via the container or `sudo`. Test runs remove only the container; data and config stay unless the user asks. Check free space before downloads. `local-mounts/` was called `test/` until 2026-11-04; since 2026-11-07 `test/` holds the tests (`test/python/`, `test/unraid/`).
- **RAM mode per model (decision 2026-11-03).** One choice per model, with a short UI text each, sorted from most to least RAM:

  | Mode | Flag | RAM need | UI text |
  | --- | --- | --- | --- |
  | Normal | none | most: all experts, also those on the cards (Q2_0: 34 GB per `MODELS.md`) | **Normal.** All experts stay in RAM, including those on the cards. Uses the most RAM. No reads from the SSD. |
  | Only what no card holds | `--set resident-experts` | much less: only experts not in VRAM | **Only what no card holds.** Experts kept on a graphics card are left out of RAM. Uses much less RAM. No reads from the SSD. |
  | RAM budget | `--set resident-budget-gib=N` | at most N GiB | **RAM budget.** Keeps up to N GiB of experts in RAM and reads the rest from the SSD when needed. Can be slower. |
  | From the model files | `--set mmap-experts` | least fixed RAM; the kernel keeps what is free | **From the model files.** Reads experts straight from the model files. The system frees this RAM when other programs need it. Uses the least fixed RAM. Can be slower. |

  - Order of "only what no card holds" and "budget" depends on N, so the UI shows estimated GB, not just a rank.
  - The UI computes the estimate from expert size and VRAM. `gpu_fit.fit()` only fits "only what no card holds"; it underestimates Normal (section 10).
  - Speed of the modes is not measured on the server. Modes with SSD reads are marked "Can be slower".
  - Combining modes (e.g. `resident-experts` + budget) is unchecked; it is a single choice.
- No authentication for now, LAN only; can be added later (section 5, point 8).
- No specific target model. IQ3_S below is only a checked example.
- Language: everything in the repo is English.

### Settled at build start

- **API key (2026-11-04):** `API_KEY`, else `API_KEY_FILE`, else the key in `/data/state/api-key`, else a new one. Passed to every launch via `--api-key-env`, so it stays the same across model switches (without it Strata makes one key per preset). The UI shows it only on click.
- **Tuning:** not in the first version. Command-line starts skip it without `--tune`.
- **Leftovers after a hard kill:** after a kill or power loss `/data/state` may hold a `stale` state. The UI must not hang on it. Unchecked whether `strata launch --stop` or the next start cleans it. A start after a container restart still loads nothing.
- **Deleting models:** the Models page deletes folders (section 14).

## 1. Result

- The UI is buildable inside the official image: one container, own supervisor, no Docker socket, CLI wrapping over `strata launch`.
- RTX 3090 + RTX 5060 Ti is a supported combination per docs and code; both run with `cuda13`.
- The split runs in a container on Unraid (server test 2026-11-03, section 12).

## 2. Basis and limits of the analysis

- Read: Strata docs `DOCKER.md`, `TERMINAL_LAUNCH.md`, `MANAGEMENT_PROTOCOL.md` fully; `TERMINAL.md`, `MULTI_GPU.md`, `MODELS.md`, `MCP_SERVER.md`, `TERMINAL_QUALIFICATION.md` partly. Source at tag `v0.2.9`: `docker/entrypoint.sh`, `docker/strata-wrapper.sh`, `strata_cli/launch/cmd.py`, `runner.py`, `settings.py`, `params.py`, `strata_control/runtime.py`, `gpu_roles.py`, `setup.py`, `apps/host/server/gpu-roles.mjs`, `apps/host/src/lib/gpu-roles.ts`, `apps/desktop/scripts/cards-e2e.mjs` (large files searched, not read fully). `docker/Dockerfile` and `docker/healthcheck.sh` from `master`. Image config of `0.2.9-cuda13` from the registry.
- Docs were loaded from `master`, not `v0.2.9`; they may differ slightly from the image. `entrypoint.sh` is identical on both. Docs still say `0.2.7`; current is `v0.2.9` (2026-10-31). Default branch is `main`.
- Of the desktop app's sliders only "Leave 20 %" is mapped (section 10).

## 3. Image facts

Registry config of `0.2.9-cuda13`: entrypoint `/usr/bin/tini -- /opt/strata/entrypoint.sh`, cmd `run`, healthcheck `/opt/strata/healthcheck.sh` (30 s interval, 8 s timeout, 2 h start period, 3 retries), port `8080/tcp`, volume `/data`, stop signal `SIGTERM`, about 785 MB compressed.

Env vars in the image:

```text
STRATA_COMPONENTS_DIR=/opt/strata/components
STRATA_INSTALL_DIR=/data/install
XDG_STATE_HOME=/data/state
XDG_CONFIG_HOME=/data/config
HOME=/data/home
PORT=8080
LANG=C.UTF-8
PYTHONUNBUFFERED=1
PYTHONNOUSERSITE=1
STRATA_IN_DOCKER=1
NVIDIA_VISIBLE_DEVICES=all
NVIDIA_DRIVER_CAPABILITIES=compute,utility
```

Paths:

- Terminal `/opt/strata/terminal/bin/strata`, Python 3.12 `/opt/strata/terminal/python/bin/python3`.
- Wrapper `/usr/local/bin/strata`: sets `STRATA_API_KEY` from `/data/state/api-key` and `STRATA_URL` from `PORT`.
- Models `/data/Strata-data/models` (not `/data` directly), packs `/data/Strata-data/packs`, draft layer `/data/Strata-data/mtp`.
- Run configs and logs `/data/install`; state, presets, tuning, key `/data/state`.

The container runs as root (a known limit per Strata's docs).

## 4. Image behavior

### What the official entrypoint does

It `exec`s other commands right away, so its parts cannot be reused. The supervisor repeats:

1. Create `/data/state`, `/data/home`, `/data/install`, `/data/Strata-data`.
2. Symlink `/data/install/third_party/llama.cpp` to `/opt/strata/seed/third_party/llama.cpp` (else the first start downloads from GitHub).
3. Delete run configs (`/data/install/strata-*.json`) that point to missing paths under `/opt/strata/components/` (e.g. after an engine update). Model files stay.
4. Check the engine: `strata components engine --output json` must report `"usable": true`.
5. Check that `/dev/nvidia[0-9]*` exists.
6. Read the key from `API_KEY` or `API_KEY_FILE`, pass it via `--api-key-env STRATA_LAUNCH_KEY` (keeps it out of the process list).
7. After start, write the key with mode 0600 to `/data/state/api-key` (`strata launch --show-key --preset NAME`).
8. Fixed flags: `launch --no-tui --access lan --port "$PORT" --data-dir /data/Strata-data`.

Optional: warnings for missing AVX2, short API key, `/data` not a volume. The memory-limit warning is dropped (a RAM limit is wanted, see "Container requirements"); the UI shows the limit instead. Migration from the 0.1.x layout and the `/data/models` guard are not needed.

Env var to flag mapping: `MODEL`/`FAMILY` → `--model`/`--family`, `MODEL_PATH` → `--model-path`, `CONTEXT` → `--context`, `PARALLEL` → `--parallel`, `KV` → `--kv`, `VISION` → `--vision`, `GPU`/`GPUS` → `--gpu`, `GPU_ROLES` → `--gpu-roles`, `TUNE`/`RETUNE`/`TUNE_ALLOW_SMALL` → `--tune` (once)/`--tune-allow-small`, `STRATA_SET` → one `--set` per entry, `PRESET` → `--preset` and `--save-preset`. `GPU_ROLES` and `GPU`/`GPUS` exclude each other.

### Does `strata launch --stop` end the container?

No, only the model. In the original, the entrypoint's watchdog loop (three failed status checks, 10 s apart) ends the container; we have no such loop. From `_stop_all` in `cmd.py`: without `--preset` it stops all `launch-*` profiles; it acts on `ready`, `starting`, `failed`, `stale`; exit code is always 0, even on failure (check status instead); internal timeout 180 s, but Unraid kills after `DOCKER_TIMEOUT` (10 s).

### Download-only command?

`strata launch` has none. The internal `setup.py` has `--yes --no-start` ("install only, do not start the model"), called that way by `runner.py`; undocumented, may change. `STRATA_CONTROL_INSTALL=1` turns on control mode (also used by the Dockerfile). Not used: the download happens at the first load. `strata launch --plan` (`--output json`) shows what would be downloaded. Interrupted downloads resume. Cancel during download: section 5, point 4.

### RAM and VRAM settings

All go through `--set KEY=VALUE`. `split_setting` accepts `prefill=65536`, `--prefill=65536` and `STRATA_*=1`; unknown names only warn.

| Setting | Meaning |
| --- | --- |
| `--vram-reserve-mib` | VRAM kept free for other programs |
| `--vram-reserve-later-mib` | separate reserve for later cards, default is `--vram-reserve-mib` |
| `--resident-experts` | low-RAM mode: experts on a card are left out of the RAM copy |
| `--resident-budget-gib` | RAM budget for experts, rest read from disk |
| `--mmap-experts` | map experts from the model files |
| `--expert-cache` | how many experts stay in VRAM |
| `--kv-resident` | how much KV cache stays in VRAM |
| `--pcie-frac` | share of the PCIe link for expert transfers |
| `--adapt-every` | how often the engine re-picks VRAM experts |
| `--prefill` | prompt tokens per pass |

`--low-ram` is not a `launch` flag (only `setup.py` from a source checkout). The engine `--help` omits several of these flags, but the binary contains all strings (section 11).

### Management bridge or MCP instead of CLI wrapping?

No. The management bridge (`python -m strata_control.management`) is a one-shot stdin/stdout process, no HTTP; per its docs no streaming, no recovery when start/stop/restart end unclear, needs qualification before production; mutating actions need plan + execute. MCP (`tools/strata_mcp.py`, in the image) is stdio for AI clients, has `strata_install` with progress and `strata_stop` via `POST /unload`, but does not fit the `strata launch` lifecycle.

### Container requirements

- Confirmed: `--ulimit memlock=-1:-1`, `--shm-size 2g`.
- **No `--stop-timeout 120` (2026-11-03).** Unraid 7.3 ignores it: WebUI stop sends `POST /containers/<id>/stop?t=<DOCKER_TIMEOUT>` (`stopContainer()` in `DockerClient.php`); array stop and shutdown run `docker stop --time=${DOCKER_TIMEOUT:-10}` (`rc.docker`). `DOCKER_TIMEOUT` is global, default 10 (*Settings → Docker → "Docker Stop Timeout"*). Only `restartContainer()` sends no time.
- **Decision:** `DOCKER_TIMEOUT` stays at 10 s. On UPS switch-over the server must shut down fast; a hard kill of Strata is fine (model files are read-only, kernel frees VRAM/RAM, downloads resume). Strata stopped in 4.4 s on the server (section 12).
- **RAM limit:** host RAM was about 95 % used in the server test (section 12), so the container gets a RAM limit. User's decision for the server: `--memory 25g --memory-swap -1`, no `--ulimit memlock` (`DESIGN.md` section 4). The template sets no limit by default. Interaction of the limit with `memlock` is open (section 9). With per-model RAM modes, swap is only a fallback.

## 5. Rules for driving `strata launch`

Things where the obvious approach is wrong. Code comments refer to these numbers.

1. **Use `launch`, not the runtime host path.** `launch` itself uses `strata_control.runtime` internally; the official entrypoint goes this way too.
2. **Roles, not GPU checkboxes.** Per card `main`, `helper` or `off` via `--gpu-roles`, never `--gpu`. Sources disagree on `--gpu` (entrypoint says first setup only and deletes differing configs; `runner.py` writes cards per launch). `--gpu-roles` is written on every start.
3. **Verify `--stop`.** Exit code 0 means nothing; poll the state (point 5). Only `stopped` counts as unloaded. The supervisor's stop budget stays under the 10 s of `DOCKER_TIMEOUT` (8 s).
4. **Cancel during download or load.** `--stop` alone is not enough. The original sends SIGINT to the launch process, waits up to 30 s, sends SIGTERM, then runs `--stop`. 30 s is too long under Unraid; the supervisor uses shorter waits. SIGINT must not be ignored in the child (the original needs a Python detour because `sh` ignores SIGINT for background jobs).
   - **Process group (2026-11-04, `runner.py` `run()`/`_spawn()`):** `setup.py` does the download as a child of the launch, same process group. On SIGINT the launch does not end it (`_stop_children()` runs only on `Cancelled`/`LaunchError`). So the launch runs in its own session and SIGINT, SIGTERM, finally SIGKILL go to the whole group. The Strata server has its own session and is not affected. Verified locally (section 13).
5. **Model state only from status.** The server runs on in its own session (`start_new_session=True` in `runtime.py`) after the launch ends, so a PID says nothing. States: `ready`, `starting`, `failed`, `stale`, `stopped`.
   - `launch --status --output json` is not enough: with a running model a plain-text line per model follows the JSON on stdout (`cmd.py`, `_say`), so `json.loads(stdout)` fails. If used, read only the first line.
   - An empty list proves no stop: `running_launches()` shows only `ready` and `starting`. Per profile use `strata runtime status --profile launch-<name> --output json`. Only `stopped` is unloaded; any other state blocks a model switch and is shown.
   - Output (`runtime.py`, `v0.2.9`): always `ok`, `profile`, `state`, `host_pid`, `owner_id`, `child_pid`, `profile_revision`, `shutdown_timeout`; depending on state `phase`, `error`, `exit_code`, `engine_exit_code`, `log_tail`, `instance_id`, `model_state`. `stopped`: no process, `"ok": true`. `starting`: `phase` is `loading`, `verifying_generation` or `ready_file_not_matched`. `ready`: probe answer succeeded. `failed`: process ended, start or probe error. `stale`: control process gone, `"ok": false`, `"error": "owner_host_lost"`, exit code 6.
   - **Trap:** a profile without a state file reports `stopped`, not "not found". A wrong profile name looks like a clean stop. The UI only queries names it assigned itself. An invalid name raises `ProfileError("runtime commands require a saved profile name")`.
   - Per `TERMINAL.md` it reports the last successful start or check, not live engine health; `strata runtime verify` does a fresh check.
   - CLI wrapping (`strata_cli/cli.py`, `_runtime()`): one JSON line `{"schema_version": 1, ...}` with the `runtime.status()` fields flat beside it, no plain text after.
   - **Launch result:** with `--output json` a launch can exit 0 with `"serving": false`, `engine_problem` and `message`. Exit 0 does not mean a model runs. "Downloading" and "loading" come from the running launch process and its output.
6. **Config files are authoritative (user's decision).** The per-model files in `/config` decide. The UI also passes `--save-preset <name>` with a fixed name per model so profile `launch-<name>` and tuning results persist. Native presets live in `launch-presets/` next to the profile folder; the UI never reads them and never passes `--preset NAME` (that loads old `extra` settings and lays new `--set` values over them, so a setting deleted from the file would survive). `--save-preset NAME` alone loads nothing old (`settings_from_args()`). Unchecked: whether tuning results survive, and how existing run configs behave.
7. **Model path:** see section 3. Installed list: `strata models list --installed --output json`.
8. **Security (user's decision: no password for now, LAN only).** Model control and HTTP layer stay separate so a login can be added. Still required: start processes with argument lists only, never a shell; validate `--set` format (it sets arbitrary engine flags and `STRATA_*` vars); inference `API_KEY` at least 16 chars, separate from the UI, active with `--access lan`; against foreign websites: changes only via POST with JSON, `Origin` must match `Host`, optional allowed-hosts list against DNS rebinding (`ALLOWED_HOSTS`, default empty, recommended in template and guide); model names and paths must not leave the allowed directories, especially for delete (container runs as root).
9. **Tag:** base image `ghcr.io/niko1221/strata:<STRATA_VERSION>-<STRATA_FLAVOR>`, two build ARGs, defaults `0.2.9` and `cuda13`. The version is set only in the `Dockerfile`, for all flavors.
10. **Changing settings:** with `--no-tui` a launch refuses other settings while a model runs, and `--yes` never restarts. So stop, then start (or `--restart`). A different `--port` would start a second server next to it; the port stays fixed.
11. **Always pass `--vision` explicitly.** `launch` enables images by default when model and engine can; `DOCKER.md` says `VISION` defaults to `off`, but the entrypoint passes nothing when it is empty. Do not rely on a default.
12. **Own healthcheck.** The original probes `/v1/models` with the key and is dead without a model. Ours checks the UI port, no 2 h start period.
13. **Logs.** `strata_cli/logs.py` is "bounded request activity", probably no engine logs. The UI uses the launch output and the logs in `/data/install`. Engine log: `/data/state/strata/launch/<preset>.engine.log` (in the server test `docker.engine.log`); the split lines are there, not in `docker logs`.

## 6. Multi-GPU

### How the split works

Pipeline parallelism, not tensor parallelism. No NVLink or P2P needed; x4 and x1 slots work; mixed cards are intended. **Both cards compute** their own layers.

| Where | Holds | Does |
| --- | --- | --- |
| Helper | front layers, copy of dense weights, own KV cache for the full context, prompt buffer, experts of its layers | computes its layers; the first card runs the image reader by default |
| Main | back layers, same copies and buffers, experts of its layers, output head, draft layer (about 0.8 GB) | computes its layers, verifies draft predictions |
| RAM | expert arena, once for all cards | CPU pool computes experts that no card holds |

Decode runs the cards one after another per verify window (the faster waits for the slower). Prefill overlaps. The decode gain comes mostly from fewer CPU experts.

### Roles on the target system

- **3090 `main`, 5060 Ti `helper`.** Strata picks the faster card (SMs × max clock; within 5 % more memory wins). Engine log: 3090 82 SMs × 1.73 GHz vs 5060 Ti 36 SMs × 2.60 GHz; the server plan chose the 3090 as main.
- **Never hardcode card numbers;** they follow PCI order. The UI reads name and number from `nvidia-smi`.
- **Numbering (NVIDIA):** `--gpu-roles` uses `nvidia-smi` numbers (server: 0 = 3090, 1 = 5060 Ti). Inside the container the engine counts differently (CUDA0 = 5060 Ti, CUDA1 = 3090), roles still land right. The `strata launch` plan counts from 1 ("GPU 1 RTX 3090, GPU 2 RTX 5060 Ti"). The UI shows only `nvidia-smi` numbers and must not map `CUDA0`/`CUDA1` or "GPU 1"/"GPU 2" texts directly onto them.
- **Numbering (AMD, `setup.py` `amd_gpus()`, `v0.2.9`):** Strata reads the KFD topology `/sys/class/kfd/kfd/topology/nodes/<n>/properties` in node order, skips CPU nodes (`gfx_target_version` or `simd_count` 0), and counts GPU nodes. Neither `/sys/class/drm/card*` order nor PCI address. Integrated AMD GPUs get a number but are unsupported. Name and VRAM from `/sys/class/drm/renderD<drm_render_minor>/device`. Main PC: 7900 XTX is card 0, the Intel iGPU does not appear. `/sys/class/kfd` is visible in the ROCm container (section 13). Unchecked: whether `HIP_VISIBLE_DEVICES`/`ROCR_VISIBLE_DEVICES` shift numbers.
- Cards without a role are `off`. `cuda13` needs driver 580.65 or later.

### Limits and costs

- Each card holds the full-context KV cache and the dense weights, so the 16 GB card limits the useful context. Each extra card costs about 2 GB of buffers.
- `--vram-reserve-mib` applies to the first card (here the 5060 Ti), `--vram-reserve-later-mib` to later cards (here the 3090); label the fields with card names.
- `--resident-budget-gib` works in the split from engine 0.1.40. For UD models `setup.py` still recommends one card, and `--yes` without cards takes only one; always pass cards explicitly.
- Image reader runs on the CPU below 24 GB and on the card from 24 GB (about 1–1.4 GB VRAM). Effect when the 5060 Ti is first: open. `--vision gpu|cpu` decides.
- `--mtp off` saves about 1 GB VRAM but is slower.
- A slow extra card can slow a good pair down. The UI must make "3090 only" and "5060 Ti only" easy to compare.

Reference numbers (none from this card pair): 2× 2080 Ti 22 GB decode 35.2 → 51.0 tok/s (+45 %), prompt 11.6K 672 → 908 tok/s; 5080 + 3090, Coder, 32K: decode about equal to the 5080 alone, best K=26 84/110 tok/s; 2× 3090 x16 + x4: K=31 114–119 tok/s, K=27 130–136 tok/s.

### How the desktop app does it

Same layer split as `strata launch`. The host lists cards and shows combinations with a recommendation (`gpu_options`, memory math in `strata_control/gpu_fit.py`); per card Main/Helper/Off (`GpuRoles.tsx`; shared test vectors `tests/data/gpu_roles_vectors.json`). It writes to the run config:

```json
{"gpu_roles": {"1": "main", "0": "helper"}, "gpu": [0, 1], "gpu_order": "as_given"}
```

`gpu` lists helpers first, main last. The server reads `gpu_roles`; if `gpu` differs after a hand edit, `gpu` wins and the log says so. Engine splits layers (`layer_split: auto`). `strata launch --gpu-roles` writes the same structure via `gpu_roles.mirror` (`runner.py`), so the UI never touches the run config. `gpu_roles.py`/`gpu_fit.py` are in the image but internal. The app is a Node host with web frontend; this UI stays a small Python server without Node. "Run multiple models" (one model per card) is out of scope.

## 7. Further notes

- **RAM need:** a model fits when RAM holds the experts plus about 10 GB (`MODELS.md`). With 48 GB only Q2_0 (34 GB experts) and IQ2_XS (35.5 GB) fit in Normal mode; IQ3_XXS (43 GB) and IQ3_S (50 GB) do not. Swap does not count.
- **IQ3_S / IQ3_XXS on the server:** possible via low-RAM mode. `setup.py` picks it when RAM < experts + 10 GB (`low_ram_needed`); `--resident-experts` then keeps only experts no card holds, also in the split from engine 0.1.40. Evidence: `MULTI_GPU.md` community report #1447, 2× 3090, 32 GB RAM, GSQ-RCO IQ3_S with `--resident-experts`: about 137 tok/s decode, 1,798 tok/s prefill.
- **Own estimate for IQ3_S** (formula from `low_ram_gpu_gb`/`low_ram_resident`, split as 38 GB effective VRAM, KV counted on both cards, int8, MTP on, no reserve; Strata itself counts the longer KV once, section 10):

  | Cards | Context | Experts in VRAM | Experts in RAM | RAM incl. 10 GB |
  | --- | ---: | ---: | ---: | ---: |
  | 3090 only | 32K | 18.0 GB | 32.0 GB | 42.0 GB |
  | 3090 only | 128K | 16.7 GB | 33.3 GB | 43.3 GB |
  | 3090 only | 512K | 11.3 GB | 38.7 GB | 48.7 GB |
  | Split | 32K | 32.0 GB | 18.0 GB | 28.0 GB |
  | Split | 128K | 29.3 GB | 20.7 GB | 30.7 GB |
  | Split | 512K | 18.5 GB | 31.5 GB | 41.5 GB |

  A VRAM reserve lowers these. KV at 512K int8 needs about 6.75 GB more per card than at 32K (`setup.py`), about 7.2 GiB total (app formula). Pinned RAM cannot be swapped, so swap does not rescue a tight run; the RAM limit only protects the host.
- **"Leave 20 % for my work" (desktop app, `apps/host/server/native-resources.mjs`):** profile `work` keeps a fifth of VRAM free on a card with display (at most 1536 MiB on cards up to 16 GB), 512 MiB on cards without display, and limits CPU workers. The UI maps this to the reserve fields.
- **Plan without loading:** `strata launch --plan --family qwen --model iq3_s --context 512k --gpu-roles 0=main,1=helper`. `qwen` is the ISTA-DASLab packs (`DOCKER.md`).
- **KV format** (`DETAILS.md`): `fp16` 2112 B/cell; `int8` (default) 1056 B; `k8v4` 816 B (−23 %, 8-bit keys, more precise than `q4_0` on long documents, prompts 2–5 % slower; 3090 Coder at 198K 99 vs 85 tok/s); `q4_0` 576 B (about half, about 4 % faster at 128K, perplexity on long documents 8–12 % worse, needle tests pass). KV per card at 512K: int8 about 7.2 GiB, k8v4 5.6, q4_0 4.0. For big contexts try `k8v4` first. The form offers all.
- **Strata's own web pages** (`--open` server page, browser monitor, desktop Node host) exist only while a server runs or only on loopback; none can load a model.
- **Disk for own GGUF files:** IQ3_XXS: shard 1 47 GB (RAM + VRAM), shard 2 29 GB (lookup table on SSD), about 76 GB. Draft layer 5.2 GB, image reader 0.9 GB.
- **Tuning:** skipped without `--tune`. Quick about 20 min, thorough up to 2 h; reused for identical settings.
- `strata update` is not the update path in the container; the engine belongs to the image.
- **Status sources:** `strata runtime status` (used), `strata status --json`, `strata monitor --once`, `/metrics`, `nvidia-smi`, `/proc/meminfo`. `GET /health` needs no key; everything else needs `Authorization: Bearer <key>`.

## 8. Split test before the build (done 2026-11-03, passed)

Official image, Q2_0 with draft layer (about 71 GB), `GPU_ROLES=0=main,1=helper`, `--gpus all` plus `NVIDIA_VISIBLE_DEVICES=all` and `NVIDIA_DRIVER_CAPABILITIES=all` (as other GPU templates on the server). Other containers on host port 8080 must be stopped first. Card numbers: `nvidia-smi --query-gpu=index,name,pci.bus_id,memory.total --format=csv`. Pass criteria: `layer split` lines in the engine log, main on the 3090, VRAM on both cards, stop under 10 s, cards free afterwards, second start without download. Exit code 0 of `docker stop` proves nothing (the official stop handler always exits 0). Results: section 12.

## 9. Open points for the server

Done: split in container (yes), numbering (section 6), driver `580.196.07` (open-source, above `580.65`), `--gpus all` (yes), `--stop-timeout` (ignored), split needed (yes), RAM 48 GB + 48 GB swap, host RAM tight (about 95 %), `runtime status` fields (section 5), `nvidia-smi` in container (needs the Unraid NVIDIA driver plugin; UI reports a missing `nvidia-smi` clearly), model swap without restart (local yes, section 13), catalog in the image (yes, section 13), placeholder vs engine port (section 13).

Open:

- [ ] Split speed vs 3090 alone. The 5060 Ti is on a slow link (7.2 GB/s). Measurable via the UI.
- [ ] How much slower with part of the model in swap? With `resident-experts`/`mmap-experts` swap is rarely needed.
- [ ] RAM limit vs `--ulimit memlock=-1:-1`: pinned memory cannot swap. Test without or with limited `memlock`.
- [ ] IQ3_S (GSQ-RCO) in the split with `--resident-experts`; usable context.
- [ ] `--gpu` per launch or only at first setup? (irrelevant while only `--gpu-roles` is used)
- [ ] Do `--set vram-reserve-mib=N`, `resident-budget-gib` and the other RAM settings act as expected?
- [ ] Real fields of `--plan --output json` beyond those in section 10.
- [ ] What `strata logs` shows in the container.
- [ ] IQ3_S above 256K (rope scaling warning, section 10).
- [ ] RAM estimate per mode is rough (Normal = experts from catalog; "only what no card holds" = experts minus estimated card space; budget = N; files = none). Compare with real runs.

## 10. Local tests without Docker (2026-11-02)

Main PC, no GPU load, `strata-control-0.1.55` (`strata 0.2.9`, Python 3.12.15) in the scratchpad. Nothing on the system changed. Units follow Strata; formulas mix GB and GiB; 512K values are estimates.

**Desktop app IQ3_S run config** (`~/.local/share/strata/install/strata-iq3_s.json`): `--max-context 524288 --rope-scaling yarn --rope-scale 2 --kv int8 --expert-cache auto --prefill auto --spec 4 --spec-min-p 0.5 --vision (GPU) --pool-workers 15 --vram-reserve-mib 4915 --resident-budget-gib 40`. So the desktop runs with a RAM budget, not Normal. Rule in `native-resources.mjs`: budget when 80 % of RAM < experts + 10 GB; value `floor(RAM × 0.8) − 10`, min 8, max experts. 4915 MiB = a fifth of 24 GB. For 48 GB RAM the rule gives 28. The current app profile also sets `STRATA_RESIDENT_HEADROOM_GIB` (a fifth of RAM).

**`gpu_roles.recommend`** (speeds from memory): 3090 = 0 → `0=main,1=helper`, `gpu` `[1, 0]`; 5060 Ti = 0 → `1=main,0=helper`, `gpu` `[0, 1]`. The 3090 is main and last either way.

**`gpu_fit.options`** with 48 GB RAM, no extra context, no reserve (fit, RAM GB, SSD GB):

| Size | 3090 only | Split | 5060 Ti only | Recommended |
| --- | --- | --- | --- | --- |
| Q2_0 | fits, 17.6, 0 | fits, 3.6, 0 | fits, 25.6, 0 | 3090 only |
| IQ2_XS | fits, 19.2, 0 | fits, 5.2, 0 | fits, 27.2, 0 | 3090 only |
| IQ3_XXS | fits, 27.0, 0 | fits, 13.0, 0 | tight, 35.0, 0 | 3090 only |
| IQ3_S | tight, 34.8, 0 | fits, 20.8, 0 | spills, 38.0, 4.8 | **Split** |

The RAM column is not Normal mode: `gpu_fit.fit()` computes `in_ram = experts - on_gpu` (Q2_0 split: 37.6 GB "RAM + VRAM" − 1 GB `DENSE_EXTRA_GB` − 33 GB on cards = 3.6). Normal keeps all experts in RAM (`MULTI_GPU.md`); the server used roughly 30–35 GB (section 12). The 34 GB "experts in shard 1" are all experts; shard 2 is a 29 GB lookup table on SSD.

**`setup.py` with 48 GB and IQ3_S** (experts 50.3 GB, download 83.6 GB, 88.8 GB with draft): `low_ram_needed` true (also IQ3_XXS; false for Q2_0, IQ2_XS). `--resident-experts` fits: 3090 32K int8 (18.0 GB experts in VRAM) yes; 3090 512K (11.3) no, falls back to `--mmap-experts`; split 128K int8 (30.7) yes; split 512K int8/k8v4/q4_0 (25.3/26.8/28.3) yes. This formula counts the longer KV once; the real value is probably nearer section 7's table.

**512K warning** from `--plan` for IQ3_S: "512K is more than the model was trained on (256K). Setup adds rope scaling for it (experimental); answers may get less reliable." The UI shows plan warnings.

**Real CLI behavior:**

| Command | Result |
| --- | --- |
| `launch --list-presets --output json` | `{"schema_version": 1, "ok": true, "presets": []}` |
| `launch --status --output json` | `{"schema_version": 1, "ok": true, "running": []}` |
| `launch --stop`, also unknown preset | "Nothing from `strata launch` is running.", exit 0 |
| `launch --plan ... --output json` | exit 0, one line `{"schema_version": 1, "ok": true, "plan": {...}}` with `profile`, `settings`, `warnings`, `flags`, `steps`, `model`, `download`, `drafting`. Refusal: `{"ok": false, "error": {"code": N, "message": ...}}` |
| `--set 'bad name=1'` | refused: "is not a setting name" |
| `--set no-such-flag=1` | no warning in a plan without installed engine |
| `--gpu 0 --gpu-roles 0=main` | refused: "Use --gpu ... or --gpu-roles ..., not both." |
| `--gpu-roles 1=main,0=helper` on one card | refused: "There is no card 1 that this build of Strata can use." |
| `--model nope` | refused with model list |
| `--set resident-experts=` | flag without value, appears as `--set resident-experts` |

Consequences: `--plan` checks roles against visible cards (split plans only on the server). Without `--access` it plans `local`, so pass `--access lan`. Without `--save-preset` the profile is named after the settings (e.g. `launch-iq3_s-512k`), so the UI names profiles itself. Without `--vision` it planned `gpu` here; it depends on the cards. Engine help says `--gpu` takes "comma-separated nvidia-smi/PCI indices". `setup.py --help` confirms `--no-start`, `--low-ram {auto,on,off,resident,mmap}`, `--gpu-roles`.

## 11. Docker tests (2026-11-02)

Main PC, real image `0.2.9-cuda13`, no GPU in the container; everything removed afterwards.

- Image content: terminal `strata 0.2.9` build `0.1.55`, Python 3.12.15; engine `0.1.55-linux-x86_64-cuda-cuda13` for `sm_75`, `sm_86`, `sm_89`, `sm_120` (3090 is `sm_86`, RTX 50 is `sm_120`). Minimum driver per engine manifest `580.65`. Present: `/usr/bin/tini`, `/opt/strata/terminal/setup.py`, `/opt/strata/terminal/tools/strata_mcp.py`, `/opt/strata/seed/third_party/llama.cpp`.
- **Missing in the image:** `curl`, `wget`, `ps`, `ss`, `python3` on `PATH`, `nvidia-smi` (comes via the NVIDIA toolkit with `--gpus all`, confirmed in section 12). Supervisor and healthcheck use `/opt/strata/terminal/python/bin/python3` and `/proc`.
- Engine help omits `--vram-reserve-mib`, `--vram-reserve-later-mib`, `--resident-budget-gib`, `--layer-split`; the binary contains them plus `--resident-experts` and `STRATA_RESIDENT_HEADROOM_GIB`.
- Official entrypoint without NVIDIA card: exits after about 1 s with code 3, no download (`"usable": true` missing). **The supervisor must do this check itself:** per `TERMINAL_LAUNCH.md` `strata launch` downloads the model even without a usable engine and then exits 0 without starting anything.
- `--status`/`--stop` without model: exit 0, container keeps running. `--plan` with `--access lan --port 8080 --data-dir /data/Strata-data` gives valid JSON and only creates empty folders. `ulimit -l` is `unlimited`, `/dev/shm` 2.0 GB.
- Throwaway prototype (not in the repo) under `tini` with a `strata` stub (`test/python/fake_strata.py` today): own `HEALTHCHECK`/`ENTRYPOINT` work (healthy after about 2 s without a model); no zombies with double-forked children; `docker stop` with stub reacting to SIGINT 2.6 s; stub ignoring SIGINT/SIGTERM escalates to SIGKILL in 6.8 s (3 s + 3 s waits); real `strata launch --stop` in the SIGTERM path about 0.1 s.

## 12. Server test (2026-11-03)

Unraid, `0.2.9-cuda13`, engine `0.1.55 (cuda)`, driver `580.196.07`, qwen Q2_0, 64K, `VISION=off`, `GPU_ROLES=0=main,1=helper`. All criteria of section 8 passed.

- `nvidia-smi` in the container: 0 = RTX 3090 (24,576 MiB), 1 = RTX 5060 Ti (16,311 MiB). `strata components engine` reports `"usable": true`.
- CUDA numbering in the container is reversed: CUDA0 = 5060 Ti (36 SMs), CUDA1 = 3090 (82 SMs). Plan: "Main: GPU 1 RTX 3090. Helper: GPU 2 RTX 5060 Ti."
- Split: CUDA0 (5060 Ti) layers 0–17, CUDA1 (3090) layers 18–47 and head. 80 % of experts in VRAM, rest in host RAM. Auto `K=18`, predicted 29.7 ms per decode window. Links: 5060 Ti 7.2 GB/s (slow slot), 3090 24.1 GB/s. Split lines only in `/data/state/strata/launch/docker.engine.log`.
- VRAM: 3090 23,702 MiB, 5060 Ti 15,124 MiB, all Strata. The 3090 is at the limit: the plan wanted 796 MiB free, 388 MiB were free (CUDA sees only 23.56 GiB ≈ 24,125 MiB of the 24,576; the driver holds about 450 MiB).
- Host RAM about 95 % used, about 5 GB in swap; Unraid stayed stable. Strata RAM roughly 30–35 GB (user's estimate), fits Normal mode with all Q2_0 experts. Plan warning: "is a tight fit: tight: close other programs".
- Load of an installed model about 30 s to "Strata is serving", first token 1.1 s. `strata monitor --once` via `docker exec` shows `READY`, 3 slots.
- `time docker stop`: 4.4 s, both cards at 0 MiB afterwards. Second `docker start` without download.
- Not covered: stop via the Unraid UI, speed under load, IQ3_S, RAM limit with swap.

## 13. Local container test (2026-11-04)

Main PC, own image on `0.2.9-rocm-gfx110x`, 7900 XTX, 64 GB RAM, `/data` in a local folder, `/config` in `local-mounts/config`, `PUID`/`PGID` 1000. Models `coder IQ1_M` and `gyro Gyro-S` (about 58.5 GB each plus 5.2 GB draft layer).

- Start checks pass, backend `hip`, `/sys/class/kfd` visible, 7900 XTX is card 0, Intel iGPU absent.
- Catalog via `Catalog().strata.model_table()` works in the image. Family `qwen36` is app-only; `strata launch` does not know it, and its plan wrongly says "Nothing to download: the model is already on this computer". The UI skips families from `own_families()` like `Catalog.rows()`.
- Placeholder: `GET /v1/models` 200 with empty list, else 503. Releases the port before a load; while Strata is `ready` its bind fails ("Address already in use"). Ten requests right before a load did not disturb the engine start.
- Load of `coder IQ1_M` with download, check and load: OK. Port 8080: 401 without key, real answer with key. VRAM 24,130 of 24,560 MiB.
- Unload 1.6 s: `stopped`, VRAM free, RAM back, placeholder has the port again.
- Cancel during download 0.6 s, no `setup.py` left (group signals, section 5, point 4). Next start resumed the download ("Getting the model files" instead of "Downloading the model files").
- Swap A → B (`gyro Gyro-S` after `coder IQ1_M`) without container restart, answer on 8080.
- `time docker stop` with a loaded model: 3.6 s, exit 0, VRAM free.
- Strata prints the start of the API key in its summary ("API key 1a2b3c4d5e••••"); the UI masks this line and the whole key in all logs.
- Plan texts count from 1 here too ("Main: GPU 1 RX 7900 series" for card 0).
- **RAM spikes with an almost full card (2026-11-07):** with `coder IQ1_M` loaded, free host RAM dropped by about 15 GiB for 1–2 s every 10–35 s; container `memory.stat` unchanged. Cause: with Strata's default reserve (700 MiB) only about 850 MiB VRAM stay free; when the desktop needs VRAM (a playing video) `amdgpu` evicts the expert cache (15.44 GiB) to system RAM (kernel memory, not the container's). With `vram_reserve_mib = 1000` the spikes stopped (user's test). Likely irrelevant on the headless NVIDIA server. Strata warns in the runtime log that it needs "of about 30 GB" RAM.

## 14. State of the build (last 2026-11-07)

Built with two Claude sessions (implementer + reviewer). Moved from a private repo to the public GitHub repo `sognix/strata-supervisor` on 2026-11-07 (image `ghcr.io/sognix/strata-supervisor`, files under `/opt/strata-supervisor/`; the Python package keeps the name `strata_webui`).

### What is built

| File | Content |
| --- | --- |
| `Dockerfile` | `FROM ghcr.io/niko1221/strata:${STRATA_VERSION}-${STRATA_FLAVOR}` (defaults `0.2.9`, `cuda13`), labels `strata.base` and `strata.flavor`, healthcheck on `UI_PORT`, entrypoint `tini` → image Python → `app/backend/main.py`; same tree under `/opt/strata-supervisor/` |
| `app/backend/strata_webui/host/startup.py` | start checks (section 4, steps 1–5), `PORT`/`UI_PORT`, API key |
| `app/backend/strata_webui/core/ownership.py` | `PUID`/`PGID` for everything created in `/config` |
| `app/backend/strata_webui/host/gpus.py` | card query: `nvidia-smi` or KFD topology (section 6) |
| `app/backend/strata_webui/host/memory.py` | RAM and swap of the container (`DESIGN.md` section 4) |
| `app/backend/strata_webui/strata/launcher.py` | load, unload, cancel, stop in 8 s, state only from `runtime status`, group signals, launch output per model, `PYTHONUNBUFFERED=1` |
| `app/backend/strata_webui/strata/placeholder.py` | 503 placeholder on `PORT` |
| `app/backend/strata_webui/strata/model_config.py` | one TOML per model in `/config/models`, validation, translation to flags |
| `app/backend/strata_webui/strata/catalog.py` | `strata models list`, catalog via `Catalog().strata.model_table()` |
| `app/backend/strata_webui/strata/gguf.py`, `own_models.py` | GGUF header (architecture, name, `file_type`, trained context), folders in `/models`, trained context of Strata downloads |
| `app/backend/strata_webui/strata/model_files.py` | Models page: list and delete Strata downloads (with packs) and own GGUF folders (a link only as link) |
| `app/backend/strata_webui/strata/engine_report.py` | memory values from the last engine log |
| `app/backend/strata_webui/core/` | command runner, cache, log buffer, log tails |
| `app/backend/strata_webui/web/api.py`, `web/server.py` | JSON API (logic) and HTTP |
| `app/frontend/` | "Strata Supervisor": pages Model configs (list; per config Overview, Logs, Settings), Models (files on disk), Logs, Hardware. Design in `DESIGN.md` |
| `unraid/strata-supervisor.xml`, `unraid/strata-supervisor.png`, `ca_profile.xml` | Unraid template, icon (256 px PNG), Community Applications profile |
| `.github/workflows/build.yml` | job `test` (unit tests), then job `build` as a matrix, one parallel job per flavor: image build, smoke test without GPU, push to ghcr.io. One package, tags with flavor suffix (`:dev-<flavor>` on main, `:X.Y.Z-<flavor>`/`:X.Y-<flavor>`/`:latest-<flavor>` on tags `vX.Y.Z`); `cuda13` also gets the tags without suffix. Docker v2 manifest for Unraid's update check |
| `LICENSE`, `NOTICE.md` | MIT; Strata's MIT license and the components of the base image |
| `README.md`, `docs/USER-GUIDE.md`, `CONTRIBUTING.md` | overview and configuration; usage; building and testing |
| `test/python/` | unit tests with fake `strata` (`test/python/fake_strata.py`); `test/python/dev_run.py` runs without Docker (moved from `tests/` on 2026-11-07) |
| `test/unraid/` | test of the Unraid template and its tag selection through a local CA feed (`UNRAID-TEST.md`) |

### Decisions during the build

- A failed start check does not end the container; the UI shows the error and blocks loading (the original exits 3).
- One API key for all models, always via `--api-key-env`.
- Launch in its own session, signals to the whole group (section 5, point 4).
- Own GGUF files may live under `/data` or `/models`. `/models` follows Strata's `docs/DOCKER.md`, optional path "Own GGUF files" in the template, writable since 2026-11-07 so the Models page can delete. Unchecked: whether Unraid skips an empty optional path cleanly.
- Blocked `--set` names: everything the form or launcher sets itself (RAM mode, VRAM reserve, `host`, `port`, `parallel`, `gpu`, `gpu-roles`, `api-key`, `vision`, `mtp`, `kv`, `max-context`).
- API key masked in all UI logs.
- Reviewer's evening review (11 points, all fixed), among them: ports are read before all other checks so the UI is reachable on `UI_PORT` even when `/data` is not writable; the page never polls overlapping (next round starts 2 s after the last ended); `nvidia-smi` cached 2 s; errors cached at most 10 s; a leftover `setup.py` is ended even when the launch already finished; saving leaves no `.tmp` files; invalid file names in `/config/models` are shown, not swallowed.
- **Flavors (2026-11-07, user's decision):** the image is published for every Strata flavor the UI can drive: `cuda13`, `cuda12`, `rocm-gfx120x`, `rocm-gfx110x`, `rocm-gfx1151` (cards per flavor: Strata `docs/DOCKER.md`). One ghcr.io package with tag suffixes, like Strata's own images, so Unraid's template `<Branch>` selection only switches the tag. `cuda13` is the default and also gets the tags without suffix. `sycl` (Intel Arc) is left out: `host/gpus.py` has no card query for it (`source_for_backend()` returns `None`), and Strata does not download models for Arc in the container (only own files). `main` is protected; dev images come from merges into `main` (a `push` event for Actions). Dependabot stays on GitHub Actions only; it cannot follow a base tag built from ARGs. One Unraid template with branches and a guide, not one template per vendor (CA policy prefers branches).
- **Download marker (2026-11-07):** while Strata downloads, the state reads "loading (downloading)" everywhere (config header, list, top bar). The user wanted no percentage, only a visible sign. Sources: Strata's launch line ("Downloading/Getting the model files"), or `status.model.download` growing between two polls (`web/api.py` `_download_size()`: size of `/data/Strata-data/models/<folder>` for a loading Strata model without `model_path`, plus `download_gb` from the catalog; `app.js` `trackDownload()`/`downloading()`). Even with `PYTHONUNBUFFERED=1` no download lines arrive during the download, locally or on the server (output stops at "Checking the engine for this PC"); probably a child writes them elsewhere, not investigated. So in practice the folder growth is the signal. Tested locally with a partial `qwen Q2_0` download (cancel ends with exit code 130). Server test with a real download open.
- **Lesson (2026-11-07):** when the first percentage attempt was rolled back, the call `trackDownload()` stayed in `tick()` without the function; every poll threw and the top bar showed "web UI not reachable" while everything worked. The `catch` in `tick()` now logs with `console.error`. When rolling back, grep for every name you remove.

### Open

- Flavors without a real card test: `cuda12`, `rocm-gfx120x`, `rocm-gfx1151` (Strix Halo is an APU with shared RAM; the VRAM value and the fit and RAM estimates are probably wrong there). Whether AMD needs `--group-add` when the container runs as root: unchecked.
- Server test with the published image (split, CUDA, stop via Unraid UI, 3090 only vs split, download marker).
- RAM limit and `memlock` (section 9); the template sets no `--memory` yet.
- RAM estimate without KV cache (section 9).
- **Strata install per version is shared (found 2026-11-07, untested):** Strata keeps one run config per version (`/data/install/strata-<tag>.json`). Loading own GGUF files ("Preparing … from your model files") rewrites it to `/models/…`. Afterwards the Strata config of the same version (no `model_path`) probably loads from `/models` too; deleting the own folder probably makes Strata download again. Check before trusting the "used by" column of the Models page.
- **Banner after a failed launch** shows Strata's status error ("status output has no state") instead of the last launch line; the real message is only under Logs → Launch. Proposed to the user, not built.
- Copying a Strata model folder into `/data/Strata-data/models` by hand (`docs/USER-GUIDE.md`, section 5) is untested.

## 15. What to check after a Strata update (2026-11-07)

The UI depends on these places in Strata `v0.2.9`. Check them for a new image tag:

- **Catalog probe** (`strata/catalog.py`, `CATALOG_PROBE`): `strata_cli.launch.catalog.Catalog`, `.strata.model_table()`, `.strata.own_families()`, `.tables()[1]` (field `tag`), `.contexts()`, `.contexts_for()`.
- **Groups and plain names** of versions: rebuilt from `apps/host/src/lib/native-catalog.ts` (`app.js`: `VERSION_LABELS`, `EDITIONS`, `groupOf`).
- **Folder of a Strata model:** `models/<tag><Version>`, packs in `packs/<lowercase>` (`Catalog.model_folder`, `strata_control/models_move.py`).
- **Engine log lines** (`strata/engine_report.py`): "slot sessions", "expert cache N slots, X GiB of VRAM", "resident RAM mode: X GiB", "loaded X GiB at", "conversation cache sized from the free RAM", "draft layer loaded", "MiB of VRAM free with everything loaded".
- **Launch output** (`app.js`: `PROGRESS`): "Downloading the model files: x of y GB", "Getting the model files", "Downloading the draft layer".
- **RAM estimate** (`app.js`: `EST`): constants from a `coder IQ1_M` run on the 7900 XTX, engine 0.1.55; rules from `docs/AMD_HIP_PERFORMANCE.md` (4 GiB headroom) and `native-fit.ts` (KV cache).
- **Runtime folders:** `runtime` and `procs` must belong to the running user, mode 0700 (`strata_control/runtime.py`).
- **`--plan --output json`:** shape `{"ok", "plan": {...}}` or `{"ok": false, "error": {...}}`.
- **`runtime status` JSON** (section 5, point 5) and the `strata launch` flags the UI passes (`model_config.py`, `launcher.py`).
