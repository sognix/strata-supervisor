# Web UI design

Audience: Claude sessions. The UI design as built and the user's decisions behind it. Section numbers are referenced from code comments; keep them stable. Discussed 2026-11-05 after the first server start, built 2026-11-07, not yet tested on the server.

The user is not a designer and gives no pixel details. Goal: clear, related things visibly grouped, one look on every page.

## 1. Prepared Strata models

The "Model" section of the config form has two tabs, **Prepared Strata model** and **GGUF file**; only one is active.

- **Source:** Strata's catalog (`Catalog().strata.model_table()`, `app/backend/strata_webui/strata/catalog.py`). No own list.
- **Every family `strata launch` offers:** since Strata 0.2.16 that includes the own-path family `qwen36` (Qwen3.6-35B-A3B, Strata #5869). Up to 0.2.13 it was app-only and the probe skipped it (`IMPLEMENTATION.md` section 13). The probe marks own-path rows with `own_path` and passes the catalog's `context_fit`.
- **Shown like the desktop app** (`app/host/src/lib/native-catalog.ts`, Strata 0.2.16; `apps/` up to 0.2.13): groups "Qwen3.8-Flash-Next" (editions Swift, Coder, Unsloth, Gyro), "Qwen3.6-35B-A3B", then "Tencent Hy4-Flash" and "Inkling-2-Mini" (Strata 0.2.18/0.2.19, the family title as group). DeepSeek-V4.1-Flash was dropped with Strata 0.2.16 (0.2.13 was the last version that ran it). Plain names: `Q2_0` Fastest, `IQ2_XS` Compact, `IQ3_XXS` Balanced, `IQ3_S` High quality, `IQ1_M` Coding, `Gyro-S` Smallest, `Gyro-M` Small memory, `SQ-64` Flagship, `SQ-96` High quality, `SQ-128` Highest quality (Hy4-Flash), `SQ-20` Lossless, `SQ-15` Flagship, `SQ-10` Compact (Inkling-2-Mini). Plus download size and `ON DISK`.
- **Selecting** fills Version, Family and a name proposal (e.g. `q2_0-32k`, `coder-iq1_m-32k`). Not the context: the catalog has no default context per model.
- With "Prepared", `model_path` is saved empty. Never put the Strata model's path there: a value switches Strata to "own files" and it starts the model differently. Strata finds its models under `/data/Strata-data/models` itself.
- **Fit marker:** each prepared model is green ("fits": experts fit on cards and RAM without SSD), yellow ("SSD reads": only with RAM budget or "From the model files") or red ("too big": what does not fit on the cards exceeds RAM plus swap). Uses what the container may use (`memory.max`, `memory.swap.max`), host values only without a limit. Basis: the "Only what no card holds" estimate plus 4 GiB headroom Strata needs for locking RAM (`AMD_HIP_PERFORMANCE.md`). Cards, context, KV format, parallel requests and reserve from the form count.
- **"Version"** (formerly "Model size"): Strata's name for `--model`, not always a quantization (`Gyro-S`, `IST-DAS-2.00bpw`). Family and Version are dropdowns from the catalog; text fields only when the catalog cannot be read.

### Own GGUF files

- Tab "GGUF file": a dropdown "Folder" with the subfolders of `/models`, no free path, no "Other folder" box. Unusable entries are greyed out with a reason; a saved path outside `/models` stays as an entry (`GET /api/own-models`, `strata/own_models.py`).
- Only the header of the first file is read (`strata/gguf.py`: architecture, name, repo URL, `general.file_type`, trained context), never the file name (names are free). Family is proposed when name or repo URL match exactly one Strata family; Version when the family has one, or `general.file_type` matches exactly one version name.
- Strata starts only its own models (`docs/MODELS.md`); an arbitrary GGUF like Gemma does not work.

## 2. Form sections

Five sections as cards, numbered, with heading and more space between them:

1. **Model:** prepared model or own GGUF, name
2. **Run:** context, KV format, vision, draft layer (MTP), parallel requests
3. **Cards:** card choice and roles
4. **Memory:** VRAM reserve and RAM mode
5. **Advanced:** `--set` rows and notes, collapsed by default

Fixed action bar with **Save** and **Plan**. Plan asks Strata with the unsaved form values (`POST /api/models/<name>/plan` with `{"config": …}`) and saves nothing.

- **Max context under "Context":** for own GGUF folders and downloaded Strata models from the file header (`<arch>.context_length`, "says its file"), else from the catalog (`Catalog.contexts_for`, "Strata offers up to …"). The folder of a Strata model comes from the catalog probe (`tag` + version, like `Catalog.model_folder`). More than the trained length turns the field yellow, not red: Strata allows it and only warns.
- **Native models (Hy4-Flash, Inkling-2-Mini):** rows with `native`. Like own-path rows the form disables KV format and reserve and hides the RAM tiles (with its own note). Pictures only on or off; "Draft layer (MTP)" becomes "Drafter" with Strata's default ("on (built into the model file)" for Hy4, "Strata's choice" in the form and "Strata's choice (measured at the first start)" on the model page for Inkling, Strata 0.2.21; the note under the field says it is measured) or "off" (`--drafting off`); parallel up to 1 (Hy4) or 64 (Inkling); the context list is the row's own. Cards (Strata 0.2.20): main, helper and off with the "Split over all cards" chip; a warning banner when more cards are on than Strata uses (Hy4 4, Inkling 4 on `cuda`, else 2: "Strata uses at most N cards for this model on this backend. Set the others to Off, or the main card may be left out."). It warns and does not block. The model page shows Strata's placement (card, RAM, SSD) instead of the UI's estimate; the fit marker is Strata's verdict ("SSD reads" for `cross_tier_ssd`, or when Strata's RAM figure is more than the container has). The Models page lists their folder file by file (found by the family name or a `.strata` file in it).
- **Own-path models (Qwen3.6):** when the chosen version has `own_path`, the form disables KV format and both VRAM reserve fields, hides the RAM mode tiles behind a note, offers no "Split" chip and no helper role, and turns helpers off (its engine runs on the first visible card, `IMPLEMENTATION.md` section 6). The saved config then has `ram_mode = "normal"`, no reserve and only one card on. The model page shows these settings as "not used by this model" and adds the context cache on the main card to the memory table.
- **Validation while typing:** a field gets a red border as soon as its value is wrong and loses it when right; the reason is in the tooltip. Rules match `model_config.py`. Required fields (Name, Folder, Version) turn red only on save when empty. A server error marks and focuses the matching field.

## 3. Look

The old problem: background, boxes, fields and buttons had the same grey; nothing stood out, the selection was invisible.

1. Sections as cards: slightly lighter than the page, border, rounded corners. Header with number in a blue circle, bold title, short grey explanation on the right.
2. One accent color for "selected": the blue of Save (`--accent`). Selected tab, card choice, RAM mode and focused field get a blue border and a light blue tint.
3. Choices as tiles: RAM modes as clickable tiles in two columns; each GPU as a tile with number, name, VRAM and role chips.
4. Badges: green `ON DISK`, blue `MAIN`, grey `HELPER` and RAM need, yellow `CAN BE SLOWER` and warnings.
5. Inputs darker than the card.
6. Short bold label, small grey explanation below.
7. More space between sections, less inside.
8. Monospace for values: paths, names, MiB, GiB, `--set` rows.
9. Light and dark theme; sidebar and top bar as before.

## 4. RAM budget from the container limit

The user's server container: `--memory 25g --memory-swap -1`, no `--ulimit memlock` (user's decision). The template sets no limit by default.

- User's decision (2026-11-05): the budget may be at most the container's RAM plus swap. No reserve, no warning.
- RAM: `/sys/fs/cgroup/memory.max`, without a limit (`max`) the host's `MemTotal`. Swap: `/sys/fs/cgroup/memory.swap.max`, without a limit (`max`, e.g. `--memory-swap -1`) the host's `SwapTotal`. If `memory.swap.max` is missing the kernel does not count swap; then the host swap applies.
- Built: `/api/status` returns `limits` (RAM and swap with source `container` or `host`, `host/memory.py`). Below "Keeps up to N GiB of experts in RAM …" a line like "Available: 25 GiB RAM, 48 GiB swap". Proposal in the field: container RAM in whole GiB. More than RAM plus swap is refused by form and server.
- Open: check on the server that the container sees the values: `docker exec strata-supervisor sh -c 'cat /sys/fs/cgroup/memory.max /sys/fs/cgroup/memory.swap.max; grep -E "MemTotal|SwapTotal" /proc/meminfo'`. Expected: `26843545600`, `max`, then host RAM and swap.

## 5. Pages

Shared classes in `app/frontend/style.css` (`card`, `card-head`, `tile`, `badge`, `chip` for tabs and switchers, `actionbar`), no per-page styles. Functions in `app/frontend/app.js`:

- **Model configs** (`#/configs`, `modelsPage`): list as a card, state badge per config (green `READY`, blue `LOADING`, grey `IDLE`, red errors), name in monospace, button "New config".
- **Config page** (`modelPage`) with tabs Overview (`overviewTab`: state, plan, buttons; Load as blue main button), Logs (`modelLogsTab`), Settings (`settingsForm`, same form as New config).
- **Models** (`#/models`, `modelFilesPage`): two lists, "Strata models" (`/data/Strata-data/models`) and "Own GGUF files" (`/models`); per entry path, size, configs using it, red trash with confirmation (`strata/model_files.py`, `GET /api/model-files`, `POST /api/model-files/delete`). A Strata model takes its packs along; the shared draft layer stays. Only entries from its own list are deleted, a link only as a link, nothing while a model is loaded. `/models` is mounted writable for this.
- **Logs** (`logsPage`), **Hardware** (`hardwarePage`: start checks with badges, GPUs as tiles, RAM/swap/limit as tiles).
- **Sidebar buttons (2026-11-07, user's wish, modeled on CoolerControl):** right-aligned `+` next to "Model configs" (opens New config); next to each config a pencil (its Settings) and a trash can (deletes after confirmation, same as the Delete button in Settings, `deleteConfig()` in `app.js`). Always visible; long names are cut with "…". The server refuses deleting the loaded config ("Unload it first").
- **Versions (2026-11-07, user's wish):** two small grey lines under "Theme": "Strata version" and "Supervisor version" (`renderVersions()` in `app.js`, `versions()` in `web/api.py`). Values come from the build: `SUPERVISOR_STRATA_VERSION`, `SUPERVISOR_VERSION` (`1.0.0` from a tag `v1.0.0`, else `dev` plus the commit, e.g. `dev (f01ec60)`); tooltip shows the flavor. A local run shows `unknown` and `dev`.
- **Changelog and Strata links (2026-11-23, user's wish):** "Strata" links to `https://github.com/Niko1221/Strata`, the Strata version to `…/releases/tag/v<version>` (new tab, `noopener noreferrer`; plain text when the version is not a release number). The Supervisor version opens a `<dialog>` with all of `CHANGELOG.md` (newest first, the installed one marked "installed"). Once after an update a popup "What's new in Strata Supervisor X" shows the sections since the last seen version, with "Full changelog" and "Got it"; closing it any way (button, ✕, Escape, click beside it) posts `/api/changelog/seen`. Remembered in `/config/last-seen-version` (user's choice: once per installation, no users to tell apart). A fresh install (no file, no saved config) writes the current version at startup, so no popup; an install with configs but no file (an update from a version before this feature) shows only the current section; `dev` builds never show it and never write the file. The Markdown subset (paragraphs, `- ` bullets with indented continuation lines, `` `code` ``, `[text](https://…)`) becomes DOM nodes, never raw HTML (`mdBlock`, `mdInline`). Backend: `web/changelog.py`, routes `GET /api/changelog` and `POST /api/changelog/seen` (same Origin check as every change). `CHANGELOG.md` is copied into the image (`/opt/strata-supervisor/CHANGELOG.md`).
- **State text:** "idle", "loading", "loading (downloading)", "ready" etc. (`stateText()` in `app.js`; download detection in `IMPLEMENTATION.md` section 14).

## 6. Name and logo

- **Name "Strata Supervisor"** (user's decision, 2026-11-05): it starts, watches and stops Strata. Sidebar shows "Strata" over "Supervisor" in the same font, size and color; also the page title.
- **Logo variant B** (user's decision, 2026-11-07): a power symbol over the three bars of the Strata logo. Ring white (light theme: almost black), switch stroke and middle bar green like Strata (`#34d399` dark, `#10b981` light), outer bars like the ring. Strata's logo file is not used, only the shape of the bars rebuilt. File: `app/frontend/logo.svg` (sidebar and favicon). Readable from 24 px; at 16 px the bars blur a little.
- Unraid icon: `unraid/strata-supervisor.png`, 256 px, the logo on a dark rounded tile so it works on light and dark themes.
- Before publishing, check that the name is free on GitHub and Docker Hub.
