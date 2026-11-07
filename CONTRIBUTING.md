# Contributing

Thanks for wanting to help! Bug reports, fixes and small improvements are welcome. For bigger
changes, please open an issue first so we can agree on the approach before you put work into it.

## How the project is laid out

- `app/backend/` - the supervisor (Python 3.12 standard library only, no pip packages):
  - `strata_webui/host/` - start checks, card query (`nvidia-smi` or the AMD KFD topology), RAM limits
  - `strata_webui/strata/` - driving `strata launch`, model configs, catalog, API port placeholder,
    GGUF headers, model files, engine log
  - `strata_webui/web/` - JSON API (`api.py`) and HTTP server (`server.py`)
  - `strata_webui/core/` - command runner, caches, log buffer
- `app/frontend/` - plain HTML/CSS/JavaScript, no build step, no frameworks.
- `tests/` - unit tests with a fake `strata` CLI (`tests/fake_strata.py`).
- `Dockerfile` - the image; `.github/workflows/build.yml` tests, builds and publishes it.
- `unraid/` - Unraid template and icon; `ca_profile.xml` is the Community Applications profile.
- `AI/` - background, decisions, test results and the UI design. Section numbers there are referenced
  from code comments; keep them stable.

## Tests and a run without Docker

```sh
python3 -m unittest discover -s tests          # unit tests with the fake strata CLI
python3 tests/dev_run.py /tmp/strata-dev       # whole supervisor without Docker: UI on :18081, API on :18080
```

`dev_run.py` uses the fake CLI, so the UI works without a GPU and without downloads.

## Building and running the image

```sh
docker build -t strata-supervisor:dev .                                                # NVIDIA (cuda13)
docker build --build-arg STRATA_TAG=0.2.9-rocm-gfx110x -t strata-supervisor:rocm .     # AMD RX 7000
```

NVIDIA:

```sh
docker run -d --name strata-dev --gpus all \
  -p 8081:8081 -p 8080:8080 --ulimit memlock=-1:-1 --shm-size 2g \
  -v "$PWD/local-mounts/data:/data" -v "$PWD/local-mounts/config:/config" \
  strata-supervisor:dev
```

AMD RX 7000 (HIP):

```sh
mkdir -p local-mounts/config
docker run -d --name strata-dev \
  --device /dev/kfd --device /dev/dri \
  --group-add $(getent group video | cut -d: -f3) --group-add $(getent group render | cut -d: -f3) \
  -p 8081:8081 -p 8080:8080 --ulimit memlock=-1:-1 --shm-size 2g \
  -e PUID=$(id -u) -e PGID=$(id -g) \
  -v "$PWD/local-mounts/data:/data" -v "$PWD/local-mounts/config:/config" \
  strata-supervisor:rocm
```

`local-mounts/` is in `.gitignore`. Create `local-mounts/config` before the first start, otherwise
Docker creates it as root. `/data` needs room for the models (60 GB and more each); files there belong
to root. Without a GPU the container still starts: the start checks report the missing card and
loading stays blocked.

## Code style

- Match the surrounding code: naming, comment density, no new dependencies (the backend uses only
  Python's standard library, the frontend has none).
- Start processes with argument lists only, never through a shell.
- Keep the UI usable in light and dark mode.

## Pull requests

- One topic per pull request, with a short description of what and why.
- The GitHub workflow runs the unit tests, builds the image and runs a smoke test on every pull
  request; it has to pass.
- If you changed something visible, a screenshot in the PR helps a lot.
- Say which GPU(s) and Strata base tag you tested with; CI has no GPU.

## License of contributions

This project is licensed under the MIT License. By submitting a pull request you agree that your
contribution is licensed under the same terms.
