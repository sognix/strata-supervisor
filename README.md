# Strata Supervisor

A web UI for [Strata](https://github.com/Niko1221/Strata), the local LLM server for large
mixture-of-experts models (Qwen, DeepSeek) on consumer GPUs. It is built `FROM` the official Strata
image: one container, no Docker socket, no Strata code changed. It drops into Unraid or any other
Docker host with an NVIDIA or AMD GPU.

Strata's own Docker image loads one model, fixed by environment variables, at container start. This
project keeps the container running without a model and lets you manage several model configs and
load or unload them by hand in the browser.

*Unofficial private project, not affiliated with, endorsed by, or supported by Niko1221 or the Strata
project.*

## Features

- **Several model configs**, one TOML file each, editable in the UI or on the host. Several configs
  of the same model are fine (e.g. different contexts).
- **Load and unload only by hand.** Nothing loads at container start, nothing loads or swaps by
  itself. Switching models needs no container restart.
- **Strata's catalog in the form**: models grouped like in Strata's desktop app, with download size,
  `ON DISK` and a fit marker for your cards and RAM. Or pick a folder of your own GGUF files.
- **Multi-GPU through roles** (main, helper, off), passed as `--gpu-roles`. Card numbers come from
  `nvidia-smi`, never from the code.
- **RAM mode per model**: all experts in RAM, only what no card holds, a RAM budget, or straight
  from the model files. The budget respects the container's RAM limit.
- **Plan and Check download** ask Strata what a load would need, without changing anything.
- **Models page**: Strata's downloads and your own GGUF folders with size and the configs using
  them; delete with confirmation.
- **Logs** of the launch, the engine and the supervisor, with the API key hidden.
- While no model runs, the API port answers `503` with "No model loaded in Strata." and
  `GET /v1/models` lists nothing.

## Requirements

- A graphics card Strata supports, with the matching image (see "Image tags" below):
  - NVIDIA: driver 580 or newer (575 or newer for `cuda12`); on Unraid the Nvidia-Driver plugin, elsewhere
    the NVIDIA Container Toolkit (`--gpus all`).
  - AMD RX 7000, RX 9000 or Strix Halo: the `amdgpu` kernel driver (built into Unraid), the devices
    `/dev/kfd` and `/dev/dri` in the container, and `--group-add video`.
- Disk space: 60 to 190 GB per model, on a fast disk.
- RAM: depends on the model and RAM mode; see Strata's `docs/MODELS.md`.
- amd64 only, like Strata's images.

## Quick start

### Unraid

Install **strata-supervisor** from Community Applications, or add
[`unraid/strata-supervisor.xml`](unraid/strata-supervisor.xml) as a template. Then follow the
[user guide](docs/USER-GUIDE.md).

### Docker

```sh
docker run -d --name strata-supervisor --gpus all \
  -p 8081:8081 -p 8080:8080 \
  --ulimit memlock=-1:-1 --shm-size 2g \
  -e ALLOWED_HOSTS=192.168.1.10,myserver \
  -v /srv/strata/data:/data \
  -v /srv/strata/config:/config \
  ghcr.io/sognix/strata-supervisor:latest
```

AMD (here an RX 7000):

```sh
docker run -d --name strata-supervisor \
  --device /dev/kfd --device /dev/dri --group-add video \
  -p 8081:8081 -p 8080:8080 \
  --ulimit memlock=-1:-1 --shm-size 2g \
  -e ALLOWED_HOSTS=192.168.1.10,myserver \
  -v /srv/strata/data:/data \
  -v /srv/strata/config:/config \
  ghcr.io/sognix/strata-supervisor:latest-rocm-gfx110x
```

Open `http://<server>:8081`. Nothing is loaded at start.

### Image tags

`ghcr.io/sognix/strata-supervisor`, one image per Strata flavor:

| Tag | Cards |
| --- | --- |
| `latest` (= `latest-cuda13`) | NVIDIA RTX 20 to 50, driver 580 or newer |
| `latest-cuda12` | older NVIDIA (GTX 10, P40, P100, V100) or driver 575 to 579 |
| `latest-rocm-gfx120x` | AMD RX 9000, Radeon AI PRO R9700 |
| `latest-rocm-gfx110x` | AMD RX 7900, 7800, 7700, 7600 |
| `latest-rocm-gfx1151` | AMD Ryzen AI Max (Strix Halo) |

Each release also has fixed tags such as `1.0.0` / `1.0.0-rocm-gfx110x` and `1.0` / `1.0-rocm-gfx110x`. `dev`
and `dev-<flavor>` follow the main branch and are for testing.

## Configuration

| Variable | Default | Meaning |
| --- | --- | --- |
| `UI_PORT` | `8081` | Web UI port inside the container (change only with host networking) |
| `PORT` | `8080` | API port inside the container |
| `API_KEY` / `API_KEY_FILE` | empty | Inference key, 16 characters or more. Empty: one is made once and kept in `/data/state/api-key` |
| `ALLOWED_HOSTS` | empty | Host names or IPs the UI answers to, comma separated. Recommended, see [Security](#security) |
| `PUID` / `PGID` | `99` / `100` | Owner of the files written to `/config` |

| Path | Content |
| --- | --- |
| `/data` | Strata's models, prepared data, state and the API key. Needs lots of room |
| `/config` | One TOML file per model config in `models/` |
| `/models` | Optional: your own GGUF files, one sub-folder per model. Writable if the Models page should be able to delete there |

`docker stop` must finish within Unraid's "Docker Stop Timeout" (10 s by default). The supervisor stops
a loaded model within 8 s.

### Model configs

One flat TOML file per config in `/config/models/<name>.toml`. These files decide: every load passes
`--save-preset <name>`, never `--preset`, so nothing old from Strata's presets comes back.

```toml
model = "Q2_0"
family = "qwen"
context = "64k"
kv = "int8"
vision = "off"
gpu_roles = "0=main,1=helper"
ram_mode = "budget"          # normal | resident-experts | budget | mmap-experts
ram_budget_gib = 28
vram_reserve_mib = 512       # optional, first card in Strata's order (the helper when split)
extra_set = ["prefill=65536"]
```

Changes to the loaded model apply at its next load.

## Security

**The web UI has no login.** Anyone who can reach port 8081 can load and unload models, delete model
files and read the API key. Run it only on a trusted network and don't expose it to the internet.
Set `ALLOWED_HOSTS` to the names you open the UI with: the UI then refuses other host names, which
stops web pages on other sites from reaching it through DNS rebinding. Changes are accepted only as
JSON `POST` requests whose `Origin` matches the host.

The inference API on port 8080 is Strata's own and needs the API key.

To report a vulnerability, see [`SECURITY.md`](SECURITY.md).

## Known limitations

- One image per Strata flavor is published: `cuda13`, `cuda12`, `rocm-gfx120x`, `rocm-gfx110x` and
  `rocm-gfx1151`. Tested on real cards: only `cuda13` (RTX 3090 + RTX 5060 Ti) and `rocm-gfx110x`
  (one RX 7900 XTX). Intel Arc (`sycl`) is not built.
- The UI depends on some internals of Strata (catalog, log lines, folder layout). A new Strata
  version may need an update of this project; the Strata version is pinned in the `Dockerfile`
  (`STRATA_VERSION`).
- Only one model is loaded at a time. Strata's "one model per card" mode is not supported.
- Tuning (`--tune`) is not offered.

## Documentation

- [`docs/USER-GUIDE.md`](docs/USER-GUIDE.md) - installing, getting models, settings, troubleshooting
- [`CONTRIBUTING.md`](CONTRIBUTING.md) - building, testing and running it yourself
- [`AI/IMPLEMENTATION.md`](AI/IMPLEMENTATION.md) - background, decisions and test results (written
  as working notes for AI coding agents, but readable for humans too)
- [`CHANGELOG.md`](CHANGELOG.md) - what changed in each release

## Support

This is a private hobby project, maintained in spare time. Bug reports with the details the issue
template asks for are welcome and get looked at; feature requests too, but without any promise.
For problems with Strata itself (model quality, speed, engine crashes), please check the
[Strata repository](https://github.com/Niko1221/Strata) first.

## License

Copyright (c) 2026 sognix, MIT License, see [`LICENSE`](LICENSE).

The image is built on the official Strata image (MIT) and contains everything in it; see
[`NOTICE.md`](NOTICE.md) for Strata's license and the third-party components.
