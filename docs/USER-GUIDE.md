# User guide

How to get from a fresh container to a model that answers, and how to work with it day to day. Container
options and building are in the [README](../README.md).

## 1. The idea in short

- The container starts **without a model**. It runs only the web UI (port 8081) and a small placeholder on the
  API port (8080) that answers `503 No model loaded in Strata.`
- You keep **several model configs** side by side, one file each. Only **one model is loaded at a time**, and
  only when you press Load. Nothing loads or swaps by itself.
- A model's files are **downloaded on its first load**. After that they stay in `/data` and later loads take
  seconds to a minute.

## 2. Install on Unraid

### Pick your image

There is one image per graphics card family, built on the matching Strata image. When you install from
Community Applications, Unraid asks which one you want; **Default** is the first row.

| Your card | Pick | Tag | Needs |
| --- | --- | --- | --- |
| NVIDIA RTX 20, 30, 40, 50 (also RTX A / Quadro), driver 580 or newer | Default | `latest` | Nvidia-Driver plugin |
| Older NVIDIA: GTX 10 (Pascal), Tesla P40 / P100, V100, or any NVIDIA card with driver 575 to 579 | `latest-cuda12` | `latest-cuda12` | Nvidia-Driver plugin |
| AMD RX 9000, Radeon AI PRO R9700 (RDNA 4) | `latest-rocm-gfx120x` | `latest-rocm-gfx120x` | nothing extra |
| AMD RX 7900, 7800, 7700, 7600 (RDNA 3) | `latest-rocm-gfx110x` | `latest-rocm-gfx110x` | nothing extra |
| AMD Ryzen AI Max, Strix Halo (Radeon 8060S) | `latest-rocm-gfx1151` | `latest-rocm-gfx1151` | nothing extra |

Not sure which NVIDIA image? Run `nvidia-smi` on the Unraid console: driver 580 or newer and an RTX card means
Default, anything else `latest-cuda12`. Intel Arc is not supported.

Tested on real cards: Default (RTX 3090 + RTX 5060 Ti) and `latest-rocm-gfx110x` (RX 7900 XTX). The others
are built the same way but nobody has run them on a card yet; please report how they work.

The choice also sets **Extra Parameters** (see step 5). To change the image later, edit the container and
change the tag at the end of **Repository**, and set Extra Parameters to match.

### Install

1. NVIDIA only: install the **Nvidia-Driver** plugin and reboot if it asks you to. AMD cards need nothing
   extra; the driver is part of Unraid.
2. Install **strata-supervisor** from Community Applications and pick your image (or add the template
   `unraid/strata-supervisor.xml` by hand; then set the tag in **Repository** yourself).
3. Check the paths before you press Apply:
   - **Strata data** (`/data`): the models live here, 60 to 190 GB each, plus prepared data. Put it on a fast
     disk (an SSD pool) with plenty of room.
   - **Model configs** (`/config`): small TOML files, one per model config.
   - **Own GGUF files** (`/models`): optional, see section 5.
4. Set **Allowed host names** to the server's IP and name, e.g. `192.168.1.10,tower`. The web UI has no login.
   Without this setting a malicious web page you open could reach the UI through a trick called DNS rebinding,
   read the API key and load or unload models. With it, the UI answers only to these names; open it with one of
   them.
5. Leave **Extra Parameters** as the image choice set them:
   - NVIDIA: `--gpus all --ulimit memlock=-1:-1 --shm-size 2g`
   - AMD: `--device /dev/kfd --device /dev/dri --group-add video --ulimit memlock=-1:-1 --shm-size 2g`
6. If another container already uses port 8080 (for example another LLM server), change the host port of
   **API port**, or stop the other container.

Other Docker hosts: see the `docker run` example in the README.

## 3. First start

1. Open `http://<server>:8081`.
2. Look at **Hardware**:
   - **Start checks** should say "all passed". A red message blocks loading until it is fixed and the container
     is restarted. Usual causes: the wrong image for the card, `--gpus all` (NVIDIA) or the `--device` and
     `--group-add video` flags (AMD) missing in Extra Parameters, the Nvidia-Driver plugin missing, or the driver too old.
   - **Graphics cards** lists your cards with the numbers Strata uses (NVIDIA: the `nvidia-smi` numbers).
   - **Engine backend** shows `cuda` (NVIDIA) or `hip` (AMD).
3. Note the **API key**: the "API key" button in the top bar shows it. Without `API_KEY` in the container
   settings, one is made on the first start and kept in `/data/state/api-key`. It stays the same for every
   model and every restart.

## 4. A model from Strata's list (download)

The normal way: Strata downloads the files itself.

1. **Model configs → New config** (or the `+` next to "Model configs" in the sidebar).
2. In **Model**, tab **Prepared Strata model**, pick a model from the list. It shows the download size,
   `ON DISK` for models already downloaded, and a fit marker for your cards and RAM:
   green "fits", yellow "SSD reads" (only with a RAM budget or "From the model files"), red "too big".
   The choice fills in Family, Version and a name proposal.
3. **Name:** a short lowercase name, e.g. `iq3s-128k`. It names the config file and Strata's preset. You can
   keep several configs for the same model, e.g. `q2-32k` and `q2-128k`.
4. Set the other sections (see section 6) and press **Save**. **Plan** asks Strata what the current form
   would do, without saving.
5. On the config's **Overview** press **Check download**. Strata answers without changing anything: how much is
   still to download (model, draft layer, picture reader) and warnings such as "tight fit" or "more than the
   model was trained on". Make sure the disk has that much room.
6. Press **Load**. The state reads **loading (downloading)** while files come in, then **loading** while Strata
   checks, prepares and loads. The first time takes long; later loads are quick.
7. When the state is **ready**, the model answers on the API port.

**Interrupting a download is fine.** Press **Cancel** while it loads. The next load continues where it stopped.

Downloaded files go to `/data/Strata-data/models/` (models), `/data/Strata-data/mtp/` (draft layers) and
`/data/Strata-data/packs/` (prepared data).

## 5. GGUF files you already have

Strata runs only its own model families (see Strata's `docs/MODELS.md`); an arbitrary GGUF does not work.
If you already have the files of such a model, Strata can use them instead of downloading.

1. Put each model into its own folder on the host, with the shards directly in it:
   ```
   /mnt/user/gguf/IQ3_S/
     Qwen...-IQ3_S-00001-of-00002.gguf
     Qwen...-IQ3_S-00002-of-00002.gguf
   ```
2. Set **Own GGUF files** in the container settings to the parent folder, here `/mnt/user/gguf`. It is mounted
   at `/models`.
3. **New config** → tab **GGUF file** → pick the folder. The UI reads the file header and proposes Family and
   Version; check them. Folders that cannot be used are greyed out with the reason.
4. **Check download** should say that the model itself is not downloaded. Strata still writes prepared data
   (tens of GB) and, if missing, the draft layer (about 5 GB) into `/data`.
5. Load as usual.

Model folders that Strata downloaded elsewhere (desktop app, another server) can also be copied into
`/data/Strata-data/models/` with their original names; Strata finds them there. This way is not tested with
this web UI yet.

## 6. The settings explained

| Field | What it does |
| --- | --- |
| Context | How much text the model keeps at once: `32k`, `64k`, `128k`, ... The hint below shows the maximum the model was trained on. More context needs more VRAM on **every** card. Above the trained length the field turns yellow: Strata allows it but answers may get less reliable. |
| KV format | How the conversation memory is stored. `int8` is the default. `k8v4` needs 23 % less, `q4_0` about half (slightly worse on long documents), `fp16` twice as much. For long contexts try `k8v4` first. |
| Pictures (vision) | `off` saves memory. `on` lets the model read pictures. `gpu` or `cpu` decides where the picture reader runs. |
| Draft layer (MTP) | Makes answers faster. `off` saves about 1 GB VRAM. |
| Parallel requests | How many requests are answered at the same time. `0` lets Strata fit it to the cards. |
| Cards | A role per card: **main**, **helper** or **off**; exactly one card is main. "Only 0" uses only card 0, "Split over all cards" uses all. Splitting adds the second card's VRAM, so more experts fit on the cards. A slow second card can also slow things down, so compare. |
| VRAM reserve | VRAM left free for other programs. "First card" is the first card **in Strata's order** (helpers first, main last); the labels name the card. Empty means Strata's default. |
| RAM mode | How Strata keeps the experts in RAM, see below. |
| Advanced | Extra engine settings, one `KEY=VALUE` per line, passed as `--set`. Settings the form sets itself (port, cards, RAM mode, VRAM reserve, ...) are refused here. |
| Notes | Free text for you. |

### RAM modes

| Mode | RAM need | When to use |
| --- | --- | --- |
| Normal | the most: all experts, also those on the cards | the model's experts plus about 10 GB fit into RAM easily |
| Only what no card holds | much less: only experts that are not on a card | the usual choice when RAM is tight; no reads from the SSD |
| RAM budget | at most N GiB, the rest is read from the SSD when needed | when even the previous mode does not fit; can be slower |
| From the model files | no fixed amount; the system frees it when other programs need RAM | the least fixed RAM; can be slower |

The budget can be at most the RAM plus swap the container may use; the form shows both. The GB estimates next
to the modes are rough.

If you limit the container's RAM (Extra Parameters `--memory 25g --memory-swap -1`, for example), the UI takes
the limit into account.

## 7. Loading, unloading, switching

- **Load** (▶): starts the model. Only possible while no other model is loaded.
- **Unload** (■): stops the model and frees VRAM and RAM, usually within seconds. While loading, the same
  button is **Cancel**.
- **Switching** from A to B: unload A, wait until it shows idle, then load B. The container is not restarted.
- States: **idle**, **loading** / **loading (downloading)**, **ready** (answers on the API port),
  **unloading**. **failed** or **stale** means Strata reports a problem; the config page and the logs say why.
  Unload it to clear it before loading another model.
- Changing the settings of the loaded model is allowed. The change applies at its next load.
- A container stop or restart unloads the model. After a start, nothing is loaded.

## 8. Using the model from other programs

- OpenAI-compatible apps: base URL `http://<server>:8080/v1`, API key from the "API key" button. The model
  name comes from `GET /v1/models`.
- Anthropic-compatible apps (for example Claude Code): base URL `http://<server>:8080`, same key.
- While no model is loaded, every request gets `503` with "No model loaded in Strata.", and `GET /v1/models`
  lists no models. While a model loads, the port may not answer at all for a moment.

## 9. Logs

- **A config's Logs tab:** the output of its last load in this container run, and its engine log. The engine
  log is kept on disk, so it is shown even when the model is not loaded ("from the last run").
- **Logs page:** the last load and the engine log of the loaded model side by side, plus the web UI's own log
  ("Supervisor").
- The API key is hidden in all logs.

## 10. Editing configs by hand

Every config is a file `/config/models/<name>.toml` (on Unraid by default
`/mnt/user/appdata/strata-supervisor/models/`). Edit it on the host and reload the page. A file with a mistake
is listed with the error and cannot be loaded until it is fixed. A new file with a valid lowercase name appears
as a new config.

```toml
model = "IQ3_S"
family = "qwen"
model_path = "/models/IQ3_S"     # empty for a prepared Strata model
context = "128k"
kv = "k8v4"
vision = "off"
gpu_roles = "0=main,1=helper"
ram_mode = "resident-experts"    # normal | resident-experts | budget | mmap-experts
ram_budget_gib = 0
vram_reserve_mib = 1000          # optional
extra_set = []
notes = ""
```

## 11. Removing configs and models

- The trash can next to a config in the sidebar, or **Delete** in its Settings, removes only the config file.
  The model files stay in `/data`.
- The **Models** page lists Strata's downloads and your own GGUF folders with their size and the configs that
  use them. The trash can deletes a model folder after a confirmation (a Strata model together with its
  prepared data; the shared draft layer stays). Nothing can be deleted while a model is loaded.

## 12. Stopping the container

`docker stop`, the Unraid UI and a shutdown give the container 10 seconds by default (Unraid's "Docker Stop
Timeout"). The UI stops the model within 8 seconds; in tests it took about 4 seconds with a loaded model.

## 13. When something goes wrong

| What you see | What to do |
| --- | --- |
| Red start-check message on every page | Read it: it names the missing flag or driver. Fix the container settings and restart. |
| "nvidia-smi is not in the container" | Add `--gpus all` (Extra Parameters) and install the Nvidia-Driver plugin. |
| "This is an AMD card, but /dev/kfd is not in the container" | Add `--device /dev/kfd --device /dev/dri --group-add video` to Extra Parameters. |
| "The engine in this image cannot run on this computer" | The image does not match the card or driver; see "Pick your image" in section 2. |
| "Port 8080 is busy" | Another container uses the API port. Stop it or map another host port. |
| Load stays at "Checking that the model files are complete" | Normal for a few minutes after a download. |
| Unload did not finish | The banner names the state. Press Unload again. If it stays, restart the container. |
| A model answers slowly | Compare "Only 0" with the split, and a RAM mode without SSD reads. The Overview shows how many experts are on the cards. |
| The page says "web UI not reachable" | The browser lost the connection to the UI. If the container runs, open the browser console for the error and report it. |
